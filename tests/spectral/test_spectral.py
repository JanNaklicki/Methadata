import itertools
import subprocess
import sys

import pytest

pytest.importorskip("sklearn")

from methadata.clustering.spectral import (build_graph, build_result, compare_with_leiden, explain_pair,  # noqa: E402
                                           get_adapters, resolve_config, sweep)
from methadata.contracts import (Artifact, ArtifactRef, ArtifactRole, AssignmentStatus, ClusterRun,  # noqa: E402
                                 InputKind, Observation, PairEvidence, Relation, discover, ids)

CFG = {"similarity_version": "asset-jaccard-v1", "dataset_version": "synthetic-1", "repeats": 5,
       "popularity_alpha": 0.0}
METHOD = "asset-jaccard-v1"


def obs(name):
    url = f"https://{name}.example.invalid/a"
    oid = ids.observation_id(f"synthetic://{name}", "url_only")
    sha = ids.sha256_bytes(name.encode())
    art = Artifact(artifact_id=ids.artifact_id(oid, "input_url", sha), observation_id=oid, role=ArtifactRole.input_url,
                   sha256=sha, url=url)
    return Observation(observation_id=oid, canonical_id=ids.canonical_id([], url), source_ref=f"synthetic://{name}",
                       input_kind=InputKind.url_only, artifacts=[art], tags=["synthetic"])


def pair(a, b, score):
    ca, cb = sorted((a.canonical_id, b.canonical_id))
    refs = {}
    if score is not None:
        for o in (a, b):
            refs[o.canonical_id] = [ArtifactRef(artifact_id=o.artifacts[0].artifact_id)]
    return PairEvidence(run_id="r", pair=(ca, cb), method_id=METHOD, relation=Relation.shared_template, score=score,
                        evidence_refs=refs, explanation="test")


def matrix(o, table, default=0.0):
    return [pair(o[x], o[y], table.get(x + y, default)) for x, y in itertools.combinations(sorted(o), 2)]


def groups(run):
    g = {}
    for a in run.assignments:
        for c in a.cluster_ids:
            g.setdefault(c, set()).add(a.material_id)
    return {frozenset(v) for v in g.values()}


def named(o, *names):
    return frozenset(o[n].canonical_id for n in names)


TWO = {"AB": .9, "AC": .8, "BC": .9, "DE": .9, "DF": .8, "EF": .9, "CD": .4}  # dwie trójki + słaby most C-D


def setup(names, table):
    o = {n: obs(n) for n in names}
    return o, list(o.values()), matrix(o, table)


def test_two_cliques_split_and_run_valid():
    o, ob, pe = setup("ABCDEF", TWO)
    res = build_result(ob, pe, {**CFG, "neighbors": 2, "n_clusters": 2}, seed=1)
    assert groups(res.run) == {named(o, *"ABC"), named(o, *"DEF")}
    ClusterRun.model_validate(res.run.model_dump())
    assert res.run.method_id == "spectral" and res.run.seed == 1 and res.run.config["seeds"][0] == 1
    assert res.run.config["similarity_version"] == "asset-jaccard-v1" and "dataset_version" not in res.run.config
    assert res.agreement["mean_ari"] == pytest.approx(1.0)
    for c in res.clusters:
        assert all(e.pair_evidence_id for e in c.edges) and c.weakest is not None
    # krawędź mostu C-D odrzucona przez regułę mutual-kNN
    assert any({e.source, e.target} == {o["C"].canonical_id, o["D"].canonical_id} for e in res.dropped_edges)


def test_auto_number_of_groups_by_eigengap():
    o, ob, pe = setup("ABCDEF", {**TWO, "CD": .3})
    res = build_result(ob, pe, {**CFG, "neighbors": 3, "n_clusters": "auto"})
    assert groups(res.run) == {named(o, *"ABC"), named(o, *"DEF")}
    assert res.components[0].chosen_by == "auto" and res.components[0].n_clusters == 2
    assert len(res.components[0].eigenvalues) >= 3


def test_elongated_chains_joined_by_weak_bridge():
    table = {"AB": .9, "BC": .9, "CD": .9, "DE": .9, "EF": .2, "FG": .9, "GH": .9, "HI": .9, "IJ": .9}
    o, ob, pe = setup("ABCDEFGHIJ", table)
    res = build_result(ob, pe, {**CFG, "neighbors": 2, "n_clusters": 2})
    assert groups(res.run) == {named(o, *"ABCDE"), named(o, *"FGHIJ")}


def test_disconnected_components_are_separate_groups():
    table = {"AB": .9, "AC": .9, "BC": .9, "DE": .8, "DF": .8, "EF": .8}
    o, ob, pe = setup("ABCDEF", table)
    res = build_result(ob, pe, {**CFG, "neighbors": 3, "n_clusters": 1})
    assert len(res.components) == 2
    assert groups(res.run) == {named(o, *"ABC"), named(o, *"DEF")}
    assert any("składowych rozłącznych" in n for n in res.notes)


def test_too_few_materials_and_no_scores():
    o, ob, pe = setup("AB", {"AB": .9})
    res = build_result(ob, pe, CFG)
    assert groups(res.run) == {named(o, "A", "B")}          # dwa połączone: jedna grupa, bez dzielenia
    o, ob, pe = setup("A", {})
    res = build_result(ob, [], CFG)
    assert [a.status for a in res.run.assignments] == [AssignmentStatus.insufficient_data]
    assert build_result([], [], CFG).run.assignments == []
    o, ob, pe = setup("ABC", {})
    pe = [pair(o["A"], o["B"], None), pair(o["A"], o["C"], None), pair(o["B"], o["C"], None)]
    res = build_result(ob, pe, CFG)
    assert {a.status for a in res.run.assignments} == {AssignmentStatus.insufficient_data}


def test_n_clusters_too_large_is_clamped():
    o, ob, pe = setup("ABC", {"AB": .9, "AC": .9, "BC": .9})
    res = build_result(ob, pe, {**CFG, "neighbors": 2, "n_clusters": 10})
    ClusterRun.model_validate(res.run.model_dump())
    assert any("przycięte" in n for n in res.notes)


def test_isolated_nodes_become_singletons_with_neighbors_effect():
    table = {**TWO, "FX": .35}  # X tylko słabo związany z F, a F ma silniejszych sąsiadów
    o, ob, pe = setup("ABCDEFX", table)
    res = build_result(ob, pe, {**CFG, "neighbors": 2, "n_clusters": 2})
    assert o["X"].canonical_id in res.isolated
    st = {a.material_id: a.status for a in res.run.assignments}
    assert st[o["X"].canonical_id] == AssignmentStatus.singleton
    big = build_result(ob, pe, {**CFG, "neighbors": 6, "n_clusters": 2})
    assert o["X"].canonical_id not in big.isolated or len(big.graph_edges) > len(res.graph_edges)
    assert len(big.graph_edges) >= len(res.graph_edges)


def test_sweep_neighbors_and_clusters():
    o, ob, pe = setup("ABCDEF", TWO)
    rows = sweep(ob, pe, CFG, neighbors=[1, 2, 5], n_clusters=["auto", 2], seeds=[0, 1])
    assert len(rows) == 6
    assert rows[0]["edges"] <= rows[-1]["edges"] and all("isolated" in r for r in rows)


def test_compare_with_leiden_same_edges():
    pytest.importorskip("leidenalg")
    o, ob, pe = setup("ABCDEF", TWO)
    res = build_result(ob, pe, {**CFG, "neighbors": 2, "n_clusters": 2})
    cmp = compare_with_leiden(res, resolution=0.5)
    assert cmp["edges"] == len(res.graph_edges) and cmp["ari"] == pytest.approx(1.0) and cmp["identical"]


def test_explain_pair_and_config_validation():
    o, ob, pe = setup("ABCDEF", TWO)
    res = build_result(ob, pe, {**CFG, "neighbors": 2, "n_clusters": 2})
    ex = explain_pair(res, o["C"].canonical_id, o["D"].canonical_id)
    assert not ex["same_cluster"] and ex["dropped_by_knn"] is not None and ex["direct_edge"] is None
    with pytest.raises(ValueError, match="similarity_version"):
        resolve_config({"dataset_version": "x"})
    with pytest.raises(ValueError):
        resolve_config({**CFG, "neighbors": 0})
    with pytest.raises(ValueError):
        resolve_config({**CFG, "n_clusters": 0})
    with pytest.raises(ValueError):
        resolve_config({**CFG, "neighbor_mode": "x"})


def test_discover_and_adapter():
    assert "spectral-v1" in discover()
    o, ob, pe = setup("ABCDEF", TWO)
    run = get_adapters()[0].cluster(ob, pe, {**CFG, "neighbors": 2, "n_clusters": 2})
    assert isinstance(run, ClusterRun)


def test_discover_without_sklearn():
    code = ("import sys\nsys.modules['sklearn']=None\nsys.modules['numpy']=None\n"
            "from methadata.contracts import discover\nassert 'spectral-v1' in discover()\n"
            "from methadata.clustering.spectral import build_result\n"
            "print(build_result([], [], {'similarity_version':'v','dataset_version':'d'}).run.assignments)\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
