from methadata.devtools.realdata import BACKUP as _REAL_BACKUP
from pathlib import Path

import pytest

from methadata.contracts import AdapterKind, Artifact, InputKind, Observation, ids
from methadata.contracts.registry import discover
from methadata.experiments.lexical import (LexicalModel, LexicalTextExtractor, LexicalTfidfComparator, fit_model,
                                           sections)
from methadata.experiments.lexical.tfidf import cosine

BACKUP_ABS = _REAL_BACKUP


class Reader:
    def __init__(self): self.d = {}
    def open_bytes(self, ref): return self.d[ref.artifact_id]


def page(title="T", body="", menu="", cta="", comments="", script="var x=1;"):
    return (f"<html><head><title>{title}</title><script>{script}</script></head><body>"
            f"<nav class='main-menu'><a>{menu}</a></nav><article><p>{body}</p></article>"
            f"<button>{cta}</button><div class='comments'><p>{comments}</p></div></body></html>")


def make(name, html, reader):
    oid = ids.observation_id(name, "full_capture")
    data = html.encode()
    sha = ids.sha256_bytes(data)
    a = Artifact(artifact_id=ids.artifact_id(oid, "landing_html", sha), observation_id=oid, role="landing_html",
                 local_ref=f"{name}.html", sha256=sha, media_type="text/html")
    reader.d[a.artifact_id] = data
    return Observation(observation_id=oid, canonical_id="can-" + name, source_ref=name,
                       input_kind=InputKind.full_capture, artifacts=[a])


def extract(o, r):
    f, st = LexicalTextExtractor().extract(o, reader=r)
    return list(f), st


def test_fields_menu_script_removed():
    r = Reader()
    o = make("a", page("Tytuł oferty", "Treść artykułu o zarabianiu", "Strona główna kontakt", "Kup teraz", "Świetny komentarz"), r)
    f, st = extract(o, r)
    by = {}
    for s in sections(f):
        by.setdefault(s["field"], []).append(s["text"])
    assert by["title"] == ["Tytuł oferty"] and by["body"] == ["Treść artykułu o zarabianiu"]
    assert by["cta"] == ["Kup teraz"] and by["comments"] == ["Świetny komentarz"]
    alltext = " ".join(t for v in by.values() for t in v)
    assert "Strona główna" not in alltext and "var x" not in alltext
    assert st[-1].state.value == "ok" and all(x.evidence_refs for x in f)


def build(pages):
    r = Reader()
    obs = [make(n, h, r) for n, h in pages.items()]
    fm = {o.observation_id: extract(o, r)[0] for o in obs}
    return obs, fm


PAGES = {
    "a": page("Trudna odpowiedź", "dochód miesięczny z inwestycji w złoto wynosi tysiące złotych", cta="Sprawdź ofertę", comments="Działa świetnie polecam"),
    "b": page("Trudna odpowiedź", "dochód miesięczny z inwestycji w złoto to tysiące złotych", cta="Sprawdź ofertę teraz", comments="Nie działa"),
    "c": page("Krem na stopy", "grzybica stóp leczenie kremem w tydzień bez recepty", cta="Zamów krem", comments="Super krem"),
}


def test_similarity_and_evidence():
    obs, fm = build(PAGES)
    a, b, c = obs
    cmp = LexicalTfidfComparator()
    ab = cmp.compare(a, b, fm, run_id="r")[0]
    ac = cmp.compare(a, c, fm, run_id="r")[0]
    assert ab.score > ac.score and 0 <= ab.score <= 1
    assert "dochód" in ab.explanation and "«" in ab.explanation  # termin + fragmenty obu stron
    assert ab.evidence_refs[a.canonical_id] and ab.evidence_refs[b.canonical_id]
    assert "body:" in ab.explanation and "title:" in ab.explanation
    assert cmp.compare(a, b, fm, run_id="r")[0] == ab  # deterministyczne


def test_hide_title_changes_score():
    obs, fm = build({"a": PAGES["a"], "b": page("Trudna odpowiedź", "zupełnie inny tekst o samochodach", cta="Kup auto")})
    a, b = obs
    s1 = LexicalTfidfComparator().compare(a, b, fm, run_id="r")[0].score
    s2 = LexicalTfidfComparator(hide=("title",)).compare(a, b, fm, run_id="r")[0].score
    assert s1 > s2


def test_comments_reported_separately_and_merge():
    obs, fm = build(PAGES)
    a, b, _ = obs
    base = LexicalTfidfComparator().compare(a, b, fm, run_id="r")[0]
    assert "comments:" in base.explanation
    merged = LexicalTfidfComparator(merge_comments=True).compare(a, b, fm, run_id="r")[0]
    assert merged.score != base.score


def test_no_text_is_none_not_zero():
    r = Reader()
    a = make("a", "<html><body><nav>menu</nav><script>x</script></body></html>", r)
    b = make("b", PAGES["a"], r)
    fm = {o.observation_id: extract(o, r)[0] for o in (a, b)}
    ev = LexicalTfidfComparator().compare(a, b, fm, run_id="r")[0]
    assert ev.score is None


def test_model_roundtrip_deterministic():
    obs, fm = build(PAGES)
    m = fit_model(fm, [o.observation_id for o in obs[:2]], meta={"split": "dev"})
    m2 = LexicalModel.from_json(m.to_json())
    assert m2.to_json() == m.to_json() and m2.meta["split"] == "dev"
    a, b, _ = obs
    s1 = LexicalTfidfComparator(m).compare(a, b, fm, run_id="r")[0]
    s2 = LexicalTfidfComparator(m2).compare(a, b, fm, run_id="r")[0]
    assert s1.score == s2.score and "modelu utrwalonego" in s1.explanation
    with pytest.raises(ValueError):
        LexicalModel.from_json('{"version": "x"}')


def test_cosine_empty_is_none():
    assert cosine({}, {"a": 1.0}) is None


def test_discover():
    found = discover()
    assert found["lexical-text-extractor"].spec.kind is AdapterKind.extractor
    assert found["lexical-tfidf-v1"].spec.kind is AdapterKind.comparator
    assert "lexical-tfidf-notitle-v1" in found


@pytest.mark.skipif(not BACKUP_ABS.is_dir(), reason="brak katalogu BACKUP")
def test_real_p8_neighbor_stable_without_title():
    from methadata.experiments.lexical.report import measure
    res = measure(BACKUP_ABS, "p8")
    for key in ("with_title", "title_hidden"):
        assert res[key][0]["id"] == "p11"
    assert res["with_title"][0]["score"] >= res["title_hidden"][0]["score"] - 0.05
