import math
import re

import pytest

from methadata.contracts import AdapterKind, Artifact, InputKind, Observation, ids
from methadata.contracts.registry import discover
from methadata.experiments.lexical import LexicalTextExtractor
from methadata.experiments.semantic import (BackendUnavailable, CallableBackend, EmbeddingCache,
                                            SemanticParagraphComparator, SentenceTransformerBackend, match_field)
from methadata.experiments.semantic import report

# Syntetyczne "pojęcia": każdy token mapuje na wymiar znaczenia (PL/EN razem), nie jest to model rzeczywisty.
CONCEPTS = {
    "gold": ["złoto", "zlota", "gold", "złoto,"], "money": ["zarabiaj", "5000", "earn", "thousand", "five", "zloty", "zł", "dochód"],
    "invest": ["inwestując", "inwestowanie", "investing", "inwestycji"], "home": ["domu", "couch", "home"],
    "foot": ["stóp", "stopy", "feet", "foot"], "fungus": ["grzybicę", "grzyba", "grzybica", "fungus"],
    "week": ["tydzień", "siedmiu", "dniach", "week"], "weight": ["schudnięcie", "kilo", "kilos", "lose", "ten"],
    "doctor": ["lekarze", "doctors", "lekarza"], "secret": ["sposób", "trick", "hide", "znał"],
    "risk": ["ryzykowny", "stracić", "zakład"], "cta": ["kup", "buy", "zamów"], "cream": ["krem", "preparat", "kremy"],
    "month": ["miesięcznie", "month", "miesiąc", "weeks"],
}
DIMS = sorted(CONCEPTS)


def toy(texts):
    out = []
    for t in texts:
        toks = re.findall(r"\w+", t.lower())
        out.append([float(sum(1 for w in toks if w in CONCEPTS[c])) for c in DIMS])
    return out


BACKEND = CallableBackend(toy, "toy-concepts", "1")


def test_paraphrase_recovers_original_and_topic_pair():
    r = report.controlled(BACKEND)
    assert r["recovered_n"] == r["of"] == 3
    assert 0 <= r["top_topic_pair"]["cosine"] <= 1


def test_cache_by_content_and_model():
    calls = []
    be = CallableBackend(lambda ts: (calls.append(len(ts)), toy(ts))[1], "m", "1")
    c = EmbeddingCache(be)
    c.embed(["złoto", "złoto", "krem"])
    c.embed(["złoto"])
    assert calls == [2] and c.hits == 1 and c.misses == 2
    be2 = CallableBackend(toy, "m", "2")
    assert EmbeddingCache(be2).key("x") != c.key("x")


def test_cache_dir_roundtrip(tmp_path):
    EmbeddingCache(BACKEND, tmp_path).embed(["złoto domu"])
    boom = CallableBackend(lambda ts: (_ for _ in ()).throw(AssertionError("no recompute")), "toy-concepts", "1")
    assert EmbeddingCache(boom, tmp_path).embed(["złoto domu"]) == [toy(["złoto domu"])[0]]


def test_match_field_best_pairs():
    sa = [{"text": "złoto inwestycji", "refs": []}, {"text": "krem stopy", "refs": []}]
    sb = [{"text": "krem grzyba", "refs": []}, {"text": "gold investing", "refs": []}]
    r = match_field(sa, sb, EmbeddingCache(BACKEND))
    assert r["pairs"][0]["cosine"] > 0.5 and math.isfinite(r["score"])
    assert match_field([], sb, EmbeddingCache(BACKEND)) is None


class Reader:
    def __init__(self): self.d = {}
    def open_bytes(self, ref): return self.d[ref.artifact_id]


def build(pages):
    r, obs, fm = Reader(), [], {}
    for name, html in pages.items():
        oid = ids.observation_id(name, "full_capture")
        data = html.encode()
        sha = ids.sha256_bytes(data)
        a = Artifact(artifact_id=ids.artifact_id(oid, "landing_html", sha), observation_id=oid, role="landing_html",
                     local_ref=f"{name}.html", sha256=sha, media_type="text/html")
        r.d[a.artifact_id] = data
        o = Observation(observation_id=oid, canonical_id="can-" + name, source_ref=name,
                        input_kind=InputKind.full_capture, artifacts=[a])
        fm[oid] = list(LexicalTextExtractor().extract(o, reader=r)[0])
        obs.append(o)
    return obs, fm


def pg(title, body, cta):
    return f"<html><head><title>{title}</title></head><body><article><p>{body}</p></article><button>{cta}</button></body></html>"


PAGES = {
    "a": pg("Zarabiaj na złocie", "Zarabiaj 5000 zł miesięcznie inwestując w złoto", "Kup teraz"),
    "b": pg("Earn with gold", "Earn five thousand zloty a month investing in gold", "Buy now"),
    "c": pg("Krem na stopy", "Krem na grzybicę stóp działa w tydzień", "Zamów krem"),
}


def test_comparator_evidence_and_ranking():
    obs, fm = build(PAGES)
    a, b, c = obs
    cmp = SemanticParagraphComparator(BACKEND)
    ab, ac = cmp.compare(a, b, fm, run_id="r")[0], cmp.compare(a, c, fm, run_id="r")[0]
    assert ab.score > ac.score and 0 <= ab.score <= 1
    assert ab.evidence_refs[a.canonical_id] and ab.evidence_refs[b.canonical_id]
    assert "title:" in ab.explanation and "body:" in ab.explanation and "cta:" in ab.explanation
    assert cmp.compare(a, b, fm, run_id="r")[0] == ab


def test_unsupported_backend_gives_none_with_status():
    def fail(ts): raise BackendUnavailable("brak lokalnych wag")
    obs, fm = build(PAGES)
    ev = SemanticParagraphComparator(CallableBackend(fail, "x", "1")).compare(obs[0], obs[1], fm, run_id="r")[0]
    assert ev.score is None and ev.explanation.startswith("unsupported")


def test_default_backend_without_weights_is_unsupported(tmp_path):
    obs, fm = build(PAGES)
    be = SentenceTransformerBackend(cache_dir=str(tmp_path), local_only=True)
    ev = SemanticParagraphComparator(be).compare(obs[0], obs[1], fm, run_id="r")[0]
    assert ev.score is None and "unsupported" in ev.explanation


def test_discover():
    found = discover()
    assert found["semantic-minilm-v1"].spec.kind is AdapterKind.comparator


def test_real_model_if_available(tmp_path):
    pytest.importorskip("sentence_transformers")
    be = SentenceTransformerBackend(cache_dir=str(tmp_path), local_only=True)
    try:
        r = report.controlled(be)
    except BackendUnavailable:
        pytest.skip("brak lokalnych wag modelu")
    assert r["recovered_n"] >= 2
