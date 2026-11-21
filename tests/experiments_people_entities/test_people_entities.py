from pathlib import Path

import pytest

from methadata.contracts import AdapterKind, Artifact, ArtifactRef, Feature, InputKind, Observation, ids
from methadata.contracts.models import ImageRect, Relation, TextSpan, ValueType
from methadata.contracts.registry import discover
from methadata.experiments.people_entities import (Lexicon, PeopleEntitiesExtractor, PeopleTextComparator,
                                                   PeopleVisibleComparator, ReferenceImage, default_lexicon, fold,
                                                   load_references, mentions)

BACKUP = Path("/Users/jnaklicki/Documents/Hackathon/Methadata/BACKUP")


class Reader:
    def __init__(self): self.d = {}
    def open_bytes(self, ref): return self.d[ref.artifact_id]


def make(name, html, reader, images=()):
    oid = ids.observation_id(name, "full_capture")
    data = html.encode()
    sha = ids.sha256_bytes(data)
    arts = [Artifact(artifact_id=ids.artifact_id(oid, "landing_html", sha), observation_id=oid, role="landing_html",
                     local_ref=f"{name}.html", sha256=sha, media_type="text/html")]
    reader.d[arts[0].artifact_id] = data
    for i, img in enumerate(images):
        s = ids.sha256_bytes(img)
        arts.append(Artifact(artifact_id=ids.artifact_id(oid, "resource", s, f"i{i}"), observation_id=oid,
                             role="resource", local_ref=f"{name}-{i}.jpg", sha256=s, media_type="image/jpeg"))
    return Observation(observation_id=oid, canonical_id="can-" + name, source_ref=name,
                       input_kind=InputKind.full_capture, artifacts=arts)


def page(title="T", body="", caption="", menu="", comments=""):
    return (f"<html><head><title>{title}</title></head><body><nav class='menu'><a>{menu}</a></nav>"
            f"<article><p>{body}</p><figure><img src=x><figcaption>{caption}</figcaption></figure></article>"
            f"<div class='comments'><p>{comments}</p></div></body></html>")


IMG_A = b"obraz-referencyjny-A"
REFS = {ids.sha256_bytes(IMG_A): ReferenceImage(ids.sha256_bytes(IMG_A), "person:krzysztof-stanowski",
                                                "opis ręczny w teście", True, "test")}


def feats(o, r, refs=None):
    f, st = PeopleEntitiesExtractor(references=REFS if refs is None else refs).extract(o, reader=r)
    return list(f), st


def test_lexicon_inflection_fold_longest():
    lx = default_lexicon()
    t = "Rafała Brzoski, Brzosce, brzoske; stanowskiego i Krzysztof Stanowski; Polsat News; NBP, nbp; INPOSTU"
    hits = lx.find(t)
    ids_ = [(h.entity_id, h.surface) for h in hits]
    assert ("person:rafal-brzoska", "Rafała Brzoski") in ids_           # dłuższy alias wygrywa z samym nazwiskiem
    assert ("person:rafal-brzoska", "Brzosce") in ids_ and ("person:rafal-brzoska", "brzoske") in ids_
    assert ("person:krzysztof-stanowski", "Krzysztof Stanowski") in ids_ and ("person:krzysztof-stanowski", "stanowskiego") in ids_
    assert ("brand:nbp", "NBP") in ids_ and ("brand:nbp", "nbp") not in ids_  # skrót tylko wielkimi literami
    assert ("brand:inpost", "INPOSTU") in ids_
    assert all(t[h.start:h.end] == h.surface for h in hits)
    assert len(fold("Łódź żółć")) == len("Łódź żółć")
    assert default_lexicon().find("Stanowska maraton") == []             # inna osoba/słowo


def test_roles_mention_caption_menu_and_exact_span():
    r = Reader()
    o = make("a", page("Stanowski w tytule", "Treść o Stanowskim.", "Fot. Rafał Brzoska", menu="Olejnik menu"), r)
    f, st = feats(o, r)
    by = {(x.value["entity_id"], x.value["role"], x.value["field"]) for x in f if x.kind == "mention"}
    assert ("person:krzysztof-stanowski", "mention", "title") in by
    assert ("person:krzysztof-stanowski", "mention", "body") in by
    assert ("person:rafal-brzoska", "caption", "caption") in by
    assert not any(x.value["entity_id"] == "person:monika-olejnik" for x in f)   # menu pominięte
    src = r.d[o.artifacts[0].artifact_id].decode()
    for x in f:
        ref = x.evidence_refs[0]
        assert isinstance(ref.locator, TextSpan) and x.value["span_exact"]
        assert src[ref.locator.start:ref.locator.end] == x.value["surface"]
    assert st[-1].state.value == "ok"


def test_visible_person_is_separate_evidence_type():
    r = Reader()
    o = make("v", page("Nic", "Tekst bez nazwisk"), r, images=[IMG_A, b"inny"])
    f, _ = feats(o, r)
    vis = [x for x in f if x.kind == "visible_person"]
    assert len(vis) == 1 and vis[0].value["role"] == "visible_person" and vis[0].value["label_source"]
    assert not [x for x in f if x.kind == "mention"]                      # brak wzmianki tekstowej


def test_text_only_person_has_no_visible_evidence():
    r = Reader()
    a = make("a", page("Stanowski", "Stanowski odpowiada na pytania"), r)
    b = make("b", page("Stanowski", "Stanowski i kamera"), r, images=[IMG_A])
    fm = {o.observation_id: feats(o, r)[0] for o in (a, b)}
    vis = PeopleVisibleComparator().compare(a, b, fm, run_id="r")[0]
    assert vis.score is None and "adnotacji osoby widocznej" in vis.explanation   # tylko w treści != widoczna
    txt = PeopleTextComparator().compare(a, b, fm, run_id="r")[0]
    assert txt.score is not None and txt.score > 0.5 and txt.method_id != vis.method_id


def test_visible_comparator_shared_and_provenance():
    r = Reader()
    a = make("a", page("x", "y"), r, images=[IMG_A])
    b = make("b", page("z", "w"), r, images=[IMG_A])
    fm = {o.observation_id: feats(o, r)[0] for o in (a, b)}
    ev = PeopleVisibleComparator().compare(a, b, fm, run_id="r")[0]
    assert ev.score == 1.0 and "opis ręczny w teście" in ev.explanation and "nie rozpoznawanie twarzy" in ev.explanation
    assert ev.evidence_refs[a.canonical_id] and ev.evidence_refs[b.canonical_id]


def test_surname_alone_does_not_decide_campaign():
    r = Reader()
    a = make("a", page("Stanowski pobity", "Stanowski i banki"), r)
    b = make("b", page("Krem na stopy", "Wywiad ze Stanowskim o kremie. Zamów teraz"), r)
    fm = {o.observation_id: feats(o, r)[0] for o in (a, b)}
    ev = PeopleTextComparator().compare(a, b, fm, run_id="r")[0]
    assert ev.relation is Relation.shared_narrative and ev.relation is not Relation.campaign_candidate
    assert "nie rozstrzyga o wspólnej kampanii" in ev.explanation
    # różne nazwiska nie wykluczają: wynik 0.0 z wyjaśnieniem, nie "przeciw"
    c = make("c", page("Sekielski pobity", "Sekielski i banki"), r)
    fm[c.observation_id] = feats(c, r)[0]
    ev0 = PeopleTextComparator(types=("person",)).compare(a, c, fm, run_id="r")[0]
    assert ev0.score == 0.0 and "Brak wspólnych encji" in ev0.explanation and "nie wyklucza" in ev0.explanation


def test_no_mentions_is_insufficient_not_zero():
    r = Reader()
    a = make("a", page("Zwykły tytuł", "bez nazw"), r)
    b = make("b", page("Stanowski", "x"), r)
    fm = {o.observation_id: feats(o, r)[0] for o in (a, b)}
    ev = PeopleTextComparator().compare(a, b, fm, run_id="r")[0]
    assert ev.score is None and "brak podstaw" in ev.explanation


def test_optional_ocr_mentions_when_available():
    r = Reader()
    a = make("a", page("t", "b"), r)
    b = make("b", page("t", "b"), r)
    art_a, art_b = a.artifacts[0].artifact_id, b.artifacts[0].artifact_id

    def ocr(o, art, text):
        v = {"width": 100, "height": 50, "engine": "t", "engine_version": "1",
             "lines": [{"text": text, "conf": 0.9, "rect": [1, 2, 30, 10], "rel": [0.01, 0.04, 0.3, 0.2]}]}
        return Feature(feature_id="ftr-" + o.canonical_id, observation_id=o.observation_id, family="ocr",
                       kind="ocr_boxes", value_type=ValueType.json, value=v, extractor_version="ocr-layout-1",
                       evidence_refs=[ArtifactRef(artifact_id=art, locator=ImageRect(x=1, y=2, width=30, height=10))])
    fm = {a.observation_id: [ocr(a, art_a, "Rafal Brzoska zatrzymany")], b.observation_id: [ocr(b, art_b, "BRZOSKI")]}
    m = mentions(fm[a.observation_id])
    assert m and m[0].source == "ocr" and m[0].entity_id == "person:rafal-brzoska"   # OCR bez diakrytyków
    ev = PeopleTextComparator().compare(a, b, fm, run_id="r")[0]
    assert ev.score == 1.0 and isinstance(ev.evidence_refs[a.canonical_id][0].locator, ImageRect)


def test_reference_label_source_required(tmp_path):
    p = tmp_path / "r.json"
    p.write_text('{"images": [{"sha256": "' + "a" * 64 + '", "entity_id": "person:x"}]}')
    with pytest.raises(ValueError):
        load_references(p)
    assert all(v.label_source for v in load_references().values())


def test_lexicon_custom_and_duplicates():
    lx = Lexicon([{"entity_id": "brand:x", "type": "brand", "canonical": "X", "aliases": ["Foo Bar"]}])
    assert lx.find("a foo  bar b")[0].entity_id == "brand:x"
    with pytest.raises(ValueError):
        Lexicon([{"entity_id": "a", "type": "zły", "canonical": "A", "aliases": ["a"]}])


def test_discover_registers():
    found = discover()
    assert {"people-entities-extractor", "people-text-mention-v1", "people-text-persons-v1",
            "people-visible-person-v1"} <= set(found)
    assert found["people-text-mention-v1"].spec.kind is AdapterKind.comparator


# ------------------------------------------------------------------ dane rzeczywiste (BACKUP)

def real(name):
    d = BACKUP / name
    r = Reader()
    html = next(d.glob("*.html"))
    files = next(d.glob("*_files"), None)
    imgs = sorted(files.glob("*.jpg")) + sorted(files.glob("*.webp")) if files else []
    o = make(name, html.read_text(encoding="utf-8", errors="replace"), r, images=[p.read_bytes() for p in imgs])
    return o, r


@pytest.mark.skipif(not BACKUP.is_dir(), reason="brak katalogu BACKUP")
def test_real_p2_p3_p4_surnames_marked_in_source_and_not_decisive():
    out = {}
    for n in ("p2", "p3", "p4"):
        o, r = real(n)
        f, st = feats(o, r, refs=load_references())
        out[n] = (o, r, f)
    for n, entity, surname in (("p2", "person:rafal-brzoska", "Brzosk"), ("p3", "person:krzysztof-stanowski", "Stanowsk"),
                               ("p4", "person:tomasz-sekielski", "Sekielsk")):
        o, r, f = out[n]
        ms = [x for x in f if x.kind == "mention" and x.value["entity_id"] == entity]
        assert ms, n
        src = r.d[o.artifacts[0].artifact_id].decode("utf-8", "replace")
        titles = [x for x in ms if x.value["field"] == "title"]
        assert titles and surname in fold(src[titles[0].evidence_refs[0].locator.start:titles[0].evidence_refs[0].locator.end])
        assert all(surname in fold(src[x.evidence_refs[0].locator.start:x.evidence_refs[0].locator.end])
                   for x in ms if x.value["span_exact"])
    fm = {out[n][0].observation_id: out[n][2] for n in out}
    p3, p4 = out["p3"][0], out["p4"][0]
    persons = PeopleTextComparator(types=("person",)).compare(p3, p4, fm, run_id="r")[0]
    assert persons.score == 0.0          # różne nazwiska, a obie strony to ten sam szablon (dowód z E01/F04)
    assert persons.relation is Relation.shared_narrative
    # p2: osoby widoczne tylko tam, gdzie jest obraz referencyjny; p3 wspomina osobę jedynie w treści
    vis = PeopleVisibleComparator().compare(out["p2"][0], p3, fm, run_id="r")[0]
    assert vis.score is None
    assert any(x.kind == "visible_person" for x in out["p2"][2])
    assert not any(x.kind == "visible_person" for x in out["p3"][2])
