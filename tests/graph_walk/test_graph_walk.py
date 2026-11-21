import math

import pytest

pytest.importorskip("igraph")

from methadata.clustering.graph_walk import (build_graph, build_result, explain_pair, find_paths, get_adapters,  # noqa: E402
                                             popular_traces, rank_neighbours, rerun_without_popular, resolve_config,
                                             walk_scores)
from methadata.contracts import (Artifact, ArtifactRole, AssignmentStatus, InputKind, Observation, ids)  # noqa: E402
from methadata.contracts.registry import discover  # noqa: E402

CFG = {"similarity_version": "rare-motifs-matrix-v1", "dataset_version": "synthetic-1", "repeats": 4}
HUB = "tr-hub"


def obs(name):
    oid = ids.observation_id(f"synthetic://{name}", "url_only")
    sha = ids.sha256_bytes(name.encode())
    art = Artifact(artifact_id=ids.artifact_id(oid, "input_url", sha), observation_id=oid, role=ArtifactRole.input_url,
                   sha256=sha, url=f"https://{name}.example.invalid/")
    return Observation(observation_id=oid, canonical_id=name, source_ref=f"synthetic://{name}",
                       input_kind=InputKind.url_only, artifacts=[art])


def export(links, n_extra=0):
    """links: {trace: [materials]}; ślad HUB dołączony do każdego materiału (popularny)."""
    mats = sorted({m for v in links.values() for m in v})
    allm = mats + [f"z{i}" for i in range(n_extra)]
    links = {**links, HUB: allm}
    n = len(allm)
    traces = [{"trace_id": t, "kind": "resource", "label": t, "df": len(v), "idf": math.log(n / len(v))}
              for t, v in sorted(links.items())]
    edges = [{"material": m, "trace": t, "observation_id": f"obs-{m}", "feature_ids": [f"f-{m}-{t}"],
              "evidence_refs": []} for t, v in sorted(links.items()) for m in v]
    return {"schema": "methadata.rare_motifs.matrix/1", "dataset_version": "d", "n_materials": n,
            "traces": traces, "graph": {"nodes": [{"id": m, "part": "material"} for m in allm], "edges": edges}}


# dwie grupy: A-B-C łańcuch (A-C tylko pośrednio), D-E-F trójkąt; hub w każdym materiale
LINKS = {"t1": ["A", "B"], "t2": ["B", "C"], "t3": ["D", "E", "F"], "t4": ["D", "E"], "t5": ["E", "F"]}


@pytest.fixture
def setup():
    ex = export(LINKS, n_extra=10)
    o = [obs(m) for m in ["A", "B", "C", "D", "E", "F"] + [f"z{i}" for i in range(10)]]
    return o, ex


def test_config_requires_versions():
    with pytest.raises(ValueError):
        resolve_config({"dataset_version": "x"})
    with pytest.raises(ValueError):
        resolve_config({**CFG, "restart": 1.5})


def test_walk_ranking_probability_mass_and_order(setup):
    _, ex = setup
    _, g = build_graph(ex, {**CFG, "max_trace_df": 5})
    sc = walk_scores(g, "A", 0.3)
    assert sum(sc.values()) == pytest.approx(1.0)
    rk = [m for m, _ in rank_neighbours(g, "A", 0.3, 10)]
    assert rk[0] == "B" and rk.index("C") == 1       # bliższy przed pośrednim
    assert "D" not in rk                              # inna składowa po usunięciu popularnego śladu


def test_indirect_path_explained(setup):
    o, ex = setup
    cfg, g = build_graph(ex, {**CFG, "max_trace_df": 5})
    paths = find_paths(g, "A", "C")
    assert paths and all(p.kind == "indirect" for p in paths)
    assert paths[0].nodes == ("A", "t1", "B", "t2", "C")
    assert [e.observation_id for e in paths[0].edges] == ["obs-A", "obs-B", "obs-B", "obs-C"]
    direct = find_paths(g, "D", "E")
    assert direct[0].kind == "direct" and direct[0].nodes[0] == "D"


def test_infomap_partition_separate_from_ranking(setup):
    o, ex = setup
    res = build_result(o, ex, {**CFG, "max_trace_df": 5})
    groups = {frozenset(g) for g in res.partition if len(g) > 1}
    assert frozenset("ABC") in groups and frozenset("DEF") in groups
    assert res.agreement["mean_ari"] == pytest.approx(1.0)
    st = {a.material_id: a.status for a in res.run.assignments}
    assert st["z0"] is AssignmentStatus.insufficient_data     # tylko ślad popularny (usunięty) -> brak podstaw
    assert res.run.config["similarity_version"] == CFG["similarity_version"] and res.run.dataset_version == "synthetic-1"
    assert res.ranking["A"][0].material_id == "B" and res.ranking["A"][0].path.kind == "direct"
    ex_pair = explain_pair(res, "A", "C")
    assert ex_pair["same_cluster"] and ex_pair["paths"][0].kind == "indirect"
    cl = next(c for c in res.clusters if set(c.members) == set("DEF"))
    assert cl.traces[0][0] == "t3"


def test_rerun_without_popular_nodes(setup):
    o, ex = setup
    cfg = {**CFG}      # hub nie jest tu usunięty progiem - usuwamy go jawnie
    before = build_result(o, ex, cfg)
    assert popular_traces(ex, top=1) == [HUB]
    r = rerun_without_popular(o, ex, cfg, before, "A", "C", drop_top=1)
    assert r["dropped_traces"] == [HUB]
    assert r["after"]["paths"][0].nodes == ("A", "t1", "B", "t2", "C")      # ścieżka przetrwała bez hubu
    assert r["after"]["same_cluster"] and r["partition_ari"] > 0.99
    assert HUB not in r["result"].graph.of_trace


def test_adapter_registered_and_runs(setup):
    o, ex = setup
    assert "graph-walk-v1" in discover()
    a = get_adapters()[0]
    run = a.cluster(o, [], {**CFG, "max_trace_df": 5}, optional={"graph_edges": ex})
    assert any(x.status is AssignmentStatus.grouped for x in run.assignments)
    none = a.cluster(o, [], CFG)       # bez grafu: insufficient_data, nie wyjątek
    assert {x.status for x in none.assignments} == {AssignmentStatus.insufficient_data}


def test_deterministic(setup):
    o, ex = setup
    a = build_result(o, ex, {**CFG, "max_trace_df": 5}, seed=3).run
    b = build_result(o, ex, {**CFG, "max_trace_df": 5}, seed=3).run
    assert a == b


def test_consumes_e14_export():
    import importlib.util
    from pathlib import Path as P
    spec = importlib.util.spec_from_file_location("e14_fixture", P(__file__).parents[1] / "experiments_rare_motifs" / "test_rare_motifs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    corpus = mod.corpus
    from methadata.experiments.rare_motifs import TraceMatrix
    obs, fm = corpus()
    export = TraceMatrix.build(obs, fm, dataset_version="synthetic-e14").export(min_df=1)
    res = build_result(obs, export, {**CFG, "dataset_version": "synthetic-e14", "max_trace_df": 6})
    groups = {frozenset(g) for g in res.partition if len(g) > 1}
    assert frozenset("abc") in groups
    assert explain_pair(res, "a", "b")["paths"][0].kind == "direct"


def test_real_backup_pair_path_survives_popular_removal():
    from methadata.devtools.realdata import BACKUP
    if not BACKUP.is_dir():
        pytest.skip("brak katalogu BACKUP")
    from methadata.experiments.rare_motifs.report import load, measure
    r = measure(BACKUP)
    export = r["matrix"].export()
    obs, _ = load(BACKUP, skip=("p1",))
    cfg = {**CFG, "dataset_version": r["matrix"].dataset_version}
    before = build_result(obs, export, cfg)
    assert {"p2", "p7", "p15"} in [set(g) for g in before.partition]
    rr = rerun_without_popular(obs, export, cfg, before, "p2", "p7", drop_top=3)
    assert rr["after"]["paths"] and rr["after"]["same_cluster"]
