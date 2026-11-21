import json
import random
import sys
from pathlib import Path

import pytest

from methadata.clustering.bicluster import (build_result, compare_with_e14, get_adapters, load_export,
                                            load_from_store, resolve_config)
from methadata.clustering.bicluster.__main__ import main as cli_main
from methadata.contracts import ArtifactRole, discover, ids
from methadata.devtools.realdata import BACKUP
from methadata.experiments.rare_motifs import MotifConfig, TraceConfig, TraceMatrix, mine_motifs, store_export

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments_rare_motifs"))
from test_rare_motifs import corpus  # noqa: E402

CFG = {"feature_version": "rare-motifs-traces-v1", "dataset_version": "synthetic-1"}


@pytest.fixture(scope="module")
def e14():
    obs, fm = corpus()
    m = TraceMatrix.build(obs, fm, TraceConfig(), dataset_version="synthetic-1")
    return obs, m, m.export(min_df=2)


def by_mats(res, *mats):
    return next(b for b in res.biclusters if b.materials == tuple(mats))


def test_pair_materials_features_support_coverage(e14):
    _, m, ex = e14
    res = build_result(ex, CFG)
    b = by_mats(res, "a", "b", "c")
    assert {"resource", "form", "text"} <= set(b.kinds)
    assert b.support == 3 and b.coverage == pytest.approx(3 / m.n)
    mats, traces = res.pair(b.cluster_id)
    assert mats == ["a", "b", "c"] and set(traces) == set(b.traces)
    # pełna podmacierz: każdy materiał ma każdy ślad
    assert all(set(b.traces) <= set(m.rows[x]) for x in b.materials)
    # domknięcie: ślady = wszystkie wspólne (z df>=2), materiały = wszystkie mające ślady
    assert set(b.traces) == set.intersection(*[set(m.rows[x]) for x in b.materials]) & set(ex["matrix"]["columns"])
    assert m.joint_set(b.traces) == frozenset(b.materials)
    assert b.weight == pytest.approx(sum(m.idf(t) for t in b.traces))


def test_evidence_points_to_source_ids(e14):
    _, _, ex = e14
    b = by_mats(build_result(ex, CFG), "a", "b", "c")
    assert len(b.evidence) == len(b.materials) * len(b.traces)
    assert all(e.observation_id and e.feature_ids and e.evidence_refs for e in b.evidence)


def test_clusterrun_assignments_overlap_and_statuses(e14):
    obs, _, ex = e14
    res = build_result(ex, {**CFG, "redundancy_jaccard": 1.0}, observations=obs)
    run = res.run
    assert run.method_id == "bicluster" and run.dataset_version == "synthetic-1"
    assert run.config["feature_version"] == CFG["feature_version"]
    st = {a.material_id: a for a in run.assignments}
    assert st["a"].status.value == "grouped" and len(st["a"].cluster_ids) >= 2   # {a,b,c} i nadgrupa z d/e
    assert st["n00"].status.value in ("singleton", "insufficient_data")
    assert set(st) == {o.canonical_id for o in obs}
    assert res.coverage == pytest.approx(len(res.covered) / 17)


def test_order_independence_and_determinism(e14):
    obs, m, ex = e14
    r1 = build_result(ex, CFG)
    ex2 = json.loads(json.dumps(ex))
    random.Random(1).shuffle(ex2["graph"]["edges"])
    r2 = build_result(ex2, CFG)
    assert r1.run.model_dump() == r2.run.model_dump()
    assert [b.cluster_id for b in r1.biclusters] == [b.cluster_id for b in r2.biclusters]
    assert build_result(m, CFG).run.run_id == r1.run.run_id   # TraceMatrix = eksport


def test_ubiquitous_traces_excluded_and_thresholds(e14):
    _, m, ex = e14
    none = build_result(ex, {**CFG, "min_materials": 10})
    assert none.biclusters == () and all(a.status.value != "grouped" for a in none.run.assignments)
    wide = build_result(ex, {**CFG, "min_traces": 4})
    assert all(len(b.traces) >= 4 for b in wide.biclusters)
    tight = build_result(ex, {**CFG, "max_df_fraction": 0.2})   # df <= 3
    assert all(max(b.component_df.values()) <= 3 for b in tight.biclusters)


def test_requires_feature_version(e14):
    _, _, ex = e14
    with pytest.raises(ValueError, match="feature_version"):
        build_result(ex, {"dataset_version": "x"})
    with pytest.raises(ValueError):
        resolve_config({**CFG, "min_materials": 0})
    with pytest.raises(ValueError, match="E14"):
        build_result({"foo": 1}, CFG)


def test_truncation_flag(e14):
    _, _, ex = e14
    assert build_result(ex, {**CFG, "max_nodes": 1}).truncated is True
    assert build_result(ex, CFG).truncated is False


def test_compare_with_e14(e14):
    _, m, ex = e14
    motifs = mine_motifs(m, MotifConfig(rare_fraction=0.3))
    res = build_result(ex, {**CFG, "redundancy_jaccard": 1.0})
    cmp = compare_with_e14(res, motifs)
    assert cmp["motifs"] == len(motifs) and cmp["matched"] == cmp["motifs"]
    assert cmp["exact"] == cmp["traces_covered"] == cmp["motifs"]   # motyw E14 jest domknięty w biklastrze
    top = next(r for r in cmp["rows"] if r["motif_materials"] == ["a", "b", "c"])
    assert top["exact"] and top["jaccard"] == 1.0


def test_adapter_discovered_and_runs(e14):
    obs, _, ex = e14
    assert [a for a in discover().values() if a.spec.id == "bicluster-v1"]
    ad = get_adapters()[0]
    run = ad.cluster(obs, [], CFG, optional={"trace_matrix": ex})
    assert run.assignments and run.method_id == "bicluster"
    empty = ad.cluster(obs, [], CFG)   # bez macierzy E14 (np. w runnerze): brak danych, nie błąd
    assert {a.status.value for a in empty.assignments} == {"insufficient_data"}
    with pytest.raises(ValueError, match="spoza obserwacji"):
        ad.cluster(obs[:3], [], CFG, optional={"trace_matrix": ex})


def test_store_roundtrip_and_cli(tmp_path, e14):
    _, _, ex = e14

    class S:
        def __init__(self):
            self.d = {}

        def put_derived(self, *, observation_id, role, data, **kw):
            assert role is ArtifactRole.graph_edges
            self.d[(kw["parent"], kw["origin"])] = data

        def read_derived(self, parent_id, origin):
            return self.d.get((parent_id, origin))

    s = S()
    store_export(ex, s, observation_id="o", parent="p1")
    assert load_from_store(s, "p1") == ex
    with pytest.raises(ValueError):
        load_from_store(s, "nope")
    (tmp_path / "e.json").write_text(json.dumps(ex), "utf-8")
    (tmp_path / "c.json").write_text(json.dumps(CFG), "utf-8")
    assert cli_main(["--export", str(tmp_path / "e.json"), "--config", str(tmp_path / "c.json"),
                     "--out", str(tmp_path / "o.json")]) == 0
    out = json.loads((tmp_path / "o.json").read_text("utf-8"))
    assert out["biclusters"] and out["biclusters"][0]["evidence"]
    assert load_export(json.dumps(ex).encode()) == ex


@pytest.mark.skipif(not BACKUP.is_dir(), reason="brak katalogu BACKUP")
def test_real_data_vs_e14():
    from methadata.experiments.rare_motifs.report import measure
    r = measure(BACKUP, motif_cfg=MotifConfig(rare_fraction=0.25, min_kinds=1))
    m = r["matrix"]
    res = build_result(m.export(min_df=2), {**CFG, "dataset_version": "BACKUP", "redundancy_jaccard": 1.0,
                                            "max_df_fraction": 0.25, "min_traces": 1})
    assert 0 <= res.coverage <= 1 and res.n_materials == m.n
    cmp = compare_with_e14(res, r["motifs"])
    assert cmp["matched"] == cmp["motifs"]
    for b in res.biclusters:
        assert m.joint_set(b.traces) == frozenset(b.materials)
