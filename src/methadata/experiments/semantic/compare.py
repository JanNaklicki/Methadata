"""Podobieństwo semantyczne akapitów: najlepsze dopasowania (maks. cosinus) osobno dla tytułu, obietnicy (body) i CTA."""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from methadata.contracts import AdapterKind, AdapterSpec, ArtifactRef, PairEvidence, Relation
from methadata.experiments.lexical.adapters import sections

from .backend import (DEFAULT_MODEL, BackendUnavailable, EmbeddingBackend, EmbeddingCache, SentenceTransformerBackend,
                      cosine)

FIELDS = ("title", "body", "cta")
DEFAULT_WEIGHTS = {"title": 0.2, "body": 0.6, "cta": 0.2}
MIN_CHARS = 2
TOP_K = 3


def match_field(sa: Sequence[dict], sb: Sequence[dict], cache: EmbeddingCache, top_k: int = TOP_K) -> Optional[dict]:
    """Symetryczne dopasowanie akapitów: dla każdego akapitu maks. cosinus do drugiej strony, średnia obu kierunków.
    Zwraca {score, pairs:[{a,b,cosine,refs_a,refs_b}]} (najlepsze pary malejąco) lub None bez tekstu."""
    sa = [s for s in sa if len(s["text"].strip()) >= MIN_CHARS]
    sb = [s for s in sb if len(s["text"].strip()) >= MIN_CHARS]
    if not sa or not sb:
        return None
    va, vb = cache.embed([s["text"] for s in sa]), cache.embed([s["text"] for s in sb])
    sim = [[cosine(x, y) for y in vb] for x in va]
    if any(c is None for r in sim for c in r):
        return None
    best_a = [max(range(len(sb)), key=lambda j: sim[i][j]) for i in range(len(sa))]
    best_b = [max(range(len(sa)), key=lambda i: sim[i][j]) for j in range(len(sb))]
    score = (sum(sim[i][best_a[i]] for i in range(len(sa))) / len(sa)
             + sum(sim[best_b[j]][j] for j in range(len(sb))) / len(sb)) / 2
    cand = {(i, best_a[i]) for i in range(len(sa))} | {(best_b[j], j) for j in range(len(sb))}
    pairs = sorted(cand, key=lambda ij: (-sim[ij[0]][ij[1]], ij))[:top_k]
    return {"score": score,
            "pairs": [{"a": sa[i]["text"], "b": sb[j]["text"], "cosine": sim[i][j],
                       "refs_a": sa[i]["refs"], "refs_b": sb[j]["refs"]} for i, j in pairs]}


def compare_secs(sa: Sequence[dict], sb: Sequence[dict], cache: EmbeddingCache, *,
                 weights: Mapping[str, float] = DEFAULT_WEIGHTS, top_k: int = TOP_K) -> dict:
    fields = {}
    for f in FIELDS:
        r = match_field([s for s in sa if s["field"] == f], [s for s in sb if s["field"] == f], cache, top_k)
        if r is not None:
            fields[f] = r
    used = {f: weights.get(f, 0.0) for f in fields if weights.get(f, 0.0) > 0}
    tot = sum(used.values())
    score = sum(fields[f]["score"] * w for f, w in used.items()) / tot if tot else None
    return {"score": score, "fields": fields, "weights_used": used}


def _dedupe(refs: list[ArtifactRef]) -> list[ArtifactRef]:
    seen, out = set(), []
    for x in refs:
        k = x.model_dump_json()
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


class SemanticParagraphComparator:
    """Embeddingi akapitów (pola tekstowe z E02/X01) + cosinus; osobno title/body/cta.

    Wymaga cech `lexical/text_field` (lexical-text-extractor). Brak backendu/wag -> score=None, wyjaśnienie
    `unsupported: ...` (nie zero)."""

    def __init__(self, backend: Optional[EmbeddingBackend] = None, *, method_id: str = "semantic-minilm-v1",
                 weights: Optional[Mapping[str, float]] = None, cache_dir: Optional[str] = None, top_k: int = TOP_K):
        self.backend = backend or SentenceTransformerBackend(local_only=False)
        self.cache = EmbeddingCache(self.backend, cache_dir)
        self.weights = dict(weights or DEFAULT_WEIGHTS)
        self.top_k = top_k
        self.method_id = method_id
        self.spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version="1",
                                description="Wielojęzyczne embeddingi akapitów, cosinus, najlepsze dopasowania; "
                                            "osobno tytuł, obietnica i CTA")

    def compare(self, a, b, features, *, run_id, optional=None) -> Sequence[PairEvidence]:
        (ca, oa), (cb, ob) = sorted([(a.canonical_id, a), (b.canonical_id, b)], key=lambda t: t[0])
        pair = (ca, cb)
        sa, sb = sections(features.get(oa.observation_id, [])), sections(features.get(ob.observation_id, []))

        def none(why):
            return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=Relation.shared_narrative,
                                 score=None, explanation=why)]
        if not sa or not sb:
            return none("brak podstaw: jedna ze stron nie ma tekstu")
        try:
            r = compare_secs(sa, sb, self.cache, weights=self.weights, top_k=self.top_k)
        except BackendUnavailable as e:
            return none(f"unsupported: {e}; model {self.backend.model_id}")
        if r["score"] is None:
            return none("brak podstaw: brak wspólnego niepustego pola title/body/cta")
        refs: dict[str, list[ArtifactRef]] = {ca: [], cb: []}
        for f in r["weights_used"]:
            for p in r["fields"][f]["pairs"]:
                refs[ca] += p["refs_a"]
                refs[cb] += p["refs_b"]
        refs = {k: _dedupe(v) for k, v in refs.items()}
        if not refs[ca] or not refs[cb]:
            return none("brak podstaw: cechy tekstowe bez odwołań do artefaktów")
        return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=Relation.shared_narrative,
                             score=min(1.0, max(0.0, r["score"])), evidence_refs=refs, explanation=explain(r, self.backend))]


def explain(r: dict, backend) -> str:
    parts = [f"{f}: {v['score']:.3f}" for f, v in sorted(r["fields"].items())]
    txt = (f"Cosinus embeddingów akapitów {r['score']:.4f} (model {backend.model_id}@{backend.revision}; "
           f"wagi {r['weights_used']}). Pola: " + "; ".join(parts) + ".")
    for f in r["weights_used"]:
        for p in r["fields"][f]["pairs"][:2]:
            txt += f" [{f}] cos {p['cosine']:.3f}: A «{p['a'][:80]}» | B «{p['b'][:80]}»."
    return txt


def get_adapters() -> list[Any]:
    return [SemanticParagraphComparator()]
