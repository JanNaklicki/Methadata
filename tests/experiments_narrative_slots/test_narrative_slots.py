import json
from pathlib import Path

import pytest

from methadata.contracts import AdapterKind
from methadata.contracts.registry import discover
from methadata.experiments.lexical import LexicalTextExtractor, sections
from methadata.experiments.narrative_slots import (LEXICON, SLOTS, NarrativeSlotsComparator, NarrativeSlotsExtractor,
                                                   compare_roles, extract_slots, layout, slots_from_features, swap_names)
from methadata.experiments.narrative_slots.bench import MemReader, analyse, load_backup, make_observation
from methadata.experiments.narrative_slots.samples import FRAME, PERSON_A, PERSON_B, SWAP_A_TO_B, pages
from methadata.runner import run_experiment

BACKUP = Path("/Users/jnaklicki/Documents/Hackathon/Methadata/BACKUP")
ROOT = Path(__file__).resolve().parents[2]


def build(html_by_name):
    r = MemReader()
    obs = [make_observation(n, h.encode(), r) for n, h in html_by_name.items()]
    return obs, r


def secs_of(obs, r):
    f = list(LexicalTextExtractor().extract(obs, reader=r)[0])
    return sections(f)


def test_quotes_are_verbatim_spans_and_refs():
    obs, r = build({"a": FRAME.format(**PERSON_A)})
    feats, st = NarrativeSlotsExtractor().extract(obs[0], reader=r)
    assert {f.value["slot"] for f in feats} == set(SLOTS) and st[-1].state.value == "ok"
    secs = secs_of(obs[0], r)
    texts = [s["text"] for s in secs]
    for f in feats:
        v = f.value
        assert any(t[v["start"]:v["end"]] == v["quote"] for t in texts), v["slot"]
        assert f.evidence_refs  # każde pole ma odwołanie do artefaktu


def test_no_cue_means_empty_slot():
    obs, r = build({"a": "<html><head><title>Pogoda</title></head><body><p>Jutro będzie słonecznie.</p></body></html>"})
    res = extract_slots(secs_of(obs[0], r))
    assert all(v is None for v in res.values())
    feats, st = NarrativeSlotsExtractor().extract(obs[0], reader=r)
    assert not feats and "puste=" in st[-1].detail


def test_same_frame_other_person_vs_same_person_other_context():
    obs, r = build(pages())
    _, slots, rows = analyse(obs, r)
    by = {tuple(x["pair"]): x for x in rows}
    same_frame = by[("can-frame-A", "can-frame-B")]
    other_ctx = by[("can-frame-A", "can-other-A")]
    assert same_frame["role_score"] > 0.9 and same_frame["frame_match"] and same_frame["name_score"] == 0.0
    assert other_ctx["role_score"] < 0.35 and not other_ctx["frame_match"] and "stanow" in other_ctx["shared_names"]
    assert same_frame["name_score"] < other_ctx["name_score"]  # nazwy i role są niezależnymi osiami


def test_layout_invariant_under_name_swap():
    html_a = FRAME.format(**PERSON_A)
    swapped = swap_names(html_a, SWAP_A_TO_B)
    assert "Stanowski" not in swapped and swapped == FRAME.format(**PERSON_B)
    obs, r = build({"a": html_a, "s": swapped})
    la, ls = [layout({k: (v.as_value() if v else None) for k, v in extract_slots(secs_of(o, r)).items()}) for o in obs]
    assert la == ls and all(la[s] is not None for s in SLOTS)
    # zmiana ramy (inna klasa zdarzenia) zmienia układ
    other = html_a.replace("napaści", "aresztowania").replace("napaść", "aresztowanie")
    obs2, r2 = build({"o": other})
    lo = layout({k: (v.as_value() if v else None) for k, v in extract_slots(secs_of(obs2[0], r2)).items()})
    assert lo["event"][0] == "arrest" and lo != la


def test_comparator_pair_evidence_and_none():
    obs, r = build(pages())
    ex, cmp_ = NarrativeSlotsExtractor(), NarrativeSlotsComparator()
    fm = {o.observation_id: list(ex.extract(o, reader=r)[0]) for o in obs}
    a, b, c = sorted(obs, key=lambda o: o.canonical_id)  # frame-A, frame-B, other-A
    ev = cmp_.compare(a, b, fm, run_id="r")[0]
    assert ev.score > 0.9 and ev.evidence_refs[a.canonical_id] and ev.evidence_refs[b.canonical_id]
    assert "Zgodność konkretnych nazw" in ev.explanation and "nie wchodzi do wyniku" in ev.explanation
    # fallback na cechy lexical/text_field (bez ekstraktora narracji)
    lex = {o.observation_id: list(LexicalTextExtractor().extract(o, reader=r)[0]) for o in obs}
    assert cmp_.compare(a, b, lex, run_id="r")[0].score == pytest.approx(ev.score)
    # brak tekstu po jednej stronie -> None, nie 0
    empty, r2 = build({"e": "<html><body></body></html>"})
    fm2 = {empty[0].observation_id: [], a.observation_id: fm[a.observation_id]}
    assert cmp_.compare(a, empty[0], fm2, run_id="r")[0].score is None


def test_backend_is_optional_and_quotes_must_be_verbatim():
    class Fake:
        def extract(self, sections):
            return {"event": {"quote": "Brutalna napaść na Krzysztofa Stanowskiego", "cls": "attack"},
                    "promise": {"quote": "tego zdania nie ma w tekście", "cls": "return"}}
    obs, r = build({"a": FRAME.format(**PERSON_A)})
    secs = secs_of(obs[0], r)
    base = extract_slots(secs)
    assert not any(v.extra.get("backend") for v in base.values() if v)  # domyślnie reguły
    res = extract_slots(secs, backend=Fake())
    assert res["event"].extra.get("backend") and res["event"].quote == "Brutalna napaść na Krzysztofa Stanowskiego"
    assert res["promise"].quote == base["promise"].quote  # zmyślony cytat odrzucony
    feats, _ = NarrativeSlotsExtractor(backend=Fake()).extract(obs[0], reader=r)
    assert {f.value["slot"] for f in feats} == set(SLOTS)


def test_frozen_config_matches_lexicon():
    cfg = json.loads((ROOT / "configs/narrative_slots/default.json").read_text(encoding="utf-8"))
    assert cfg["lexicon"] == LEXICON and cfg["backend"] is None


def test_discover_and_runner():
    found = discover()
    assert found["narrative-slots-v1"].spec.kind is AdapterKind.comparator
    assert found["narrative-slots-extractor"].spec.kind is AdapterKind.extractor
    obs, r = build(pages())
    cl = next(v for v in found.values() if v.spec.kind is AdapterKind.clusterer)
    cfg = {"dataset_version": "t", "seed": 0, "relation": "shared_narrative", "clusterer": cl.spec.id,
           "extractors": ["narrative-slots-extractor"], "comparators": ["narrative-slots-v1"],
           "cluster_config": {"threshold": 0.8, "similarity_version": "ns-1", "dataset_version": "t"}}
    res = run_experiment(cfg, obs, found, reader=r)
    sc = {tuple(p.pair): p.score for p in res["bundle"].pairs}
    assert sc[("can-frame-A", "can-frame-B")] > 0.9 and sc[("can-frame-A", "can-other-A")] < 0.35


@pytest.mark.skipif(not BACKUP.is_dir(), reason="brak katalogu BACKUP")
def test_real_p3_p4_same_frame_different_people():
    r = MemReader()
    obs = load_backup(BACKUP, ["p3", "p4", "p8"], r)
    if len(obs) < 3:
        pytest.skip("brak p3/p4/p8")
    feats, slots, rows = analyse(obs, r)
    p3, p4, p8 = slots["can-p3"], slots["can-p4"], slots["can-p8"]
    cmp34 = compare_roles(p3, p4)
    assert cmp34["frame_match"] and cmp34["role_score"] > 0.6
    assert cmp34["name_score"] == 0.0  # różne osoby, marki i platformy
    assert "Stanowskiego" in p3["who"]["quote"] and "Sekielskiego" in p4["who"]["quote"]
    assert p3["event"]["cls"] == p4["event"]["cls"] == "attack"
    assert p3["product"]["extra"]["product_name"] != p4["product"]["extra"]["product_name"]
    # brak cytatu = puste pole; inna rodzina daje niższy wynik ról
    assert p8["product"] is None and compare_roles(p3, p8)["role_score"] < cmp34["role_score"]
    for o in obs:
        secs = sections(feats[o.observation_id] and list(LexicalTextExtractor().extract(o, reader=r)[0]))
        for v in slots_from_features(feats[o.observation_id])[0].values():
            if v:
                assert any(s["text"][v["start"]:v["end"]] == v["quote"] for s in secs)
