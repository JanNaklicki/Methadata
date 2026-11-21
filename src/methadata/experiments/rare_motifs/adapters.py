"""Komparator E14: PairEvidence z najlepszej wspólnej kombinacji rzadkich śladów (≥2 rodzaje)."""
from __future__ import annotations

import math
from itertools import combinations
from typing import Any, Mapping, Optional, Sequence

from methadata.contracts import AdapterKind, AdapterSpec, PairEvidence, Relation

from .matrix import TraceMatrix
from .motifs import MotifConfig
from .traces import TraceConfig, extract_traces

VERSION = "1"
METHOD_ID = "rare-motifs-v1"


class RareMotifComparator:
    """Para dostaje wynik z najcięższej wspólnej kombinacji rzadkich śladów różnych rodzajów:
    W = Σ ln(N/df) składników, score = 1 - exp(-W / ln N). Pojedynczy wspólny ślad nie jest kombinacją (score 0).
    Wymaga korpusu (TraceMatrix) – częstości liczone na tym samym zbiorze co macierz; bez niego score=None.
    Runner F03 powinien zbudować komparator przez RareMotifComparator.fit(observations, feature_map)."""
    method_id = METHOD_ID
    spec = AdapterSpec(id=METHOD_ID, kind=AdapterKind.comparator, version=VERSION,
                       description="Rzadkie kombinacje zasób+formularz+tekst ważone częstością składników (E14)")

    def __init__(self, matrix: Optional[TraceMatrix] = None, motif_cfg: MotifConfig = MotifConfig(),
                 trace_cfg: TraceConfig = TraceConfig()):
        self.matrix, self.motif_cfg, self.trace_cfg = matrix, motif_cfg, trace_cfg

    @classmethod
    def fit(cls, observations, feature_map, motif_cfg: MotifConfig = MotifConfig(), trace_cfg: TraceConfig = TraceConfig(),
            dataset_version: str = "") -> "RareMotifComparator":
        return cls(TraceMatrix.build(observations, feature_map, trace_cfg, dataset_version), motif_cfg, trace_cfg)

    def compare(self, a, b, features, *, run_id, optional=None) -> Sequence[PairEvidence]:
        (ca, oa), (cb, ob) = sorted([(a.canonical_id, a), (b.canonical_id, b)], key=lambda t: t[0])
        pair = (ca, cb)

        def none(why):
            return [PairEvidence(run_id=run_id, pair=pair, method_id=METHOD_ID, relation=Relation.shared_template,
                                 score=None, explanation=why)]

        m = self.matrix
        if m is None or m.n < 3:
            return none("brak podstaw: komparator wymaga korpusu >= 3 materiałów (częstości składników)")
        ta = extract_traces(oa.observation_id, features.get(oa.observation_id, []), self.trace_cfg)
        tb = extract_traces(ob.observation_id, features.get(ob.observation_id, []), self.trace_cfg)
        if not ta or not tb:
            return none("brak podstaw: jedna ze stron nie ma śladów (zasób/formularz/tekst)")
        max_df = self.motif_cfg.max_df(m.n)
        # df z korpusu; ślad spoza korpusu liczymy jako jednorazowy (df=1), a przy obu stronach co najmniej 2
        df = lambda t: max(m.df(t), 2 if (t in ta and t in tb) else 1)
        shared = sorted(t for t in set(ta) & set(tb) if df(t) <= max_df)
        # najcięższa kombinacja: po jednym najrzadszym śladzie z każdego rodzaju (kombinacja maksymalna rodzajowo)
        best: dict[str, str] = {}
        for t in shared:
            k = ta[t][0]
            if k not in best or (df(t), t) < (df(best[k]), best[k]):
                best[k] = t
        idf = lambda t: math.log(m.n / df(t))
        pick = sorted(best.values())
        refs = lambda tr, ts: [r for t in ts for r in tr[t][2].refs]
        if pick and len(pick) >= self.motif_cfg.min_kinds:
            W = sum(idf(t) for t in pick)
            score = 1 - math.exp(-W / math.log(m.n))
            joint = m.joint_df(pick)
            expl = (f"Wspólna kombinacja {len(pick)} rzadkich śladów (N={m.n}, wsparcie koniunkcji w korpusie={joint}, "
                    f"waga Σln(N/df)={W:.3f}): " + "; ".join(f"{ta[t][0]} df={df(t)} «{ta[t][1][:70]}»" for t in pick) + ".")
            ra, rb = refs(ta, pick), refs(tb, pick)
        else:
            score = 0.0
            expl = ("Brak wspólnej kombinacji rzadkich śladów różnych rodzajów"
                    + (f" (wspólny pojedynczy ślad {ta[pick[0]][0]} df={df(pick[0])} nie jest kombinacją)." if pick else "."))
            ra, rb = refs(ta, sorted(ta)), refs(tb, sorted(tb))
        if not ra or not rb:
            return none("brak podstaw: ślady bez odwołań do artefaktów")
        return [PairEvidence(run_id=run_id, pair=pair, method_id=METHOD_ID, relation=Relation.shared_template,
                             score=score, evidence_refs={ca: ra, cb: rb}, explanation=expl)]


def get_adapters() -> list[Any]:
    return [RareMotifComparator()]
