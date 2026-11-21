"""Serie pojawień jednego wzorca na kolejnych domenach oraz porównanie z modelem bez czasu."""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Iterable, Mapping, Optional, Sequence
from urllib.parse import urlsplit

from .times import DEFAULT_TZ, Interval, build_interval

VERSION = "1"
DEFAULT_CONFIG = {
    "timezone": DEFAULT_TZ,
    "pattern_kinds": [["asset", "image_sha256"]],   # (family, kind) cech tworzących wzorzec
    "domain_kinds": [["url", "domain"], ["url", "host"], ["network", "host"]],
    "max_gap_days": 14.0,          # największy odstęp między kolejnymi startami w jednej serii
    "min_domains": 3,              # minimum różnych domen w serii
    "coactivity_cap": 0.2,         # sufit wyniku pary z samą zbieżnością okresu (bez wspólnego wzorca)
    "pattern_base": 0.5,           # wynik pary ze wspólnym wzorcem i dowolnym odstępem
}


def cfg(over: Optional[Mapping[str, Any]] = None) -> dict:
    c = dict(DEFAULT_CONFIG)
    c.update(over or {})
    return c


def host_of(value: Any) -> str:
    v = str(value or "").strip().lower()
    if "//" in v:
        v = urlsplit(v).hostname or ""
    return v.removeprefix("www.").strip(".")


@dataclass
class Item:
    observation_id: str
    canonical_id: str
    domain: str
    interval: Interval
    patterns: frozenset
    refs: list = field(default_factory=list)   # ArtifactRef do cech dat


def _vals(features, kinds):
    ks = {tuple(k) for k in kinds}
    return [f for f in features if (f.family, f.kind) in ks and f.value not in (None, "")]


def make_item(obs, features, config: Optional[Mapping[str, Any]] = None):
    """-> (Item | None, powód). Daty z cech S01 (payer/delivery_start|delivery_stop); czas pobrania tylko kontrolnie."""
    c = cfg(config)
    st = [f for f in features if f.family == "payer" and f.kind == "delivery_start"]
    sp = [f for f in features if f.family == "payer" and f.kind == "delivery_stop"]
    if not st:
        return None, "brak delivery_start (metadane publikacji S01)"
    iv, why = build_interval(str(st[0].value), str(sp[0].value) if sp else None, obs.capture_time, c["timezone"])
    if iv is None:
        return None, why
    doms = _vals(features, c["domain_kinds"])
    dom = host_of(doms[0].value) if doms else ""
    pats = frozenset(str(f.value) for f in _vals(features, c["pattern_kinds"]))
    refs = [r for f in st[:1] + sp[:1] for r in f.evidence_refs]
    return Item(obs.observation_id, obs.canonical_id, dom, iv, pats, refs), ""


@dataclass
class Series:
    pattern: str
    members: list           # Item, w kolejności startu
    kind: str               # "sequential" (kolejne starty) | "simultaneous" (wszystkie okresy wspólne w jednym punkcie)

    def to_dict(self) -> dict:
        return {"pattern": self.pattern, "kind": self.kind, "domains": [m.domain for m in self.members],
                "canonical_ids": [m.canonical_id for m in self.members],
                "starts": [m.interval.start.isoformat().replace("+00:00", "Z") for m in self.members]}


def detect_series(items: Iterable[Item], config: Optional[Mapping[str, Any]] = None) -> list[Series]:
    """Dla każdego wzorca: pojawienia na różnych domenach (pierwsze pojawienie na domenie) uporządkowane wg startu
    i cięte tam, gdzie odstęp między kolejnymi startami > max_gap_days. Seria = >= min_domains domen."""
    c = cfg(config)
    by: dict[str, dict[str, Item]] = {}
    for it in sorted(items, key=lambda i: (i.interval.start, i.canonical_id)):
        if not it.domain:
            continue
        for p in it.patterns:
            by.setdefault(p, {}).setdefault(it.domain, it)
    out: list[Series] = []
    for p in sorted(by):
        seq = sorted(by[p].values(), key=lambda i: (i.interval.start, i.domain))
        run: list[Item] = []
        for it in seq + [None]:
            if it is not None and (not run or (it.interval.start - run[-1].interval.start).total_seconds() / 86400 <= c["max_gap_days"]):
                run.append(it)
                continue
            if len(run) >= c["min_domains"]:
                last_start = max(m.interval.start for m in run)
                first_end = min(m.interval.end for m in run)
                out.append(Series(p, run, "simultaneous" if last_start < first_end else "sequential"))
            run = [it] if it is not None else []
    return out


def pair_model(a: Item, b: Item, config: Optional[Mapping[str, Any]] = None) -> dict:
    """Wynik pary w modelu z czasem. shared = wspólne wzorce; bez nich zbieżność okresu daje najwyżej coactivity_cap."""
    c = cfg(config)
    shared = sorted(a.patterns & b.patterns)
    ov, gap = a.interval.overlap_ratio(b.interval), a.interval.gap_days(b.interval)
    start_gap = abs((a.interval.start - b.interval.start).total_seconds()) / 86400
    if not shared:
        return {"score": round(c["coactivity_cap"] * ov, 6), "shared": [], "overlap": ov, "gap_days": gap, "start_gap_days": start_gap}
    prox = 1.0 if ov > 0 or gap <= 0 else 2.718281828 ** (-gap / max(c["max_gap_days"], 1e-9))
    return {"score": round(c["pattern_base"] + (1 - c["pattern_base"]) * prox, 6), "shared": shared, "overlap": ov,
            "gap_days": gap, "start_gap_days": start_gap}


def compare_models(items: Sequence[Item], config: Optional[Mapping[str, Any]] = None,
                   truth: Optional[Mapping[tuple, bool]] = None) -> dict:
    """Model bez czasu: para połączona <=> wspólny wzorzec. Model z czasem: połączona <=> wspólny wzorzec i obie reklamy
    w jednej serii wzorca (odstęp startów <= max_gap_days w łańcuchu). Raport: pary rozcięte przez czas, serie, i - gdy
    podano truth {(canonical_a, canonical_b): bool} - precyzja/pokrycie obu modeli na parach z etykietą."""
    c = cfg(config)
    items = sorted(items, key=lambda i: i.canonical_id)
    # składowe czasowe: dla wzorca łańcuchy kolejnych startów (wszystkie ogłoszenia, także na tej samej domenie)
    link_time: set = set()
    link_free: set = set()
    for p in sorted({p for i in items for p in i.patterns}):
        seq = sorted([i for i in items if p in i.patterns], key=lambda i: (i.interval.start, i.canonical_id))
        for x, y in combinations(seq, 2):
            link_free.add(tuple(sorted((x.canonical_id, y.canonical_id))))
        chain = [seq[0]] if seq else []
        for prev, it in zip(seq, seq[1:]):
            if (it.interval.start - prev.interval.start).total_seconds() / 86400 > c["max_gap_days"]:
                chain = []
            chain.append(it)
            # wszyscy z bieżącego łańcucha łączą się z it tranzytywnie
            for m in chain[:-1]:
                link_time.add(tuple(sorted((m.canonical_id, it.canonical_id))))
        # ścieżka łańcucha jest tranzytywna, więc pary w obrębie łańcucha już dodane
    cut = sorted(link_free - link_time)
    rep: dict[str, Any] = {"version": VERSION, "config": c, "pairs_no_time": len(link_free), "pairs_time": len(link_time),
                           "pairs_cut_by_time": [list(p) for p in cut], "series": [s.to_dict() for s in detect_series(items, c)]}
    if truth:
        def pr(links):
            lab = [(p, v) for p, v in truth.items()]
            tp = sum(1 for p, v in lab if v and tuple(sorted(p)) in links)
            fp = sum(1 for p, v in lab if not v and tuple(sorted(p)) in links)
            pos = sum(1 for _, v in lab if v)
            return {"tp": tp, "fp": fp, "precision": tp / (tp + fp) if tp + fp else None, "recall": tp / pos if pos else None}
        rep["no_time"], rep["with_time"] = pr(link_free), pr(link_time)
    return rep
