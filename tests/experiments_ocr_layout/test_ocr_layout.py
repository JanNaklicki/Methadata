from methadata.devtools.realdata import BACKUP as _REAL_BACKUP
import io
import json
from pathlib import Path

import pytest

from methadata.contracts import AdapterKind, Artifact, Feature, InputKind, Observation, discover, ids
from methadata.contracts.models import ArtifactRole, ImageRect, TextSpan, ValueType
from methadata.experiments.ocr_layout import (OcrExtractor, OcrHtmlComparator, OcrLayoutComparator, OcrLine, OcrResult,
                                              OcrSentenceComparator, analyze_layout, error_impact, layout_similarity,
                                              locate_quote, match_to_html, ocr_results, sentences)
from methadata.experiments.ocr_layout.compare import FAMILY, KIND_BOXES


def L(text, x, y, w, h, conf=0.95):
    return OcrLine(text, (x, y, w, h), conf)


def ad(scale=1.0, title="Wielka wyprzedaz zimowa", body="Tylko dzis rabaty do 70% na cala kolekcje. Sprawdz oferte sklepu.",
       button="Kup teraz", conf=0.95):
    """Reklama 400x600: tytuł u góry, ilustracja (puste pasmo) w środku, przycisk na dole."""
    s = scale
    return OcrResult(int(400 * s), int(600 * s), [
        L(title, 40 * s, 30 * s, 320 * s, 40 * s, conf),
        L(body, 40 * s, 420 * s, 320 * s, 18 * s, conf),
        L(button, 140 * s, 520 * s, 120 * s, 30 * s, conf)], "fake", "0")


def feat(oid, aid, res):
    d = res.to_dict()
    return Feature(feature_id=ids.feature_id(oid, FAMILY, KIND_BOXES, "t", [aid, res.width]), observation_id=oid,
                   family=FAMILY, kind=KIND_BOXES, value_type=ValueType.json, value=d, extractor_version="t",
                   evidence_refs=[__import__("methadata").contracts.ArtifactRef(artifact_id=aid)])


def obs(name, role=ArtifactRole.landing_screenshot, sha_seed=None):
    oid = ids.observation_id(name, "full_capture")
    sha = ids.sha256_bytes((sha_seed or name).encode())
    a = Artifact(artifact_id=ids.artifact_id(oid, role, sha, "0"), observation_id=oid, role=role,
                 local_ref=name, sha256=sha)
    return Observation(observation_id=oid, canonical_id="can-" + name, source_ref=name, input_kind=InputKind.full_capture,
                       artifacts=[a]), a


def test_relative_coordinates_resolution_independent():
    a, b = ad(1.0), ad(2.5)
    la, lb = analyze_layout(a), analyze_layout(b)
    for r in ("title", "illustration", "button"):
        assert la[r] and lb[r]
        for u, v in zip(la[r]["rect_rel"], lb[r]["rect_rel"]):
            assert abs(u - v) < 0.01
    assert layout_similarity(la, lb) > 0.98
    assert la["title"]["text"] == "Wielka wyprzedaz zimowa" and la["button"]["text"] == "Kup teraz"
    ill = la["illustration"]["rect_rel"]
    assert la["title"]["rect_rel"][1] < ill[1] < la["button"]["rect_rel"][1]


def test_layout_comparator_and_different_layout():
    oa, aa = obs("a")
    ob, ab = obs("b")
    oc, ac = obs("c")
    flipped = OcrResult(400, 600, [L("Kup teraz", 140, 40, 120, 30), L("Inna reklama o czyms", 40, 520, 320, 40)], "fake")
    feats = {oa.observation_id: [feat(oa.observation_id, aa.artifact_id, ad())],
             ob.observation_id: [feat(ob.observation_id, ab.artifact_id, ad(2.0))],
             oc.observation_id: [feat(oc.observation_id, ac.artifact_id, flipped)]}
    cmp_ = OcrLayoutComparator()
    same = cmp_.compare(oa, ob, feats, run_id="r")[0]
    assert same.score > 0.95 and all(isinstance(r.locator, ImageRect) for refs in same.evidence_refs.values() for r in refs)
    diff = cmp_.compare(oa, oc, feats, run_id="r")[0]
    assert diff.score is None or diff.score < 0.4


def test_sentence_comparator_exact_fuzzy_and_evidence_rect():
    oa, aa = obs("a")
    ob, ab = obs("b")
    noisy = ad(1.5, title="Wie1ka wyprzedaz zimowa", body="Tylko dzis rabaty do 70% na cala ko1ekcje. Sprawdz oferte sklepu.")
    feats = {oa.observation_id: [feat(oa.observation_id, aa.artifact_id, ad())],
             ob.observation_id: [feat(ob.observation_id, ab.artifact_id, noisy)]}
    ev = OcrSentenceComparator().compare(oa, ob, feats, run_id="r")[0]
    assert ev.score > 0.85
    assert "Cytat" in ev.explanation and "~" in ev.explanation and "ko1ekcje" in ev.explanation
    ra = ev.evidence_refs[oa.canonical_id]
    assert any(r.locator.y == 30 and r.locator.width == 320 for r in ra)  # prostokąt tytułu na obrazie
    rb = ev.evidence_refs[ob.canonical_id]
    assert any(r.locator.y == 45 for r in rb)  # 30*1.5


def test_unrelated_text_scores_low():
    oa, aa = obs("a")
    ob, ab = obs("b")
    other = OcrResult(400, 600, [L("Konferencja o zmianach klimatu w Warszawie", 20, 20, 300, 20)], "fake")
    feats = {oa.observation_id: [feat(oa.observation_id, aa.artifact_id, ad())],
             ob.observation_id: [feat(ob.observation_id, ab.artifact_id, other)]}
    ev = OcrSentenceComparator().compare(oa, ob, feats, run_id="r")[0]
    assert ev.score is not None and ev.score < 0.2


def test_empty_ocr_is_no_basis_not_zero():
    oa, aa = obs("a")
    ob, ab = obs("b")
    feats = {oa.observation_id: [feat(oa.observation_id, aa.artifact_id, ad())],
             ob.observation_id: [feat(ob.observation_id, ab.artifact_id, OcrResult(400, 600, [], "fake"))]}
    for c in (OcrSentenceComparator(), OcrLayoutComparator(), OcrHtmlComparator()):
        ev = c.compare(oa, ob, feats, run_id="r")[0]
        assert ev.score is None and "brak podstaw" in ev.explanation
    low = {oa.observation_id: feats[oa.observation_id],
           ob.observation_id: [feat(ob.observation_id, ab.artifact_id, ad(conf=0.1))]}
    assert OcrSentenceComparator().compare(oa, ob, low, run_id="r")[0].score is None  # poniżej progu pewności


def html_secs():
    return [{"text": "Wielka wyprzedaż zimowa", "start": 100, "end": 130, "tag": "h1"},
            {"text": "Tylko dziś rabaty do 70% na całą kolekcję. Sprawdź ofertę sklepu.", "start": 200, "end": 280, "tag": "p"},
            {"text": "Kup teraz", "start": 300, "end": 320, "tag": "a"},
            {"text": "Regulamin i polityka cookies", "start": 400, "end": 440, "tag": "footer"}]


def test_match_to_html_and_quote_location():
    res = ad()
    m = match_to_html(res, html_secs())
    assert m["ocr_coverage"] > 0.95 and len(m["matches"]) == 4
    t = next(x for x in m["matches"] if x.quote.startswith("Wielka"))
    assert t.html_span == (100, 130) and t.rect == (40, 30, 320, 40)
    assert 0.0 < m["html_coverage"] < 1.0  # stopka niewidoczna na zrzucie
    # obcy HTML nie pasuje
    far = match_to_html(res, [{"text": "Przepis na szarlotkę z jabłkami i cynamonem", "start": 0, "end": 40}])
    assert far["ocr_coverage"] < 0.3


def test_locate_quote_accuracy():
    res = ad()
    rect, score = locate_quote(res, "rabaty do 70% na cala kolekcje")
    assert rect == (40, 420, 320, 18) and score >= 0.99
    rect, _ = locate_quote(res, "Wie1ka wyprzedaz")  # błąd OCR w cytacie
    assert rect == (40, 30, 320, 40)
    assert locate_quote(res, "zupełnie inny cytat który nie istnieje") is None
    assert locate_quote(OcrResult(10, 10, []), "x") is None


def test_html_comparator_evidence_leads_to_rect_and_span():
    oa, aa = obs("shot")
    ob, ab = obs("page", ArtifactRole.landing_html)
    secs = [Feature(feature_id=ids.feature_id(ob.observation_id, "html", "text_section", "t", [i]),
                    observation_id=ob.observation_id, family="html", kind="text_section", value_type=ValueType.json,
                    value={**s, "hidden": False}, extractor_version="t",
                    evidence_refs=[__import__("methadata").contracts.ArtifactRef(artifact_id=ab.artifact_id,
                                                                                   locator=TextSpan(start=s["start"], end=s["end"]))])
            for i, s in enumerate(html_secs())]
    feats = {oa.observation_id: [feat(oa.observation_id, aa.artifact_id, ad())], ob.observation_id: secs}
    ev = OcrHtmlComparator().compare(oa, ob, feats, run_id="r")[0]
    assert ev.score > 0.95
    assert any(isinstance(r.locator, ImageRect) and r.locator.y == 30 for r in ev.evidence_refs[oa.canonical_id])
    assert any(isinstance(r.locator, TextSpan) and r.locator.start == 100 for r in ev.evidence_refs[ob.canonical_id])
    assert ev.pair == tuple(sorted(ev.pair))


def test_error_impact_degrades_gracefully():
    rows = error_impact(ad(), ad(1.5))
    s = [r["score"] for r in rows]
    assert s[0] > 0.99 and s[-1] < s[0] and all(0 <= x <= 1 for x in s)
    assert rows[1]["score"] > 0.85  # 2% błędów nadal wykrywa te same zdania


def test_sentences_split_and_rects():
    ss = sentences(ad())
    texts = [s.text for s in ss]
    assert "Tylko dzis rabaty do 70% na cala kolekcje." in texts and "Sprawdz oferte sklepu." in texts
    assert next(s for s in ss if s.text.startswith("Tylko")).rect == (40, 420, 320, 18)


class Reader:
    def __init__(self, files): self.files = files
    def open_bytes(self, ref): return self.files[ref.artifact_id]


class FakeEngine:
    name, version = "fake", "1"
    def __init__(self, res): self.res = res
    def recognize(self, data):
        if data == b"boom":
            raise ValueError("uszkodzony")
        return self.res


class Store:
    def __init__(self): self.items = []
    def put_derived(self, **kw):
        self.items.append(kw)
        return Artifact(artifact_id=ids.artifact_id(kw["observation_id"], kw["role"].value, ids.sha256_bytes(kw["data"]), kw["origin"]),
                        observation_id=kw["observation_id"], role=kw["role"], local_ref="x", sha256=ids.sha256_bytes(kw["data"]))


def test_extractor_features_store_and_failure_modes():
    o, a = obs("a")
    st = Store()
    ex = OcrExtractor(FakeEngine(ad()))
    feats, sts = ex.extract(o, reader=Reader({a.artifact_id: b"img"}), optional={"store": st})
    kinds = {f.kind for f in feats}
    assert {"ocr_boxes", "ocr_text", "ocr_layout"} <= kinds and sts[0].state.value == "ok"
    assert st.items and json.loads(st.items[0]["data"])["lines"][0]["rel"][0] == pytest.approx(0.1)
    back = ocr_results(feats)
    assert back[0][0] == a.artifact_id and len(back[0][1].lines) == 3
    # pusty OCR
    feats, sts = OcrExtractor(FakeEngine(OcrResult(400, 600, []))).extract(o, reader=Reader({a.artifact_id: b"img"}))
    assert [f.kind for f in feats] == ["ocr_boxes"] and "pusty OCR" in sts[0].detail
    # awaria silnika / brak pliku
    _, sts = ex.extract(o, reader=Reader({a.artifact_id: b"boom"}))
    assert sts[0].state.value == "failed"
    _, sts = ex.extract(o, reader=Reader({}))
    assert sts[0].state.value == "missing_input"


def test_discover_returns_adapters():
    found = discover(strict=False)
    for i, k in (("ocr-layout-extractor", AdapterKind.extractor), ("ocr-sentences-v1", AdapterKind.comparator),
                 ("ocr-layout-v1", AdapterKind.comparator), ("ocr-html-match-v1", AdapterKind.comparator)):
        assert found[i].spec.kind is k


# ------------------------------------------------------------ prawdziwy OCR (pomijany bez silnika)
def _engine():
    pytest.importorskip("rapidocr_onnxruntime")
    pytest.importorskip("PIL")
    from methadata.experiments.ocr_layout import RapidOcrEngine
    return RapidOcrEngine()


def _render(scale=1):
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (int(600 * scale), int(800 * scale)), "white")
    d = ImageDraw.Draw(im)
    big, small = ImageFont.load_default(int(44 * scale)), ImageFont.load_default(int(26 * scale))
    d.text((40 * scale, 40 * scale), "MEGA PROMOCJA ZIMOWA", fill="black", font=big)
    d.rectangle((40 * scale, 150 * scale, 560 * scale, 560 * scale), fill=(120, 170, 220))
    d.text((40 * scale, 600 * scale), "Oferta ważna tylko do niedzieli w sklepach", fill="black", font=small)
    d.rectangle((200 * scale, 700 * scale, 400 * scale, 760 * scale), fill=(200, 30, 30))
    d.text((230 * scale, 712 * scale), "Kup teraz", fill="white", font=small)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_real_ocr_layout_on_rendered_ad_two_resolutions():
    eng = _engine()
    r1, r2 = eng.recognize(_render(1)), eng.recognize(_render(2))
    assert r1.lines and "PROMOCJA" in " ".join(l.text for l in r1.lines).upper()
    l1, l2 = analyze_layout(r1), analyze_layout(r2)
    assert l1["title"] and l1["button"] and l1["illustration"]
    assert layout_similarity(l1, l2) > 0.7
    rect, _ = locate_quote(r1, "MEGA PROMOCJA ZIMOWA")
    assert 25 < rect[0] < 60 and 25 < rect[1] < 70  # prostokąt na faktycznym położeniu tytułu


def _backup_shot():
    p = _REAL_BACKUP / "p1" / "Zrzut ekranu 2026-10-08 105745.png"
    if not p.is_file():
        pytest.skip("brak danych BACKUP")
    return p.read_bytes()


def test_real_backup_screenshot_self_match():
    eng = _engine()
    res = eng.recognize(_backup_shot())
    assert len(res.lines) >= 3
    rect, _ = locate_quote(res, "Dowiedz sie wiecej") or (None, 0)
    assert rect is not None
    lay = analyze_layout(res)
    assert lay["button"] and lay["button"]["text"].lower().startswith("dowiedz")
    o, a = obs("real")
    o2, a2 = obs("real2", sha_seed="real2")
    fe = OcrExtractor(eng)
    f1, _ = fe.extract(o, reader=Reader({a.artifact_id: _backup_shot()}))
    f2, _ = fe.extract(o2, reader=Reader({a2.artifact_id: _backup_shot()}))
    ev = OcrSentenceComparator().compare(o, o2, {o.observation_id: f1, o2.observation_id: f2}, run_id="r")[0]
    assert ev.score is not None and ev.score > 0.95
