import json
from pathlib import Path

import pytest

pytest.importorskip("numpy")

from methadata.clustering.network_fusion import (DEFAULT_CONFIG, SnfClusterer, compare_with_fusion, resolve_config,  # noqa: E402
                                                 snf_cluster)
from methadata.clustering.network_fusion.core import snf_matrix  # noqa: E402
from methadata.contracts import (Artifact, ArtifactRef, ArtifactRole, Bundle, InputKind, Observation, PairEvidence,  # noqa: E402
                                 Relation, discover, ids)
from methadata.devtools.realdata import BACKUP  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
VIEWS = {"text": "m-text", "image": "m-image", "structure": "m-struct"}
CFG = {"similarity_version": "test-sim-1", "dataset_version": "synthetic-1", "threshold": 0.5, "neighbors": 3,
       "views": {k: {"method_id": v} for k, v in VIEWS.items()}}


def obs(name):
    url = f"https://{name}.example.invalid/a"
    oid = ids.observation_id(f"synthetic://{name}", "url_only")
    sha = ids.sha256_bytes(name.encode())
    art = Artifact(artifact_id=ids.artifact_id(oid, "input_url", sha), observation_id=oid, role=ArtifactRole.input_url,
                   sha256=sha, url=url)
    return Observation(observation_id=oid, canonical_id=ids.canonical_id([], url), source_ref=f"synthetic://{name}",
                       input_kind=InputKind.url_only, artifacts=[art], tags=["synthetic"])


def pe(a, b, score, method, relation=Relation.shared_template):
    ca, cb = sorted((a.canonical_id, b.canonical_id))
    refs = {o.canonical_id: [ArtifactRef(artifact_id=o.artifacts[0].artifact_id)] for o in (a, b)} if score is not None else {}
    return PairEvidence(run_id="r", pair=(ca, cb), method_id=method, relation=relation, score=score,
                        evidence_refs=refs, explanation="test")


def build(table, names):
    o = {n: obs(n) for n in names}
    ev = []
    for (a, b), (t, i, s) in table.items():
        for m, v in zip(("m-text", "m-image", "m-struct"), (t, i, s)):
            ev.append(pe(o[a], o[b], v, m))
    return o, ev


def block_table(names_a, names_b, cross):
    t = {}
    for grp in (names_a, names_b):
        for i, a in enumerate(grp):
            for b in grp[i + 1:]:
                t[(a, b)] = (0.9, 0.9, 0.9)
    for a in names_a:
        for b in names_b:
            t[(a, b)] = cross
    return t


def groups(run):
    g = {}
    for a in run.assignments:
        for c in a.cluster_ids:
            g.setdefault(c, set()).add(a.material_id)
    return {frozenset(v) for v in g.values()}


def test_snf_matrix_properties():
    import numpy as np
    rng = np.random.default_rng(0)
    vs = []
    for _ in range(3):
        m = rng.random((8, 8))
        vs.append((m + m.T) / 2)
    f, d, S, P0 = snf_matrix(vs, k=3, iterations=10)
    assert f.shape == (8, 8) and np.allclose(f, f.T) and f.min() >= 0 and f.max() <= 1 + 1e-12
    assert np.allclose(np.diag(f), 0) and np.allclose(S[0].sum(axis=1), 1)  # jądro kNN jest wierszowo stochastyczne


def test_two_blocks_are_grouped_with_evidence():
    o, ev = build(block_table(list("ABC"), list("DEF"), (0.05, 0.0, 0.05)), "ABCDEF")
    res = snf_cluster(list(o.values()), ev, CFG)
    assert groups(res.run) == {frozenset(o[n].canonical_id for n in "ABC"), frozenset(o[n].canonical_id for n in "DEF")}
    assert res.run.method_id == "snf-v1" and res.run.relation is Relation.campaign_candidate
    assert res.run.dataset_version == "synthetic-1" and res.run.config["snf"]["similarity_version"] == "test-sim-1"
    assert all(p.method_id == "snf-v1" and p.evidence_refs for p in res.pairs if p.score is not None)
    assert not res.amplified and set(res.view_stats) == set(VIEWS)
    Bundle(observations=list(o.values()), pairs=[*ev, *res.pairs], runs=[res.run])


def test_deterministic_and_input_order_independent():
    o, ev = build(block_table(list("ABC"), list("DEF"), (0.05, 0.0, 0.05)), "ABCDEF")
    a = snf_cluster(list(o.values()), ev, CFG)
    b = snf_cluster(list(reversed(list(o.values()))), list(reversed(ev)), CFG)
    assert a.run == b.run and [p.score for p in a.pairs] == [p.score for p in b.pairs]


def test_subset_with_complete_views_reported():
    t = block_table(list("ABC"), list("DEF"), (0.05, 0.0, 0.05))
    o, ev = build(t, "ABCDEF")
    # materiał G ma tylko widok tekstu
    g = obs("G")
    ev += [pe(g, o["A"], 0.8, "m-text"), pe(g, o["A"], None, "m-image"), pe(g, o["A"], None, "m-struct")]
    o["G"] = g
    res = snf_cluster(list(o.values()), ev, CFG)
    assert res.excluded == {g.canonical_id: ["image", "structure"]}
    assert g.canonical_id not in res.subset and len(res.subset) == 6
    st = {a.material_id: a.status.value for a in res.run.assignments}
    assert st[g.canonical_id] == "insufficient_data"
    assert res.view_stats["text"]["materials_with_view"] == 7 and res.view_stats["image"]["materials_with_view"] == 6
    full = snf_cluster(list(o.values()), ev, {**CFG, "subset": "all"})
    assert g.canonical_id in full.subset


def test_amplified_edge_has_original_traces():
    # C-D słabo bezpośrednio, ale oba silnie powiązane z tymi samymi sąsiadami A,B -> wzmocnienie sąsiedztwem
    t = {("A", "B"): (.95, .95, .95), ("A", "C"): (.8, .8, .8), ("B", "C"): (.8, .8, .8), ("A", "D"): (.8, .8, .8),
         ("B", "D"): (.8, .8, .8), ("C", "D"): (.3, .3, .3), ("A", "E"): (.1, .1, .1), ("B", "E"): (.1, .1, .1),
         ("C", "E"): (.1, .1, .1), ("D", "E"): (.1, .1, .1)}
    o, ev = build(t, "ABCDE")
    res = snf_cluster(list(o.values()), ev, {**CFG, "neighbors": 2})
    key = tuple(sorted((o["C"].canonical_id, o["D"].canonical_id)))
    d = res.edge_traces(*key)
    assert d["direct"] < 0.5 <= d["fused"] and d["amplified"] and key in res.amplified
    assert d["via"] and {v["via"] for v in d["via"]} <= {o["A"].canonical_id, o["B"].canonical_id}
    leg = d["via"][0]["legs_a"][0]
    assert leg["pair_evidence_id"] in {e.pair_evidence_id for e in ev}      # oryginalny dowód pary (c,m)
    p = next(p for p in res.pairs if p.pair == key)
    assert p.score >= 0.5 and "wzmocniona przez sąsiedztwo" in p.explanation and p.evidence_refs[key[0]] and p.evidence_refs[key[1]]


def test_view_floor_blocks_noise():
    o, ev = build(block_table(list("ABC"), list("DEF"), (0.02, 0.0, 0.02)), "ABCDEF")
    # w obrębie bloku 0.01: wszystko poniżej bramki -> brak krawędzi mimo znormalizowanej fuzji
    weak = [pe(o[a], o[b], 0.01, m) for a, b in (("A", "B"), ("A", "C"), ("B", "C")) for m in VIEWS.values()]
    ev2 = [e for e in ev if not (set(e.pair) <= {o[x].canonical_id for x in "ABC"})] + weak
    res = snf_cluster(list(o.values()), ev2, CFG)
    assert frozenset(o[n].canonical_id for n in "ABC") not in groups(res.run)


@pytest.mark.parametrize("clusterer", ["components", "spectral", "leiden"])
def test_clusterers_on_fused_network(clusterer):
    if clusterer == "spectral":
        pytest.importorskip("sklearn")
    if clusterer == "leiden":
        pytest.importorskip("leidenalg")
    o, ev = build(block_table(list("ABC"), list("DEF"), (0.05, 0.0, 0.05)), "ABCDEF")
    res = snf_cluster(list(o.values()), ev, {**CFG, "clusterer": clusterer, "clusterer_config": {"repeats": 3} if clusterer != "components" else {}})
    assert groups(res.run) == {frozenset(o[n].canonical_id for n in "ABC"), frozenset(o[n].canonical_id for n in "DEF")}
    assert res.run.config["clusterer_method"].startswith(clusterer[:5])


def test_compare_with_k09_same_subset():
    o, ev = build(block_table(list("ABC"), list("DEF"), (0.05, 0.0, 0.05)), "ABCDEF")
    res = snf_cluster(list(o.values()), ev, CFG)
    cmp_ = compare_with_fusion(res, list(o.values()), ev)
    assert cmp_["identical"] and cmp_["rand_index"] == 1.0 and cmp_["subset"] == sorted(res.subset)
    assert cmp_["k09_config"]["min_support"] == 2 and cmp_["edge_differences"] == []


def test_config_validation():
    for bad in ({"views": {"text": {"method_id": "m"}}}, {"scale": "x"}, {"clusterer": "kmeans"}, {"neighbors": 0},
                {"threshold": 0}, {"subset": "some"}, {"similarity_version": ""}):
        with pytest.raises(ValueError):
            resolve_config({**CFG, **bad})
    with pytest.raises(ValueError):
        resolve_config({k: v for k, v in CFG.items() if k != "dataset_version"})
    resolve_config({k: v for k, v in CFG.items() if k != "dataset_version"}, for_run=False)


def test_adapter_discovered_and_config_mirror():
    ad = discover(strict=False)
    assert "snf-v1" in ad and isinstance(ad["snf-v1"], SnfClusterer)
    data = json.loads((ROOT / "configs" / "network_fusion" / "snf.json").read_text(encoding="utf-8"))
    data.pop("name")
    assert data == DEFAULT_CONFIG


@pytest.mark.skipif(not BACKUP.is_dir(), reason="brak katalogu BACKUP")
def test_real_corpus(tmp_path):
    from methadata.clustering.network_fusion.run import run_snf
    from methadata.ingest.__main__ import main as ingest_main
    store = tmp_path / "store"
    assert ingest_main(["import", "--store", str(store), *sorted(str(p) for p in BACKUP.glob("p*") if p.is_dir())]) == 0
    base = json.loads((ROOT / "configs" / "baseline" / "baseline.json").read_text(encoding="utf-8"))
    cfg = json.loads((ROOT / "configs" / "network_fusion" / "snf.json").read_text(encoding="utf-8"))
    cfg.pop("name")
    ref = json.loads((ROOT / "configs" / "fusion" / "eval.json").read_text(encoding="utf-8"))["reference"]["families"]
    res = run_snf(store, base, cfg, reference=ref)
    rep = res["report"]
    assert rep["dataset_version"].endswith(base["expected"]["dataset_fingerprint"])
    assert len(rep["subset"]) == base["expected"]["materials"] and not rep["excluded_missing_views"]
    assert ["p8", "p11"] in rep["snf_groups"]                        # przykład dopasowania zgodny z K09
    assert ["p8", "p11"] in rep["k09"]["groups"] and ["p2", "p7", "p15"] in rep["k09"]["groups"]
    # przypadek zawodności: SNF łączy rodzinę p2/p7/p15 z p3/p4 przez sąsiedztwo (K09 tego nie robi)
    assert any({e["a"], e["b"]} & {"p3", "p4"} and {e["a"], e["b"]} & {"p2", "p7", "p15"} for e in rep["amplified_edges"])
    assert rep["reference_control"]["k09_matches"] and not rep["reference_control"]["snf_matches"]
    amp = rep["amplified_edges"][0]
    assert amp["via"] and all(amp["n_refs"].values()) and amp["direct"] < 0.5 <= amp["fused"]
    Bundle.model_validate_json(res["bundle"].model_dump_json())
