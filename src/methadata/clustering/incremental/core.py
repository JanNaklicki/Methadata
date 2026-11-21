"""K14: przyrostowe dołączanie materiału do wersjonowanego indeksu klastrów.

Nowy materiał jest porównywany z reprezentantem każdej jednostki (klastra lub singletona) i z kilkoma jej członkami
(farthest-first, więc próbka obejmuje całą rozpiętość klastra). Porównania idą przez fuzję K09 (`fuse_pair`), więc
obowiązują te same progi, pokrycie i wsparcie co w pełnym przeliczeniu. Jednostka przyjmuje materiał, gdy
  * pokrycie próbki (udział par z wynikiem) >= coverage_min ORAZ
  * udział par przyjętych (status ok, wynik >= threshold) >= min_fraction.
Brak takiej jednostki: singleton (porównania wykonano, nic nie pasuje) albo insufficient_data (słabe pokrycie wszędzie
= brak przypisania). Indeks jest niemutowalny: dołączenie zwraca nowy indeks (kolejna wersja) i nowy ClusterRun.
Przyrostowe dołączanie nie scala istniejących klastrów; kandydaci, których pełne przeliczenie by scaliło, trafiają
do dowodów (`also_qualifying`), a rozbieżność mierzy `compare_with_full`.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Callable, Mapping, Optional, Sequence

from methadata.clustering.fusion.core import (
    RELATION, ST_OK, fuse_and_cluster, fuse_pair, resolve_config as resolve_fusion, to_pair_evidence,
)
from methadata.contracts import (
    AdapterKind, AdapterSpec, Assignment, AssignmentStatus, ClusterRun, Edge, Observation, PairEvidence,
)

METHOD_ID = "incremental-attach"
METHOD_VERSION = "1"

# (nowy materiał, istniejący członek) -> surowe PairEvidence komparatorów składowych dla tej pary
Comparer = Callable[[str, str], Sequence[PairEvidence]]


def _digest(value: Any, n: int = 12) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:n]


def _cluster_id(members: Sequence[str]) -> str:
    """Ten sam wzór co K01, więc identyczna grupa ma identyfikator jak w pełnym przeliczeniu."""
    return "cl-" + hashlib.sha256("\x1f".join(sorted(members)).encode()).hexdigest()[:12]


def resolve_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Konfiguracja fuzji (K09) + parametry przyrostu. Wymaga feature_version (wersji cech/podobieństw)."""
    fusion = resolve_fusion(config["fusion"] if "fusion" in config else config, for_run=False)
    fv = config.get("feature_version")
    if not isinstance(fv, str) or not fv.strip():
        raise ValueError("config.feature_version jest wymagane (wejście musi wskazywać wersję cech lub podobieństw)")
    sample = config.get("sample_size", 2)
    if isinstance(sample, bool) or not isinstance(sample, int) or sample < 0:
        raise ValueError("config.sample_size musi być liczbą całkowitą >= 0")
    out = {"fusion": fusion, "feature_version": fv, "sample_size": sample}
    for key, default in (("min_fraction", 0.5), ("coverage_min", fusion["coverage_min"])):
        v = config.get(key, default)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.0 < v <= 1.0:
            raise ValueError(f"config.{key} poza zakresem (0, 1]")
        out[key] = float(v)
    return out


@dataclass(frozen=True)
class ProbeEvidence:
    member_id: str
    role: str                     # representative | member
    status: str                   # status fuzji K09 (ok, no_evidence, insufficient_coverage, insufficient_support)
    score: Optional[float]
    accepted: bool
    pair_evidence_id: Optional[str]
    edge: Optional[Edge] = None   # krawędź dowodowa, gdy para przyjęta


@dataclass(frozen=True)
class UnitEvidence:
    unit_id: str                  # cluster_id albo materiał singletona
    size: int
    representative_id: str
    probes: tuple[ProbeEvidence, ...]
    coverage: float
    accepted_fraction: float
    mean_accepted_score: Optional[float]
    qualifies: bool
    members: tuple[str, ...] = ()


@dataclass(frozen=True)
class AttachEvidence:
    material_id: str
    decision: str                 # joined_cluster | formed_pair | singleton | no_assignment
    reason: str
    cluster_id: Optional[str]
    representative_id: Optional[str]
    units: tuple[UnitEvidence, ...]
    also_qualifying: tuple[str, ...]   # inne jednostki, które pełne przeliczenie by scaliło (nieobsłużone przyrostowo)
    comparisons: int                   # liczba porównanych par (każda przez wszystkie rodziny)


@dataclass(frozen=True)
class VersionedIndex:
    index_id: str
    version: int
    parent_id: Optional[str]
    feature_version: str
    dataset_version: str
    config: Mapping[str, Any]
    observations: tuple[Observation, ...]
    run: ClusterRun
    pairs: tuple[PairEvidence, ...]    # sfuzowane pary znane indeksowi (do wyboru reprezentanta)

    @property
    def materials(self) -> tuple[str, ...]:
        return tuple(sorted(o.canonical_id for o in self.observations))

    def groups(self) -> list[tuple[str, ...]]:
        by: dict[str, list[str]] = {}
        for a in self.run.assignments:
            for c in a.cluster_ids:
                by.setdefault(c, []).append(a.material_id)
        return sorted(tuple(sorted(v)) for v in by.values())


@dataclass(frozen=True)
class AttachResult:
    index: VersionedIndex
    run: ClusterRun
    evidence: AttachEvidence
    config: Mapping[str, Any]
    pairs: tuple[PairEvidence, ...]    # sfuzowane PairEvidence par użytych jako dowody przypisania


def _index_id(feature_version, dataset_version, mats, run_id, pair_ids) -> str:
    return "idx-" + _digest([feature_version, dataset_version, list(mats), run_id, sorted(pair_ids)])


def build_index(observations: Sequence[Observation], raw_pairs: Sequence[PairEvidence], config: Mapping[str, Any],
                *, seed: int = 0) -> VersionedIndex:
    """Wersja 0 indeksu: pełna fuzja K09 + K01 na podanych obserwacjach (pary spoza zbioru są pomijane)."""
    cfg = resolve_config(config)
    mats = {o.canonical_id for o in observations}
    fcfg = {**cfg["fusion"], "dataset_version": _dataset(config)}
    pairs = [p for p in raw_pairs if set(p.pair) <= mats]
    res = fuse_and_cluster(list(observations), pairs, fcfg, seed=seed)
    return VersionedIndex(
        index_id=_index_id(cfg["feature_version"], fcfg["dataset_version"], sorted(mats), res.run.run_id,
                           [p.pair_evidence_id for p in res.pairs]),
        version=0, parent_id=None, feature_version=cfg["feature_version"], dataset_version=fcfg["dataset_version"],
        config=cfg, observations=tuple(sorted(observations, key=lambda o: o.canonical_id)), run=res.run,
        pairs=res.pairs)


def _dataset(config: Mapping[str, Any]) -> str:
    dv = config.get("dataset_version") or (config.get("fusion") or {}).get("dataset_version")
    if not isinstance(dv, str) or not dv.strip():
        raise ValueError("config.dataset_version jest wymagane")
    return dv


def _units(index: VersionedIndex) -> list[tuple[str, list[str]]]:
    by: dict[str, list[str]] = {}
    for a in index.run.assignments:
        if a.cluster_ids:
            by.setdefault(a.cluster_ids[0], []).append(a.material_id)
        elif a.status is AssignmentStatus.singleton:
            by[a.material_id] = [a.material_id]
    return sorted((k, sorted(v)) for k, v in by.items())


def _sample(members: Sequence[str], pairs: Mapping[tuple[str, str], float], size: int) -> list[str]:
    """Reprezentant = najbardziej centralny członek (suma wyników wewnątrz jednostki), reszta farthest-first."""
    if len(members) == 1:
        return list(members)

    def sim(a, b):
        return pairs.get((min(a, b), max(a, b)), 0.0)

    rep = max(members, key=lambda m: (sum(sim(m, o) for o in members if o != m), [-ord(c) for c in m]))
    chosen = [rep]
    while len(chosen) < min(size + 1, len(members)):
        rest = [m for m in members if m not in chosen]
        far = max(rest, key=lambda m: (min(1.0 - sim(m, c) for c in chosen), [-ord(c) for c in m]))
        chosen.append(far)
    return chosen


def attach(index: VersionedIndex, observation: Observation, compare: Comparer, *, seed: int = 0,
           dataset_version: Optional[str] = None) -> AttachResult:
    """Dodaje materiał do indeksu: zwraca nowy indeks, nowy ClusterRun i dowody przypisania."""
    cfg = index.config
    fcfg = cfg["fusion"]
    new = observation.canonical_id
    if new in index.materials:
        raise ValueError(f"materiał {new} jest już w indeksie")
    score_of = {p.pair: p.score for p in index.pairs if p.score is not None}
    fused_pairs: dict[tuple[str, str], PairEvidence] = {}
    run_tag = "inc-" + _digest([index.index_id, new])
    units, comparisons = [], 0
    for unit_id, members in _units(index):
        probed = _sample(members, score_of, cfg["sample_size"])
        probes = []
        for m in probed:
            pair = (min(new, m), max(new, m))
            pf = fuse_pair(pair, list(compare(new, m)), fcfg)
            comparisons += 1
            pe = to_pair_evidence(pf, fcfg, run_tag)
            fused_pairs[pair] = pe
            acc = pf.status == ST_OK and pf.score is not None and pf.score >= fcfg["threshold"]
            edge = Edge(source=pair[0], target=pair[1], relation=RELATION, pair_evidence_id=pe.pair_evidence_id,
                        weight=pf.score) if acc else None
            probes.append(ProbeEvidence(m, "representative" if m == probed[0] else "member", pf.status, pf.score,
                                        acc, pe.pair_evidence_id, edge))
        n = len(probes)
        coverage = sum(p.score is not None for p in probes) / n
        frac = sum(p.accepted for p in probes) / n
        accepted = [p.score for p in probes if p.accepted]
        qualifies = coverage + 1e-12 >= cfg["coverage_min"] and frac + 1e-12 >= cfg["min_fraction"] and bool(accepted)
        units.append(UnitEvidence(unit_id, len(members), probed[0], tuple(probes), round(coverage, 6),
                                  round(frac, 6), round(sum(accepted) / len(accepted), 6) if accepted else None,
                                  qualifies, tuple(members)))
    qualifying = sorted((u for u in units if u.qualifies),
                        key=lambda u: (-u.mean_accepted_score, -u.size, u.unit_id))
    members_of = dict(_units(index))
    if qualifying:
        best = qualifying[0]
        joined = sorted([*members_of[best.unit_id], new])
        cid = _cluster_id(joined)
        decision = "joined_cluster" if best.size > 1 else "formed_pair"
        reason = (f"jednostka {best.unit_id}: pokrycie {best.coverage}, udział przyjętych {best.accepted_fraction} "
                  f"(próbka {len(best.probes)} z {best.size})")
        if len(qualifying) > 1:
            reason += f"; także {len(qualifying) - 1} innych jednostek spełnia warunek (pełne przeliczenie mogłoby scalić)"
        ev_cluster, ev_rep = cid, best.representative_id
    else:
        covered = any(u.coverage + 1e-12 >= cfg["coverage_min"] for u in units)
        if covered:
            decision, reason = "singleton", "porównania wykonano, żadna jednostka nie spełnia progu przyjęcia"
        else:
            decision = "no_assignment"
            reason = ("słabe pokrycie: " + ("brak jednostek w indeksie" if not units else
                                            "żadna próbka nie ma wystarczającej liczby par z wynikiem"))
        ev_cluster, ev_rep, joined, best = None, None, None, None
    evidence = AttachEvidence(new, decision, reason, ev_cluster, ev_rep, tuple(units),
                              tuple(u.unit_id for u in qualifying[1:]), comparisons)

    # nowy ClusterRun
    assignments = {a.material_id: a for a in index.run.assignments}
    edges = {(e.source, e.target): e for e in index.run.edges}
    if best is not None:
        for m in joined:
            assignments[m] = Assignment(material_id=m, cluster_ids=[ev_cluster], status=AssignmentStatus.grouped)
        for p in best.probes:
            if p.edge:
                edges[(p.edge.source, p.edge.target)] = p.edge
    else:
        st = AssignmentStatus.singleton if decision == "singleton" else AssignmentStatus.insufficient_data
        assignments[new] = Assignment(material_id=new, status=st)
    dv = dataset_version or f"{index.dataset_version}+add:{new}"
    run_cfg = {"incremental": {"feature_version": cfg["feature_version"], "sample_size": cfg["sample_size"],
                               "min_fraction": cfg["min_fraction"], "coverage_min": cfg["coverage_min"],
                               "parent_index_id": index.index_id, "parent_run_id": index.run.run_id,
                               "added": new, "decision": decision},
               "fusion": {k: v for k, v in fcfg.items() if k != "dataset_version"}}
    pair_ids = sorted(p.pair_evidence_id for p in fused_pairs.values())
    run = ClusterRun(
        run_id="run-incremental-" + _digest([index.index_id, new, run_cfg, seed, pair_ids]),
        dataset_version=dv, method_id=METHOD_ID, method_version=METHOD_VERSION, config=run_cfg, seed=seed,
        relation=RELATION, assignments=sorted(assignments.values(), key=lambda a: a.material_id),
        edges=[edges[k] for k in sorted(edges)])
    obs = tuple(sorted([*index.observations, observation], key=lambda o: o.canonical_id))
    new_index = VersionedIndex(
        index_id=_index_id(cfg["feature_version"], dv, [o.canonical_id for o in obs], run.run_id, pair_ids),
        version=index.version + 1, parent_id=index.index_id, feature_version=cfg["feature_version"],
        dataset_version=dv, config=cfg, observations=obs, run=run,
        pairs=tuple(sorted([*index.pairs, *fused_pairs.values()], key=lambda p: p.pair_evidence_id)))
    return AttachResult(new_index, run, evidence, run_cfg, tuple(fused_pairs[k] for k in sorted(fused_pairs)))


# ------------------------------------------------------------------ pełne przeliczenie i porównanie

class PairBank:
    """Surowe PairEvidence całego korpusu jako Comparer z licznikiem wywołań (koszt)."""

    def __init__(self, raw: Sequence[PairEvidence]):
        self._by: dict[tuple[str, str], list[PairEvidence]] = {}
        for p in raw:
            self._by.setdefault(p.pair, []).append(p)
        self.calls = 0

    def __call__(self, a: str, b: str) -> Sequence[PairEvidence]:
        self.calls += 1
        return self._by.get((min(a, b), max(a, b)), [])

    def subset(self, mats) -> list[PairEvidence]:
        s = set(mats)
        return [p for ps in self._by.values() for p in ps if set(p.pair) <= s]


def _pair_set(groups) -> set[tuple[str, str]]:
    return {p for g in groups for p in combinations(sorted(g), 2)}


def jaccard(a, b) -> Optional[float]:
    left, right = _pair_set(a), _pair_set(b)
    return len(left & right) / len(left | right) if left | right else None


def compare_with_full(result: AttachResult, bank: PairBank, observations: Sequence[Observation],
                      *, seed: int = 0) -> dict[str, Any]:
    """Porównuje wynik przyrostowy z pełną fuzją na tym samym zbiorze (indeks + nowy materiał)."""
    idx = result.index
    full = fuse_and_cluster(list(observations), bank.subset(idx.materials),
                            {**idx.config["fusion"], "dataset_version": idx.dataset_version}, seed=seed)
    inc_groups = idx.groups()
    full_groups = sorted(tuple(c.members) for c in full.clusters)
    new = result.evidence.material_id
    n = len(idx.materials)
    g_inc = next((g for g in inc_groups if new in g), (new,))
    g_full = next((g for g in full_groups if new in g), (new,))
    return {
        "material": new,
        "groups_incremental": [list(g) for g in inc_groups], "groups_full": [list(g) for g in full_groups],
        "pair_jaccard": jaccard(inc_groups, full_groups),
        "identical_groups": inc_groups == full_groups,
        "new_group_incremental": list(g_inc), "new_group_full": list(g_full),
        "new_group_identical": g_inc == g_full,
        "cost": {"incremental_pair_comparisons": result.evidence.comparisons,
                 "full_new_vs_all": n - 1, "full_all_pairs": n * (n - 1) // 2},
    }


def order_experiment(observations: Sequence[Observation], bank: PairBank, config: Mapping[str, Any],
                     base_ids: Sequence[str], orders: Sequence[Sequence[str]], *, seed: int = 0) -> dict[str, Any]:
    """Dołącza te same materiały w różnych kolejnościach; mierzy rozbieżność między kolejnościami i względem
    pełnego przeliczenia całości."""
    by_id = {o.canonical_id: o for o in observations}
    base = build_index([by_id[m] for m in base_ids], bank.subset(base_ids), config, seed=seed)
    rows, finals = [], []
    for order in orders:
        idx, comparisons, decisions = base, 0, []
        for m in order:
            r = attach(idx, by_id[m], bank, seed=seed)
            idx, comparisons = r.index, comparisons + r.evidence.comparisons
            decisions.append({"material": m, "decision": r.evidence.decision, "cluster_id": r.evidence.cluster_id})
        finals.append(idx.groups())
        rows.append({"order": list(order), "decisions": decisions, "groups": [list(g) for g in idx.groups()],
                     "comparisons": comparisons, "final_index_id": idx.index_id})
    all_ids = [*base_ids, *orders[0]]
    full = fuse_and_cluster([by_id[m] for m in all_ids], bank.subset(all_ids),
                            {**base.config["fusion"], "dataset_version": base.dataset_version}, seed=seed)
    full_groups = sorted(tuple(c.members) for c in full.clusters)
    for row, g in zip(rows, finals):
        row["jaccard_vs_full"] = jaccard(g, full_groups)
        row["identical_to_full"] = g == full_groups
    cross = [jaccard(a, b) for a, b in combinations(finals, 2)]
    return {"base": list(base_ids), "groups_full": [list(g) for g in full_groups], "orders": rows,
            "order_independent": all(g == finals[0] for g in finals),
            "min_pairwise_jaccard_between_orders": min((c for c in cross if c is not None), default=None),
            "orders_identical_to_full": sum(r["identical_to_full"] for r in rows)}


# ------------------------------------------------------------------ adapter clusterera

class IncrementalClusterer:
    """Kontrakt clusterera: config.new_material (canonical_id) dołączany do indeksu zbudowanego z pozostałych."""
    spec = AdapterSpec(id="incremental-attach-v1", kind=AdapterKind.clusterer, version=METHOD_VERSION,
                       description="Przyrostowe dołączenie materiału do indeksu (reprezentant + próbka członków)")

    def cluster(self, observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                config: Mapping[str, Any], *, seed: int = 0, optional: Mapping[str, Any] | None = None) -> ClusterRun:
        new = config.get("new_material")
        by_id = {o.canonical_id: o for o in observations}
        if new not in by_id:
            raise ValueError("config.new_material musi wskazywać materiał z obserwacji")
        bank = PairBank(pair_evidence)
        rest = [o for o in observations if o.canonical_id != new]
        base_cfg = {k: v for k, v in config.items() if k != "new_material"}
        index = build_index(rest, bank.subset([o.canonical_id for o in rest]), base_cfg, seed=seed)
        return attach(index, by_id[new], bank, seed=seed, dataset_version=_dataset(config)).run


def get_adapters() -> list[IncrementalClusterer]:
    return [IncrementalClusterer()]
