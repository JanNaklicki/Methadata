"""Wyszukiwanie powtarzalnych kombinacji rzadkich śladów (zasób + formularz + tekst), minimalny podpis grupy, ablacja."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import combinations
from typing import Iterable, Optional, Sequence

from .matrix import TraceMatrix
from .traces import KINDS


@dataclass(frozen=True)
class MotifConfig:
    rare_fraction: float = 0.1   # ślad rzadki: df <= max(2, rare_fraction * N)
    min_support: int = 2         # minimalna liczba materiałów z całą kombinacją
    max_size: int = 3
    min_kinds: int = 2           # 2 = kombinacja musi łączyć co najmniej dwa rodzaje śladów; 1 dopuszcza pojedynczy ślad
    top_k: int = 200

    def max_df(self, n: int) -> int:
        return max(2, int(math.floor(self.rare_fraction * n)))

    def to_dict(self) -> dict:
        return {"rare_fraction": self.rare_fraction, "min_support": self.min_support, "max_size": self.max_size,
                "min_kinds": self.min_kinds, "top_k": self.top_k}


@dataclass
class Motif:
    traces: tuple
    kinds: tuple
    materials: tuple
    weight: float            # Σ idf składników
    lift: Optional[float]    # support / (N·Π df/N)
    component_df: dict
    equivalent: dict = field(default_factory=dict)  # trace_id -> liczba śladów o identycznym wsparciu zwiniętych w reprezentanta

    @property
    def support(self) -> int:
        return len(self.materials)

    def to_dict(self) -> dict:
        return {"traces": list(self.traces), "kinds": list(self.kinds), "materials": list(self.materials),
                "support": self.support, "weight": self.weight, "lift": self.lift,
                "component_df": self.component_df, "equivalent": self.equivalent}


def _classes(m: TraceMatrix, ids: Iterable[str]) -> dict[tuple, list[str]]:
    """Ślady o identycznym (rodzaj, wsparcie) są nierozróżnialne dla kombinacji: zwijamy je w klasy."""
    post = m.postings()
    cl: dict[tuple, list[str]] = {}
    for t in sorted(ids):
        cl.setdefault((m.traces[t][0], post[t]), []).append(t)
    return cl


def mine_motifs(m: TraceMatrix, cfg: MotifConfig = MotifConfig()) -> list[Motif]:
    post = m.postings()
    max_df = cfg.max_df(m.n)
    rare = [t for t in m.traces if cfg.min_support <= len(post.get(t, ())) <= max_df]
    cl = _classes(m, rare)
    reps = sorted((v[0], k[0], k[1], len(v)) for k, v in cl.items())  # (rep, kind, posting, size)
    # klasy po materiale, by pary szukać tylko wśród współwystępujących
    by_mat: dict[str, list[int]] = {}
    for i, (_, _, p, _) in enumerate(reps):
        for c in p:
            by_mat.setdefault(c, []).append(i)

    def make(idx: tuple, mats: frozenset) -> Motif:
        ts = tuple(reps[i][0] for i in idx)
        st = m.combination_stats(ts)
        return Motif(traces=ts, kinds=tuple(reps[i][1] for i in idx), materials=tuple(sorted(mats)),
                     weight=st["weight"], lift=st["lift"], component_df=st["component_df"],
                     equivalent={reps[i][0]: reps[i][3] for i in idx if reps[i][3] > 1})

    level: dict[tuple, frozenset] = {}
    near: dict[int, set] = {}
    for mats in by_mat.values():
        for i, j in combinations(mats, 2):
            near.setdefault(i, set()).add(j)
    for i, js in near.items():
        for j in js:
            if i < j and reps[i][1] != reps[j][1]:
                inter = reps[i][2] & reps[j][2]
                if len(inter) >= cfg.min_support:
                    level[(i, j)] = inter
    found = dict(level)
    if cfg.min_kinds <= 1:
        found.update({(i,): reps[i][2] for i in range(len(reps)) if len(reps[i][2]) >= cfg.min_support})
    size = 2
    while level and size < cfg.max_size:
        nxt: dict[tuple, frozenset] = {}
        for idx, mats in level.items():
            kinds = {reps[i][1] for i in idx}
            for k in near.get(idx[0], ()):
                if k > idx[-1] and reps[k][1] not in kinds and all(k in near.get(i, ()) for i in idx):
                    inter = mats & reps[k][2]
                    if len(inter) >= cfg.min_support:
                        nxt[idx + (k,)] = inter
        found.update(nxt)
        level, size = nxt, size + 1
    # domknięcie: nadzbiór o tym samym wsparciu wypiera podzbiór
    keep = []
    byset: dict[frozenset, list[tuple]] = {}
    for idx, mats in found.items():
        byset.setdefault(mats, []).append(idx)
    for mats, idxs in byset.items():
        for idx in idxs:
            if not any(set(idx) < set(o) for o in idxs):
                keep.append((idx, mats))
    motifs = [make(i, mt) for i, mt in keep]
    motifs.sort(key=lambda x: (-x.weight, -x.support, x.traces))
    return motifs[:cfg.top_k]


# ---------------------------------------------------------------- grupa: minimalny podpis i ablacja

@dataclass
class GroupSignature:
    group: tuple
    common: tuple                 # wszystkie ślady wspólne grupie (po wykluczeniach)
    signature: tuple              # minimalny zestaw śladów odróżniający grupę
    outside_each: dict            # ślad -> liczba wystąpień poza grupą (materiałów)
    outside_support: tuple        # materiały spoza grupy, które mają cały podpis
    labels: dict = field(default_factory=dict)
    excluded: tuple = ()

    @property
    def distinguishes(self) -> bool:
        return bool(self.signature) and not self.outside_support

    def to_dict(self) -> dict:
        return {"group": list(self.group), "common_count": len(self.common), "signature": list(self.signature),
                "outside_each": self.outside_each, "outside_support_count": len(self.outside_support),
                "outside_support": list(self.outside_support), "distinguishes": self.distinguishes,
                "labels": self.labels, "excluded": list(self.excluded)}


def common_traces(m: TraceMatrix, group: Sequence[str], exclude: Iterable[str] = ()) -> list[str]:
    if len(group) < 1:
        return []
    ex = set(exclude)
    sets = [set(m.rows[g]) for g in group]
    return sorted(set.intersection(*sets) - ex)


def minimal_signature(m: TraceMatrix, group: Sequence[str], *, exclude: Iterable[str] = (), max_size: int = 4,
                      beam: int = 25) -> GroupSignature:
    """Najmniejszy podzbiór śladów wspólnych grupie, którego koniunkcja ma najmniejsze wsparcie poza grupą.
    Przeszukanie pełne dla rozmiarów <= max_size wśród `beam` śladów o najmniejszej liczbie wystąpień poza grupą;
    dalej zachłannie. Ślady o identycznym wsparciu są równoważne – bierzemy najmniejszy trace_id."""
    group = tuple(sorted(group))
    gset = set(group)
    common = common_traces(m, group, exclude)
    post = m.postings()
    out_each = {t: len(post[t] - gset) for t in common}
    reps: dict[frozenset, str] = {}
    for t in common:
        reps.setdefault(post[t], t)
    cand = sorted(reps.values(), key=lambda t: (out_each[t], t))
    labels = {t: m.traces[t][1] for t in common}
    if not cand:
        return GroupSignature(group, tuple(common), (), out_each, tuple(sorted(set(m.rows) - gset)), labels, tuple(sorted(exclude)))
    outside_all = frozenset.intersection(*[post[t] for t in cand]) - gset
    target = len(outside_all)

    def outside(ts) -> frozenset:
        return frozenset.intersection(*[post[t] for t in ts]) - gset

    best = None
    top = cand[:beam]
    for k in range(1, min(max_size, len(top)) + 1):
        hits = [c for c in combinations(top, k) if len(outside(c)) == target]
        if hits:
            best = min(hits, key=lambda c: (sum(out_each[t] for t in c), c))
            break
    if best is None:  # zachłannie
        chosen, cur = [], None
        rest = list(cand)
        while rest and (cur is None or len(cur) > target):
            t = min(rest, key=lambda t: (len(outside(chosen + [t])), out_each[t], t))
            chosen.append(t)
            rest.remove(t)
            cur = outside(chosen)
        best = tuple(chosen)
    best = tuple(sorted(best, key=lambda t: (out_each[t], t)))
    return GroupSignature(group, tuple(common), best, out_each, tuple(sorted(outside(best))), labels, tuple(sorted(exclude)))


def rarest_trace(m: TraceMatrix, traces: Sequence[str]) -> Optional[str]:
    """Najrzadszy w korpusie (najmniejsze df; remis: najmniejszy trace_id)."""
    return min(traces, key=lambda t: (m.df(t), t)) if traces else None


def ablate_rarest(m: TraceMatrix, group: Sequence[str], **kw) -> dict:
    """Wynik przed i po usunięciu najrzadszego śladu wspólnego grupie (z podpisu, a gdy podpis pusty – ze wspólnych)."""
    full = minimal_signature(m, group, **kw)
    victim = rarest_trace(m, full.signature or full.common)
    reduced = minimal_signature(m, group, exclude=[victim] if victim else (), **kw)
    return {"removed": victim, "removed_label": m.traces[victim][1] if victim else None,
            "removed_df": m.df(victim) if victim else None, "full": full, "reduced": reduced,
            "outside_support_delta": len(reduced.outside_support) - len(full.outside_support),
            "still_distinguishes": reduced.distinguishes,
            "signature_changed": reduced.signature != full.signature}
