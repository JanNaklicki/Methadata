"""Metryki par (§6.4). Decyzja: True = w tej samej grupie, False = rozdzielone, None = wstrzymanie (abstencja)."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping, Optional

from ..contracts import AssignmentStatus, ClusterRun, PairEvidence
from .labels import DISPUTED, NO, UNKNOWN, YES, Pair

Decision = Optional[bool]
NO_BASIS = None  # mianownik 0 -> „brak podstaw do wyliczenia”


@dataclass
class AxisMetrics:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    abstained_positive: int = 0   # pozytywna para bez decyzji; wliczona do FN (obniża recall)
    abstained_negative: int = 0   # negatywna para bez decyzji; nie jest ani FP, ani TN
    evaluated: int = 0            # pary z referencją tak/nie
    unknown: int = 0              # pary z referencją „nie wiadomo” lub spornych: poza oceną
    unlabeled_predicted: int = 0  # pary z decyzją modelu, lecz bez referencji
    precision: Optional[float] = None
    recall: Optional[float] = None
    f1: Optional[float] = None
    coverage: Optional[float] = None  # część ocenionych par, dla których podjęto decyzję

    def to_dict(self) -> dict:
        return asdict(self)


def _div(n: int, d: int) -> Optional[float]:
    return n / d if d else NO_BASIS


def axis_metrics(reference: Mapping[Pair, str], predicted: Mapping[Pair, Decision],
                 pairs: Optional[set] = None) -> AxisMetrics:
    """`pairs` ogranicza ocenę do podzbioru (np. część rozwojowa). Brak pary w `predicted` = wstrzymanie."""
    m = AxisMetrics()
    for pair, ref in reference.items():
        if pairs is not None and pair not in pairs:
            continue
        if ref in (UNKNOWN, DISPUTED):
            m.unknown += 1
            continue
        if ref not in (YES, NO):
            raise ValueError(f"nieznana etykieta referencji {ref!r} dla {pair}")
        m.evaluated += 1
        d = predicted.get(pair)
        if ref == YES:
            if d is True:
                m.tp += 1
            else:
                m.fn += 1
                if d is None:
                    m.abstained_positive += 1
        else:
            if d is True:
                m.fp += 1
            elif d is False:
                m.tn += 1
            else:
                m.abstained_negative += 1
    m.unlabeled_predicted = sum(1 for p, d in predicted.items()
                                if d is not None and (pairs is None or p in pairs) and p not in reference)
    m.precision = _div(m.tp, m.tp + m.fp)
    m.recall = _div(m.tp, m.tp + m.fn)
    if m.precision is not None and m.recall is not None:
        s = m.precision + m.recall
        m.f1 = 2 * m.precision * m.recall / s if s else 0.0
    decided = m.evaluated - m.abstained_positive - m.abstained_negative
    m.coverage = _div(decided, m.evaluated)
    return m


def decisions_from_run(run: ClusterRun, pairs: Optional[set] = None) -> dict[Pair, Decision]:
    """Para jest „razem”, gdy materiały mają wspólny cluster_id. Brak materiału w przebiegu lub
    status insufficient_data u któregokolwiek -> None. Singleton i szum są decyzją (rozdzielenie)."""
    by = {a.material_id: a for a in run.assignments}

    def decide(a: str, b: str) -> Decision:
        x, y = by.get(a), by.get(b)
        if x is None or y is None:
            return None
        if AssignmentStatus.insufficient_data in (x.status, y.status):
            return None
        return bool(set(x.cluster_ids) & set(y.cluster_ids))

    if pairs is None:
        ids = sorted(by)
        pairs = {(a, b) for i, a in enumerate(ids) for b in ids[i + 1:]}
    return {p: decide(*p) for p in pairs}


def decisions_from_pairs(evidence: list[PairEvidence], threshold: float, relation: str | None = None) -> dict[Pair, Decision]:
    """score None -> None; w przeciwnym razie score >= threshold. Przy wielu dowodach pary max score."""
    best: dict[Pair, Decision] = {}
    for e in evidence:
        if relation and e.relation.value != relation:
            continue
        p = tuple(e.pair)
        if e.score is None:
            best.setdefault(p, None)
        else:
            cur = best.get(p)
            best[p] = (e.score >= threshold) or bool(cur)
    return best


def cluster_stats(run: ClusterRun) -> dict:
    sizes: dict[str, int] = {}
    for a in run.assignments:
        for c in a.cluster_ids:
            sizes[c] = sizes.get(c, 0) + 1
    cnt = lambda s: sum(1 for a in run.assignments if a.status is s)
    return {"materials": len(run.assignments), "groups": len(sizes),
            "largest_group": max(sizes.values(), default=0),
            "singletons": cnt(AssignmentStatus.singleton), "noise": cnt(AssignmentStatus.noise),
            "insufficient_data": cnt(AssignmentStatus.insufficient_data)}
