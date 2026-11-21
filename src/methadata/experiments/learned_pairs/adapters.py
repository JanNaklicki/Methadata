"""Komparator wyuczony: łączy wyniki komparatorów bazowych wyuczonym modelem i zwraca PairEvidence."""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from methadata.contracts import AdapterKind, AdapterSpec, PairEvidence, Relation

from .model import Logit, featurize

METHOD_ID = "learned-pairs-logit-v1"


class LearnedPairComparator:
    """base: komparatory bazowe (compare(a,b,features,run_id=...)); model: Logit z cechami sim:/__missing__: po method_id."""
    method_id = METHOD_ID
    spec = AdapterSpec(id=METHOD_ID, kind=AdapterKind.comparator, version="1",
                       description="Regresja logistyczna na jawnych podobieństwach i wskaźnikach braków")

    def __init__(self, model: Optional[Logit] = None, base: Sequence[Any] = (), methods: Sequence[str] = (),
                 relation: Relation = Relation.shared_template):
        self.model, self.base, self.methods, self.relation = model, list(base), list(methods), relation

    def compare(self, a, b, features, *, run_id, optional=None) -> Sequence[PairEvidence]:
        pair = tuple(sorted((a.canonical_id, b.canonical_id)))
        if self.model is None:
            return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=self.relation, score=None,
                                 explanation="brak podstaw: model nie został wyuczony (za mało ocen obu klas)")]
        scores: dict[str, Optional[float]] = {}
        refs: dict[str, list] = {pair[0]: [], pair[1]: []}
        for c in self.base:
            for ev in c.compare(a, b, features, run_id=run_id, optional=optional):
                if ev.relation != self.relation:
                    continue
                scores[ev.method_id] = ev.score
                if ev.score is not None:
                    for k, v in ev.evidence_refs.items():
                        refs[k] += [r for r in v if r not in refs[k]]
        x = featurize(scores, self.methods)
        if not (refs[pair[0]] and refs[pair[1]]):
            return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=self.relation, score=None,
                                 explanation="brak podstaw: żadna metoda bazowa nie dała dowodów po obu stronach")]
        p = self.model.proba(x)
        why = "; ".join(f"{n}={c:+.2f}" for n, c in self.model.contributions(x)[:4])
        return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=self.relation, score=p,
                             evidence_refs=refs, explanation=f"P(podobne)={p:.3f}; wkład cech do logitu: {why}")]


def get_adapters() -> list[Any]:
    """Bez modelu komparator zwraca „brak podstaw”; runner składa go z wyuczonym modelem (patrz README modułu)."""
    return [LearnedPairComparator()]
