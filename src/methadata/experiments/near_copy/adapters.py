"""E05: bliskie kopie tekstu. Dokładny Jaccard k-shingles i zawieranie krótszego tekstu w dłuższym na polu
`body` (cechy E02/lexical). MinHash/LSH służy wyłącznie do wyboru kandydatów (patrz `bench.py`), nie zmienia wyniku."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from methadata.contracts import AdapterKind, AdapterSpec, ArtifactRef, PairEvidence, Relation
from methadata.experiments.lexical.adapters import sections

from .shingle import DEFAULT_K, containment, jaccard, shingles


class NearCopyComparator:
    """measure='jaccard' (symetryczne, kara za dopisany tekst) albo 'containment' (krótszy w dłuższym).
    Para bez tekstu w polu -> score=None (nie 0). evidence_refs: sekcje ze wspólnymi shingles."""

    def __init__(self, measure: str = "jaccard", k: int = DEFAULT_K, field: str = "body"):
        if measure not in ("jaccard", "containment"):
            raise ValueError(measure)
        self.measure, self.k, self.field = measure, k, field
        self.method_id = f"near-copy-{measure}-v1"
        self.spec = AdapterSpec(id=self.method_id, kind=AdapterKind.comparator, version="1",
                                description=f"Dokładne podobieństwo shingles ({k} słów): {measure}, pole {field}")

    def _sh(self, secs):
        per = [(shingles(s["text"], self.k), s["refs"]) for s in secs if s["field"] == self.field]
        whole = shingles(" \n".join(s["text"] for s in secs if s["field"] == self.field), self.k) if per else set()
        return whole, per

    def compare(self, a, b, features, *, run_id, optional: Mapping[str, Any] | None = None) -> Sequence[PairEvidence]:
        (ca, oa), (cb, ob) = sorted([(a.canonical_id, a), (b.canonical_id, b)], key=lambda t: t[0])
        pair = (ca, cb)

        def none(why):
            return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=Relation.shared_narrative,
                                 score=None, explanation=why)]
        sa, sb = sections(features.get(oa.observation_id, [])), sections(features.get(ob.observation_id, []))
        wa, pa = self._sh(sa)
        wb, pb = self._sh(sb)
        if not wa or not wb:
            return none(f"brak podstaw: pole {self.field} puste po jednej ze stron lub po obu")
        common = wa & wb
        j, c = jaccard(wa, wb), containment(wa, wb)
        score = j if self.measure == "jaccard" else c
        refs: dict[str, list[ArtifactRef]] = {ca: [], cb: []}
        for cid, per in ((ca, pa), (cb, pb)):
            hit = [r for sh, r in per if sh & common] or [r for _, r in per]
            seen = set()
            for rr in hit:
                for x in rr:
                    k = x.model_dump_json()
                    if k not in seen:
                        seen.add(k)
                        refs[cid].append(x)
        if not refs[ca] or not refs[cb]:
            return none("brak podstaw: cechy tekstowe bez odwołań do artefaktów")
        shorter = "A" if len(wa) <= len(wb) else "B"
        expl = (f"{self.measure} shingles po {self.k} słów (pole {self.field}): {score:.4f}; "
                f"Jaccard {j:.4f}, zawieranie krótszego ({shorter}) w dłuższym {c:.4f}; "
                f"wspólnych shingles {len(common)} z {len(wa)} (A) i {len(wb)} (B). Wynik dokładny (bez MinHash).")
        return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=Relation.shared_narrative,
                             score=min(1.0, max(0.0, score)), evidence_refs=refs, explanation=expl)]


def get_adapters() -> list[Any]:
    # ekstraktor tekstu (lexical-text-extractor) dostarcza moduł E02
    return [NearCopyComparator("jaccard"), NearCopyComparator("containment")]
