from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Optional, Sequence

from methadata.contracts import AdapterKind, AdapterSpec, ArtifactRef, PairEvidence, Relation

from .text import FAMILY, KIND, LexicalTextExtractor
from .tfidf import ANALYZERS, LexicalModel, contributions, cosine, terms, vectorize

DEFAULT_WEIGHTS = {"title": 0.2, "body": 0.6, "cta": 0.2, "comments": 0.0}
EXPLAIN_TERMS = 5


def sections(features: Iterable[Any]) -> list[dict]:
    """Pola tekstowe z cech: [{field, text, refs}] w kolejności dokumentu (wg feature_id deterministycznie)."""
    out = [{"field": f.value["field"], "text": f.value["text"], "refs": list(f.evidence_refs)}
           for f in features if f.family == FAMILY and f.kind == KIND]
    return out


def field_texts(secs: Sequence[dict], fields: Optional[Sequence[str]] = None) -> dict[str, str]:
    d: dict[str, list[str]] = {}
    for s in secs:
        if fields is None or s["field"] in fields:
            d.setdefault(s["field"], []).append(s["text"])
    return {k: " \n".join(v) for k, v in d.items()}


def fit_model(feature_map: Mapping[str, Sequence[Any]], observation_ids: Optional[Iterable[str]] = None,
              meta: Optional[dict] = None) -> LexicalModel:
    """Słownik i IDF z cech wskazanych obserwacji (np. część rozwojowa)."""
    ids_ = sorted(observation_ids if observation_ids is not None else feature_map)
    return LexicalModel.fit([field_texts(sections(feature_map[i])) for i in ids_ if i in feature_map], meta=meta)


def fragment(text: str, term: str, analyzer: str, width: int = 50) -> Optional[str]:
    """Krótki fragment tekstu wokół pierwszego wystąpienia terminu (słowo lub n-gram znakowy)."""
    t = term.strip()
    m = re.search(re.escape(t), text, re.I) if t else None
    if not m:
        return None
    s, e = max(0, m.start() - width), min(len(text), m.end() + width)
    return ("…" if s else "") + text[s:e].replace("\n", " ") + ("…" if e < len(text) else "")


def _locate(secs: Sequence[dict], field: str, term: str, analyzer: str):
    for s in secs:
        if s["field"] == field and (frag := fragment(s["text"], term, analyzer)):
            return frag, s["refs"]
    return None, []


def compare_docs(sa: Sequence[dict], sb: Sequence[dict], model: LexicalModel, *, weights: Mapping[str, float],
                 hide: Sequence[str] = (), merge_comments: bool = False) -> dict:
    """Wynik pochodny: {score, fields: {pole: {word, char, score}}, terms: {pole: [{term, contribution, a, b}]}}.
    hide: pola pominięte (np. title). merge_comments: komentarze dołączone do korpusu (pomiar wpływu)."""
    def prep(secs):
        sec = [dict(s, field="body") if merge_comments and s["field"] == "comments" else s for s in secs]
        return sec, field_texts(sec)
    sa2, ta = prep(sa)
    sb2, tb = prep(sb)
    fields: dict[str, dict] = {}
    top: dict[str, list] = {}
    for f in sorted(set(ta) & set(tb)):
        if f in hide:
            continue
        per: dict[str, Optional[float]] = {}
        for an in ANALYZERS:
            m = model.get(f, an)
            va, vb = vectorize(terms(ta[f], an), m), vectorize(terms(tb[f], an), m)
            per[an] = cosine(va, vb)
            if an == "word":
                top[f] = []
                for t, c in contributions(va, vb)[:EXPLAIN_TERMS]:
                    fa, ra = _locate(sa2, f, t, an)
                    fb, rb = _locate(sb2, f, t, an)
                    top[f].append({"term": t, "contribution": c, "a": fa, "b": fb, "refs_a": ra, "refs_b": rb})
        vals = [v for v in per.values() if v is not None]
        fields[f] = {**per, "score": sum(vals) / len(vals) if vals else None}
    used = {f: weights.get(f, 0.0) for f, r in fields.items() if r["score"] is not None and weights.get(f, 0.0) > 0}
    tot = sum(used.values())
    score = sum(fields[f]["score"] * w for f, w in used.items()) / tot if tot else None
    return {"score": score, "fields": fields, "terms": top, "weights_used": used}


class LexicalTfidfComparator:
    """TF-IDF słów i znakowych n-gramów + cosinus, osobno dla title/body/cta (komentarze raportowane osobno).

    model: utrwalony LexicalModel (część rozwojowa). Bez modelu IDF liczone z cech całego przebiegu
    (`features` przekazane do compare) i jest to zaznaczone w wyjaśnieniu."""

    def __init__(self, model: Optional[LexicalModel] = None, *, method_id: str = "lexical-tfidf-v1",
                 weights: Optional[Mapping[str, float]] = None, hide: Sequence[str] = (),
                 merge_comments: bool = False):
        self.model = model
        self.weights = dict(weights or DEFAULT_WEIGHTS)
        self.hide = tuple(hide)
        self.merge_comments = merge_comments
        self._cache: tuple[Any, LexicalModel] | None = None
        self.spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version="1",
                                description="TF-IDF słów i n-gramów znakowych, cosinus osobno dla tytułu, korpusu i CTA")
        self.method_id = method_id

    def _model(self, features) -> tuple[LexicalModel, str]:
        if self.model is not None:
            return self.model, "IDF z modelu utrwalonego na części rozwojowej"
        if self._cache is None or self._cache[0] is not features:
            self._cache = (features, fit_model(features))
        return self._cache[1], "IDF z korpusu bieżącego przebiegu (brak modelu rozwojowego)"

    def compare(self, a, b, features, *, run_id, optional=None) -> Sequence[PairEvidence]:
        (ca, oa), (cb, ob) = sorted([(a.canonical_id, a), (b.canonical_id, b)], key=lambda t: t[0])
        pair = (ca, cb)
        sa, sb = sections(features.get(oa.observation_id, [])), sections(features.get(ob.observation_id, []))

        def none(why):
            return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=Relation.shared_narrative,
                                 score=None, explanation=why)]
        if not sa or not sb:
            return none("brak podstaw: jedna ze stron nie ma tekstu (po usunięciu menu i skryptów)")
        model, src = self._model(features)
        r = compare_docs(sa, sb, model, weights=self.weights, hide=self.hide, merge_comments=self.merge_comments)
        if r["score"] is None:
            return none("brak podstaw: strony nie mają wspólnego niepustego pola tekstowego z dodatnią wagą")
        refs: dict[str, list[ArtifactRef]] = {ca: [], cb: []}
        for f in r["weights_used"]:
            for t in r["terms"].get(f, []):
                refs[ca] += t["refs_a"]
                refs[cb] += t["refs_b"]
        if not refs[ca] or not refs[cb]:  # brak wspólnych terminów: dowód to sekcje danego pola
            for f in r["weights_used"]:
                for s in sa:
                    refs[ca] += s["refs"] if s["field"] == f else []
                for s in sb:
                    refs[cb] += s["refs"] if s["field"] == f else []
        if not refs[ca] or not refs[cb]:
            return none("brak podstaw: cechy tekstowe bez odwołań do artefaktów")
        for k in refs:  # bez duplikatów, stała kolejność
            seen, out = set(), []
            for x in refs[k]:
                key = x.model_dump_json()
                if key not in seen:
                    seen.add(key)
                    out.append(x)
            refs[k] = out
        return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=Relation.shared_narrative,
                             score=min(1.0, max(0.0, r["score"])), evidence_refs=refs,
                             explanation=explain(r, src, self.hide, self.merge_comments))]


def explain(r: dict, src: str, hide=(), merge_comments=False) -> str:
    parts = []
    for f, v in sorted(r["fields"].items()):
        s = lambda x: "brak" if x is None else f"{x:.3f}"
        parts.append(f"{f}: słowa {s(v['word'])}, znaki {s(v['char'])}, razem {s(v['score'])}")
    txt = f"TF-IDF cosinus {r['score']:.4f} (wagi {r['weights_used']}; {src}). Pola: " + "; ".join(parts) + "."
    if hide:
        txt += f" Ukryte pola: {', '.join(hide)}."
    if merge_comments:
        txt += " Komentarze dołączone do korpusu."
    for f in r["weights_used"]:
        for t in r["terms"].get(f, [])[:3]:
            txt += f" [{f}] „{t['term']}” (wkład {t['contribution']:.3f}): A «{t['a']}» | B «{t['b']}»."
    return txt


def get_adapters() -> list[Any]:
    return [LexicalTextExtractor(), LexicalTfidfComparator(),
            LexicalTfidfComparator(method_id="lexical-tfidf-notitle-v1", hide=("title",))]
