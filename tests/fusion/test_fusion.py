import itertools
import json
import os
import random
from pathlib import Path

import pytest

from methadata.clustering.fusion import (DEFAULT_CONFIG, FusionComparator, fuse_and_cluster, fuse_pair,
                                         resolve_config)
from methadata.clustering.fusion import core
from methadata.contracts import (Artifact, ArtifactRef, ArtifactRole, Bundle, InputKind, Observation, PairEvidence,
                                 Relation, discover, ids)

ROOT = Path(__file__).resolve().parents[2]
CFG = {"similarity_version": "test-sim-1", "dataset_version": "synthetic-1", "threshold": 0.5, "coverage_min": 0.5,
       "min_support": 2,
       "families": {"asset": {"method_id": "m-asset", "weight": 0.5, "origin": "asset", "support_min": 0.5},
                    "text": {"method_id": "m-text", "weight": 0.5, "origin": "content", "support_min": 0.3}}}
OCR = {"method_id": "m-ocr", "weight": 0.5, "origin": "content", "support_min": 0.3}


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


@pytest.fixture
def four():
    return {n: obs(n) for n in "ABCD"}


def groups(run):
    g = {}
    for a in run.assignments:
        for c in a.cluster_ids:
            g.setdefault(c, set()).add(a.material_id)
    return {frozenset(v) for v in g.values()}


def test_weighted_mean_coverage_support(four):
    a, b = four["A"], four["B"]
    pf = fuse_pair(tuple(sorted((a.canonical_id, b.canonical_id))), [pe(a, b, 0.8, "m-asset"), pe(a, b, 0.6, "m-text")], CFG)
    assert pf.fused == pytest.approx(0.7) and pf.coverage == 1.0
    # asset i content to dwa źródła, oba powyżej swoich progów wsparcia
    assert pf.supporting_origins == 2 and pf.status == "ok" and pf.score == pytest.approx(0.7)


def test_coverage_gate_gives_none(four):
    a, b = four["A"], four["B"]
    cfg = {**CFG, "coverage_min": 0.6}
    pf = fuse_pair(tuple(sorted((a.canonical_id, b.canonical_id))), [pe(a, b, 0.9, "m-asset"), pe(a, b, None, "m-text")], cfg)
    assert pf.fused == pytest.approx(0.9) and pf.coverage == pytest.approx(0.5)
    assert pf.status == "insufficient_coverage" and pf.score is None
    out = core.to_pair_evidence(pf, resolve_config(cfg, for_run=False), "r")
    assert out.score is None and "pokrycie 0.50" in out.explanation


def test_support_gate_caps_below_threshold(four):
    a, b = four["A"], four["B"]
    pf = fuse_pair(tuple(sorted((a.canonical_id, b.canonical_id))), [pe(a, b, 0.2, "m-asset"), pe(a, b, 0.95, "m-text")], CFG)
    assert pf.fused > 0.5 and pf.supporting_origins == 1 and pf.status == "insufficient_support"
    assert pf.score < 0.5  # jedna rodzina nie daje hipotezy kampanii
    assert "obcięty" in core.explain(pf, resolve_config(CFG, for_run=False))


def test_shared_origin_counted_once(four):
    a, b = four["A"], four["B"]
    pair = tuple(sorted((a.canonical_id, b.canonical_id)))
    cfg3 = {**CFG, "families": {**CFG["families"], "ocr": OCR}}
    ev = [pe(a, b, 0.2, "m-asset"), pe(a, b, 0.9, "m-text"), pe(a, b, 0.9, "m-ocr")]
    with_ocr = fuse_pair(pair, ev, cfg3)
    without = fuse_pair(pair, ev[:2], CFG)
    assert with_ocr.fused == pytest.approx(without.fused)          # wkład liczony raz
    assert with_ocr.coverage == without.coverage == 1.0
    assert with_ocr.supporting_origins == 1                        # OCR + HTML to jedno źródło, nie dwa
    assert with_ocr.status == "insufficient_support"
    (content,) = [o for o in with_ocr.origins if o.origin == "content"]
    assert content.families == ("ocr", "text") and content.weight == 0.5
    # po wyłączeniu jednej z dwóch rodzin tego samego źródła treść nadal wnosi wkład
    assert with_ocr.ablation["text"].fused == pytest.approx(with_ocr.fused)
    assert with_ocr.ablation["ocr"].fused == pytest.approx(with_ocr.fused)
    # wyłączenie rodziny assets zostawia wyłącznie tekst
    assert with_ocr.ablation["asset"].fused == pytest.approx(0.9) and with_ocr.ablation["asset"].supporting_origins == 1


def test_ablation_shown_in_pair_evidence(four):
    a, b = four["A"], four["B"]
    res = fuse_and_cluster([a, b], [pe(a, b, 0.9, "m-asset"), pe(a, b, 0.8, "m-text")], CFG)
    (p,) = res.pairs
    assert p.relation is Relation.campaign_candidate and p.method_id == "fusion-v1"
    assert "Bez rodziny: asset" in p.explanation and "text" in p.explanation and "pokrycie" in p.explanation
    assert p.evidence_refs[p.pair[0]] and p.evidence_refs[p.pair[1]]
    assert res.ablation_groups == {"asset": [], "text": []}        # jedna rodzina nie wystarcza do grupy
    assert groups(res.run) == {frozenset(p.pair)}


def test_cluster_requires_support_and_exposes_evidence(four):
    o = four
    ev = [pe(o["A"], o["B"], 0.9, "m-asset"), pe(o["A"], o["B"], 0.8, "m-text"),
          pe(o["B"], o["C"], 0.1, "m-asset"), pe(o["B"], o["C"], 0.99, "m-text"),   # sam tekst: nie łączy
          pe(o["C"], o["D"], 0.7, "m-asset"), pe(o["C"], o["D"], 0.7, "m-text")]
    res = fuse_and_cluster(list(o.values()), ev, CFG)
    assert groups(res.run) == {frozenset({o["A"].canonical_id, o["B"].canonical_id}),
                               frozenset({o["C"].canonical_id, o["D"].canonical_id})}
    assert res.run.relation is Relation.campaign_candidate
    assert res.run.config["similarity_version"] == "test-sim-1" and res.run.dataset_version == "synthetic-1"
    assert res.run.config["fusion"]["families"]["asset"]["method_id"] == "m-asset"
    assert all(e.pair_evidence_id for e in res.run.edges) and len(res.run.edges) == 2
    evid = res.assignment_evidence(o["A"].canonical_id)
    assert len(evid) == 1 and evid[0]["detail"]["status"] == "ok" and "without" in evid[0]["detail"]
    Bundle(observations=list(o.values()), pairs=list(res.pairs), runs=[res.run])    # spójność odwołań
    # bez rodziny asset nic nie jest grupowane (min_support=2)
    assert res.ablation_groups["asset"] == [] and res.ablation_groups["text"] == []


def test_no_comparison_and_order_independence(four):
    o = four
    ev = [pe(o["A"], o["B"], 0.9, "m-asset"), pe(o["A"], o["B"], 0.9, "m-text"),
          pe(o["C"], o["D"], None, "m-asset"), pe(o["C"], o["D"], None, "m-text")]
    base = fuse_and_cluster(list(o.values()), ev, CFG)
    st = {a.material_id: a.status.value for a in base.run.assignments}
    assert st[o["C"].canonical_id] == "insufficient_data" and st[o["A"].canonical_id] == "grouped"
    for seed in range(5):
        e2, o2 = list(ev), list(o.values())
        random.Random(seed).shuffle(e2)
        random.Random(seed).shuffle(o2)
        r = fuse_and_cluster(o2, e2, CFG)
        assert r.run.model_dump() == base.run.model_dump()


def test_config_validation(four):
    for bad in ({"similarity_version": ""}, {"dataset_version": None}, {"threshold": 0}, {"min_support": 0},
                {"families": {}}, {"coverage_min": 2}):
        with pytest.raises(ValueError):
            resolve_config({**CFG, **bad})
    f = {"asset": {"method_id": "m-asset", "weight": 0.5}}
    with pytest.raises(ValueError, match="support_min"):
        resolve_config({**CFG, "families": f})


def test_comparator_adapter_discovered_and_runs(four):
    assert "fusion-v1" in discover()
    a, b = four["A"], four["B"]

    class Fake:
        def __init__(self, mid, s):
            self.method_id, self.s = mid, s
        def compare(self, x, y, feats, *, run_id, optional=None):
            return [pe(x, y, self.s, self.method_id)]
    cmp_ = FusionComparator(CFG, [Fake("m-asset", 0.9), Fake("m-text", 0.7)])
    (p,) = cmp_.compare(b, a, {}, run_id="r1")
    assert p.score == pytest.approx(0.8) and p.pair == tuple(sorted((a.canonical_id, b.canonical_id)))


def test_default_config_matches_file():
    data = json.loads((ROOT / "configs" / "fusion" / "fusion.json").read_text(encoding="utf-8"))
    data.pop("name")
    assert data == DEFAULT_CONFIG


# ------------------------------------------------------------ rzeczywisty korpus (pomijany bez BACKUP)

from methadata.devtools.realdata import BACKUP


@pytest.mark.skipif(not BACKUP.is_dir(), reason="brak katalogu BACKUP")
def test_real_corpus(tmp_path):
    from methadata.clustering.fusion.run import run_fusion
    from methadata.ingest.__main__ import main as ingest_main
    store = tmp_path / "store"
    assert ingest_main(["import", "--store", str(store), *sorted(str(p) for p in BACKUP.glob("p*") if p.is_dir())]) == 0
    base = json.loads((ROOT / "configs" / "baseline" / "baseline.json").read_text(encoding="utf-8"))
    fcfg = json.loads((ROOT / "configs" / "fusion" / "fusion.json").read_text(encoding="utf-8"))
    fcfg.pop("name")
    res = run_fusion(store, base, fcfg)
    rep = res["report"]
    assert rep["dataset_version"].endswith(base["expected"]["dataset_fingerprint"])
    assert rep["groups"] == [["p2", "p7", "p15"], ["p8", "p11"]]
    byp = {frozenset((p["a"], p["b"])): p for p in rep["pairs"]}
    # przykład dopasowania: obie rodziny zgodne
    ok = byp[frozenset(("p8", "p11"))]
    assert ok["status"] == "ok" and ok["supporting_origins"] == 2 and ok["coverage"] == 1.0
    assert set(ok["without"]) == {"asset", "text"}
    # przypadek zawodności: tekst bardzo podobny, ale grafiki nie -> sama rodzina nie tworzy hipotezy kampanii
    bad = byp[frozenset(("p3", "p4"))]
    assert bad["fused"] > 0.5 and bad["status"] == "insufficient_support"
    assert all(pf.score < 0.5 for pf in res["result"].fusions.values() if pf.status == "insufficient_support")
    Bundle.model_validate_json(res["bundle"].model_dump_json())
