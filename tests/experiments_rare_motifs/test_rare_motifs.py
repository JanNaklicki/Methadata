from methadata.devtools.realdata import BACKUP as _REAL_BACKUP
import json
from pathlib import Path

import pytest

from methadata.contracts import AdapterKind, Artifact, ArtifactRef, Feature, InputKind, Observation, ids
from methadata.contracts.models import ArtifactRole, ValueType
from methadata.contracts.registry import discover
from methadata.experiments.rare_motifs import (MotifConfig, RareMotifComparator, TraceConfig, TraceMatrix, ablate_rarest,
                                               dumps, mine_motifs, minimal_signature, store_export)

BACKUP = _REAL_BACKUP
RES_A, RES_B = "https://cdn.evil.example/kit/pay.js", "https://cdn.evil.example/kit/img.png"
FORM = {"method": "post", "action": "/x", "action_resolved": "https://a.example/x",
        "fields": [{"tag": "input", "name": "card", "type": "text"}, {"tag": "input", "name": "cvv", "type": "text"}]}
TEXT_A = "wpłać dwieście złotych aby odebrać swoją gwarantowaną rekompensatę od banku"
TEXT_B = "ekskluzywna oferta tylko dzisiaj dla pierwszych stu klientów naszego sklepu"


def make(name, resources=(), form=None, texts=()):
    oid = ids.observation_id(name, "full_capture")
    art = Artifact(artifact_id=ids.artifact_id(oid, "landing_html", ids.sha256_bytes(name.encode())), observation_id=oid,
                   role=ArtifactRole.landing_html, local_ref=f"{name}.html", sha256=ids.sha256_bytes(name.encode()))
    o = Observation(observation_id=oid, canonical_id=name, source_ref=name, input_kind=InputKind.full_capture, artifacts=[art])
    ref = [ArtifactRef(artifact_id=art.artifact_id)]
    fs = []

    def add(kind, value, fam="html"):
        fs.append(Feature(feature_id=ids.feature_id(oid, fam, kind, "t", [len(fs), str(value)]), observation_id=oid,
                          family=fam, kind=kind, value_type=ValueType.json, value=value, extractor_version="t",
                          evidence_refs=ref))
    for r in resources:
        add("resource_edge", {"rel": "script", "state": "missing", "resolved": r})
    if form:
        add("form", form)
    for t in texts:
        add("text_section", {"selector": "p", "tag": "p", "text": t, "hidden": False, "start": 0, "end": 1})
    return o, fs


def corpus():
    """a,b,c = grupa kampanii (zasób+formularz+tekst); d,e dzielą tylko tekst B; reszta szum. Zasób A jest też w d."""
    spec = {
        "a": dict(resources=[RES_A], form=FORM, texts=[TEXT_A, TEXT_B]),
        "b": dict(resources=[RES_A], form=FORM, texts=[TEXT_A]),
        "c": dict(resources=[RES_A], form=FORM, texts=[TEXT_A + " dziś"]),
        "d": dict(resources=[RES_A, RES_B], texts=[TEXT_B]),
        "e": dict(resources=[RES_B], texts=[TEXT_B]),
    }
    for i in range(12):
        spec[f"n{i:02d}"] = dict(resources=[f"https://x{i}.example/a.js"], texts=[f"zupełnie inny tekst numer {i} o niczym ważnym tutaj"])
    obs, fm = [], {}
    for k, v in spec.items():
        o, fs = make(k, **v)
        obs.append(o)
        fm[o.observation_id] = fs
    return obs, fm


def build(**kw):
    obs, fm = corpus()
    return obs, fm, TraceMatrix.build(obs, fm, TraceConfig(**kw))


def tid(m, kind, needle):
    return next(t for t, (k, lab) in m.traces.items() if k == kind and needle in lab)


def test_frequencies_same_set():
    _, _, m = build()
    assert m.n == 17
    ra, fm_ = tid(m, "resource", "kit/pay.js"), tid(m, "form", "card")
    assert m.df(ra) == 4 and m.df(fm_) == 3
    assert m.joint_df([ra, fm_]) == 3                      # koniunkcja liczona na tej samej macierzy
    st = m.combination_stats([ra, fm_])
    assert st["support"] == 3 and st["lift"] == pytest.approx(3 / (17 * (4 / 17) * (3 / 17)))
    assert st["weight"] == pytest.approx(m.idf(ra) + m.idf(fm_))


def test_mine_motifs_cross_kind_and_weighting():
    _, _, m = build()
    ms = mine_motifs(m, MotifConfig(rare_fraction=0.3))
    top = ms[0]
    assert set(top.kinds) == {"resource", "form", "text"} and top.materials == ("a", "b", "c")
    assert all(len(set(x.kinds)) == len(x.kinds) >= 2 for x in ms)
    # zasób B + tekst B: rzadkie, ale wspólne dla d,e (rodzaje różne) -> motyw
    assert any(x.materials == ("d", "e") for x in ms)
    # ważenie: kombinacja rzadsza ma większą wagę niż kombinacja częstszych składników
    w = {x.materials: x.weight for x in ms}
    assert max(w.values()) == ms[0].weight


def test_minimal_signature_and_outside_counts():
    _, _, m = build()
    sig = minimal_signature(m, ["a", "b", "c"])
    assert sig.common and sig.signature
    # zasób A występuje poza grupą w d (1); formularz nigdzie poza grupą
    ra, fm_ = tid(m, "resource", "kit/pay.js"), tid(m, "form", "card")
    assert sig.outside_each[ra] == 1 and sig.outside_each[fm_] == 0
    assert sig.signature == (fm_,) and sig.distinguishes and sig.outside_support == ()


def test_ablation_removing_rarest():
    _, _, m = build()
    ab = ablate_rarest(m, ["a", "b", "c"])
    fm_ = tid(m, "form", "card")
    assert ab["removed"] == fm_ and ab["removed_df"] == 3
    # po usunięciu formularza podpis przechodzi na zasób+tekst/ inne wspólne i nadal może rozróżniać grupę lub nie
    assert fm_ not in ab["reduced"].common and ab["reduced"].excluded == (fm_,)
    assert ab["reduced"].signature != ab["full"].signature
    only_res = minimal_signature(m, ["a", "b", "c"], exclude=[t for t in m.traces if m.traces[t][0] != "resource"])
    assert not only_res.distinguishes and only_res.outside_support == ("d",)


def test_comparator_pair_evidence_and_none():
    obs, fm, m = build()
    cmp = RareMotifComparator(m, MotifConfig(rare_fraction=0.3))
    o = {x.canonical_id: x for x in obs}
    fms = lambda *c: {o[k].observation_id: fm[o[k].observation_id] for k in c}
    ev = cmp.compare(o["a"], o["b"], fms("a", "b"), run_id="r1")[0]
    assert ev.pair == ("a", "b") and ev.score is not None and ev.score > 0.5
    assert ev.evidence_refs["a"] and ev.evidence_refs["b"] and "df=" in ev.explanation
    noise = cmp.compare(o["a"], o["n00"], fms("a", "n00"), run_id="r1")[0]
    assert noise.score == 0.0
    d = cmp.compare(o["d"], o["e"], fms("d", "e"), run_id="r1")[0]
    assert 0 < d.score and d.score != noise.score
    assert RareMotifComparator().compare(o["a"], o["b"], fms("a", "b"), run_id="r1")[0].score is None
    # niezależność od kolejności argumentów
    assert cmp.compare(o["b"], o["a"], fms("a", "b"), run_id="r1")[0].score == ev.score


def test_export_matrix_equals_graph_with_source_ids():
    _, _, m = build()
    ex = m.export(min_df=2, with_conjunctions=mine_motifs(m, MotifConfig(rare_fraction=0.3)))
    mat, g = ex["matrix"], ex["graph"]
    assert mat["shape"] == [len(mat["rows"]), len(mat["columns"])]
    assert {(mat["rows"][r], mat["columns"][c]) for r, c in mat["entries"]} == {(e["material"], e["trace"]) for e in g["edges"]}
    assert all(e["observation_id"] and e["feature_ids"] and e["evidence_refs"] for e in g["edges"])
    cols = {t["trace_id"]: t["df"] for t in ex["traces"]}
    assert all(m.df(t) == df >= 2 for t, df in cols.items()) and ex["conjunctions"]
    assert dumps(ex) == dumps(m.export(min_df=2, with_conjunctions=mine_motifs(m, MotifConfig(rare_fraction=0.3))))


def test_store_export_uses_derived_store():
    _, _, m = build()
    calls = []

    class Fake:
        def put_derived(self, **kw):
            calls.append(kw)
            return "ok"
    ref = ArtifactRef(artifact_id="art-x")
    assert store_export(m.export(), Fake(), observation_id="obs-x", parent=ref) == "ok"
    assert calls[0]["role"] is ArtifactRole.graph_edges and json.loads(calls[0]["data"])["schema"].startswith("methadata.rare_motifs")


def test_order_independent():
    obs, fm = corpus()
    m1 = TraceMatrix.build(obs, fm)
    m2 = TraceMatrix.build(list(reversed(obs)), fm)
    assert dumps(m1.export()) == dumps(m2.export())


def test_discover():
    found = discover()
    assert found["rare-motifs-v1"].spec.kind is AdapterKind.comparator


@pytest.mark.skipif(not BACKUP.is_dir(), reason="brak katalogu BACKUP")
def test_real_backup_group_signature():
    from methadata.experiments.rare_motifs.report import measure
    r = measure(BACKUP, group=("p2", "p7", "p15"), motif_cfg=MotifConfig(rare_fraction=0.25, min_kinds=1))
    ab = r["ablation"]
    assert r["n"] >= 13 and ab["full"].distinguishes and ab["removed_df"] == 3
    # w tym zbiorze rodzina A nie ma wspólnych śladów zasobu/formularza: kombinacje międzyrodzajowe nie istnieją
    assert mine_motifs(r["matrix"], MotifConfig(rare_fraction=0.25)) == []
