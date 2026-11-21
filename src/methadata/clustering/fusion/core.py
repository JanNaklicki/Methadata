"""K09: fuzja niezależnych rodzin dowodów -> hipoteza kampanii (relation=campaign_candidate), grupowanie przez K01.

Pojęcia (jawnie rozdzielone):
  * podobieństwo  = ważona średnia wyników dostępnych źródeł treści (origin);
  * pokrycie      = udział wagi źródeł, dla których porównanie dało wynik (Σw dostępnych / Σw skonfigurowanych);
  * wsparcie      = liczba odrębnych źródeł treści, których wynik >= support_min (próg skonfigurowany per rodzina).
Wspólne pochodzenie: rodziny deklarują `origin`. Rodziny o tym samym origin (np. OCR akapitu i jego tekst HTML) to jedno
źródło treści: jego wynik to średnia ważona wyników członków, waga = największa waga członka, więc wkład liczy się raz
i nie zwiększa ani pokrycia, ani wsparcia. Krawędź wymaga: pokrycie >= coverage_min ORAZ wsparcie >= min_support
ORAZ fuzja >= threshold (ostatnie egzekwuje K01). Brak pokrycia -> score=None (porównanie niewykonalne);
brak wsparcia -> score obcięty tuż poniżej progu (oznaczone w wyjaśnieniu), więc K01 nie połączy pary.
Wagi i progi to parametry konfiguracji (nie dopasowane do danych).
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Iterable, Mapping, Optional, Sequence

from methadata.clustering.components import ClusterEvidence, build_result
from methadata.contracts import (AdapterKind, AdapterSpec, ArtifactRef, ClusterRun, Observation, PairEvidence,
                                 Relation)

METHOD_ID = "fusion-v1"
METHOD_VERSION = "1"
RELATION = Relation.campaign_candidate
ST_OK, ST_NO_EVIDENCE, ST_COVERAGE, ST_SUPPORT = "ok", "no_evidence", "insufficient_coverage", "insufficient_support"


# ------------------------------------------------------------------ konfiguracja

def resolve_config(config: Mapping[str, Any], *, for_run: bool = True) -> dict[str, Any]:
    fams = config.get("families")
    if not isinstance(fams, Mapping) or not fams:
        raise ValueError("config.families jest wymagane (nazwa -> {method_id, weight, origin, support_min})")
    required = ["threshold", "similarity_version"] + (["dataset_version"] if for_run else [])
    for key in required:
        if config.get(key) in (None, ""):
            raise ValueError(f"config.{key} jest wymagane (wejście musi wskazywać wersję cech/podobieństw)")

    def num(name, lo, hi, v, lo_open=False):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not ((lo < v if lo_open else lo <= v) and v <= hi):
            raise ValueError(f"config.{name} poza zakresem: {v!r}")
        return float(v)

    out_f: dict[str, dict[str, Any]] = {}
    seen_methods: set[str] = set()
    for name in sorted(fams):
        f = fams[name]
        for key in ("method_id", "weight", "support_min"):
            if f.get(key) in (None, ""):
                raise ValueError(f"config.families.{name}.{key} jest wymagane (skonfigurowane wsparcie i waga)")
        if f["method_id"] in seen_methods:
            raise ValueError(f"metoda {f['method_id']} przypisana do dwóch rodzin")
        seen_methods.add(f["method_id"])
        out_f[name] = {"method_id": str(f["method_id"]),
                       "weight": num(f"families.{name}.weight", 0.0, 1.0e6, f["weight"], lo_open=True),
                       "origin": str(f.get("origin") or name),
                       "support_min": num(f"families.{name}.support_min", 0.0, 1.0, f["support_min"], lo_open=True),
                       "relation": Relation(f["relation"]).value if f.get("relation") else None}
    min_support = config.get("min_support", 2)
    if isinstance(min_support, bool) or not isinstance(min_support, int) or min_support < 1:
        raise ValueError("config.min_support musi być liczbą całkowitą >= 1")
    cfg = {"families": out_f,
           "threshold": num("threshold", 0.0, 1.0, config["threshold"], lo_open=True),
           "coverage_min": num("coverage_min", 0.0, 1.0, config.get("coverage_min", 0.5)),
           "min_support": min_support,
           "similarity_version": str(config["similarity_version"])}
    if for_run:
        cfg["dataset_version"] = str(config["dataset_version"])
    return cfg


def cluster_config(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Konfiguracja K01 dla fuzji (do cluster_config runnera)."""
    out = {"threshold": cfg["threshold"], "similarity_version": cfg["similarity_version"],
           "relation": RELATION.value, "method_id": METHOD_ID}
    if "dataset_version" in cfg:
        out["dataset_version"] = cfg["dataset_version"]
    return out


# ------------------------------------------------------------------ fuzja jednej pary

@dataclass(frozen=True)
class FamilyScore:
    family: str
    method_id: str
    origin: str
    weight: float
    score: Optional[float]
    pair_evidence_id: Optional[str]
    disabled: bool = False


@dataclass(frozen=True)
class OriginScore:
    origin: str
    families: tuple[str, ...]          # rodziny, które policzyły wynik (wkład liczony raz)
    score: Optional[float]
    weight: float
    support_min: float
    supports: bool


@dataclass(frozen=True)
class PairFusion:
    pair: tuple[str, str]
    families: tuple[FamilyScore, ...]
    origins: tuple[OriginScore, ...]
    fused: Optional[float]
    coverage: float
    supporting_origins: int
    status: str
    score: Optional[float]             # wynik przekazywany do K01 (po bramkach)
    disabled: tuple[str, ...] = ()
    ablation: dict[str, "PairFusion"] = field(default_factory=dict)
    refs: dict[str, list[ArtifactRef]] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {"fused": _r(self.fused), "coverage": _r(self.coverage), "supporting_origins": self.supporting_origins,
                "status": self.status, "score": _r(self.score)}

    def details(self) -> dict[str, Any]:
        d = {**self.summary(),
             "origins": [{"origin": o.origin, "families": list(o.families), "score": _r(o.score), "weight": o.weight,
                          "support_min": o.support_min, "supports": o.supports} for o in self.origins],
             "families": [{"family": f.family, "method": f.method_id, "score": _r(f.score),
                           "pair_evidence_id": f.pair_evidence_id} for f in self.families if not f.disabled]}
        if self.ablation:
            d["without"] = {k: v.summary() for k, v in sorted(self.ablation.items())}
        return d


def _r(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(x, 6)


def _below(threshold: float) -> float:
    return math.nextafter(threshold, 0.0)


def _pick_best(evs: Iterable[PairEvidence]) -> Optional[PairEvidence]:
    best = None
    for e in sorted(evs, key=lambda e: e.pair_evidence_id):
        if best is None or (e.score is not None and (best.score is None or e.score > best.score)):
            best = e
    return best


def _fuse(pair: tuple[str, str], by_method: Mapping[str, PairEvidence], cfg: Mapping[str, Any],
          disabled: frozenset[str]) -> PairFusion:
    fams = []
    for name, fc in sorted(cfg["families"].items()):
        ev = by_method.get(fc["method_id"])
        dis = name in disabled
        fams.append(FamilyScore(name, fc["method_id"], fc["origin"], fc["weight"],
                                None if dis or ev is None else ev.score,
                                None if dis or ev is None else ev.pair_evidence_id, dis))
    groups: dict[str, list[FamilyScore]] = {}
    for f in fams:
        if not f.disabled:
            groups.setdefault(f.origin, []).append(f)
    origins = []
    for origin, members in sorted(groups.items()):
        scored = [m for m in members if m.score is not None]
        w = max(m.weight for m in members)                       # waga źródła liczona raz
        smin = min(cfg["families"][m.family]["support_min"] for m in members)
        sc = (sum(m.weight * m.score for m in scored) / sum(m.weight for m in scored)) if scored else None
        origins.append(OriginScore(origin, tuple(m.family for m in scored), sc, w, smin,
                                   sc is not None and sc >= smin))
    total = sum(o.weight for o in origins)
    avail = [o for o in origins if o.score is not None]
    aw = sum(o.weight for o in avail)
    coverage = aw / total if total else 0.0
    fused = round(sum(o.weight * o.score for o in avail) / aw, 12) if aw else None
    supports = sum(1 for o in avail if o.supports)
    if fused is None:
        status, score = ST_NO_EVIDENCE, None
    elif coverage + 1e-12 < cfg["coverage_min"]:
        status, score = ST_COVERAGE, None
    elif supports < cfg["min_support"]:
        status, score = ST_SUPPORT, min(fused, _below(cfg["threshold"]))
    else:
        status, score = ST_OK, fused
    refs: dict[str, list[ArtifactRef]] = {pair[0]: [], pair[1]: []}
    for f in fams:
        ev = by_method.get(f.method_id)
        if ev is not None and f.score is not None:
            for c in pair:
                for r in ev.evidence_refs.get(c, []):
                    if r not in refs[c]:
                        refs[c].append(r)
    return PairFusion(pair, tuple(fams), tuple(origins), fused, coverage, supports, status, score,
                      tuple(sorted(disabled)), {}, refs)


def fuse_pair(pair: tuple[str, str], evidence: Iterable[PairEvidence], config: Mapping[str, Any]) -> PairFusion:
    """Fuzja jednej pary z jej dowodów składowych (dowolnie wiele metod) + wyniki po wyłączeniu każdej rodziny."""
    cfg = resolve_config(config, for_run=False)  # idempotentne dla skonfigurowanej postaci
    wanted = {fc["method_id"]: fc.get("relation") for fc in cfg["families"].values()}
    groups: dict[str, list[PairEvidence]] = {}
    for e in evidence:
        if e.pair == tuple(pair) and e.method_id in wanted and (wanted[e.method_id] in (None, e.relation.value)):
            groups.setdefault(e.method_id, []).append(e)
    by_method = {m: _pick_best(v) for m, v in groups.items()}
    base = _fuse(tuple(pair), by_method, cfg, frozenset())
    abl = {name: _fuse(tuple(pair), by_method, cfg, frozenset({name})) for name in cfg["families"]}
    return PairFusion(**{**base.__dict__, "ablation": abl})


def explain(pf: PairFusion, cfg: Mapping[str, Any]) -> str:
    s = lambda x: "brak" if x is None else f"{x:.4f}"  # noqa: E731
    parts = [f"Fuzja {METHOD_ID}: wynik {s(pf.score)} (średnia ważona {s(pf.fused)}, pokrycie {pf.coverage:.2f} "
             f"przy minimum {cfg['coverage_min']}, wsparcie {pf.supporting_origins} odrębnych źródeł treści "
             f"przy wymaganych {cfg['min_support']}, status {pf.status})."]
    if pf.status == ST_SUPPORT:
        parts.append(f"Wynik obcięty poniżej progu {cfg['threshold']}: brak wsparcia dwóch odrębnych źródeł.")
    for o in pf.origins:
        parts.append(f"Źródło {o.origin} [{', '.join(o.families) or 'brak'}]: {s(o.score)} (waga {o.weight}, "
                     f"wsparcie od {o.support_min}: {'tak' if o.supports else 'nie'}).")
    if pf.ablation:
        parts.append("Bez rodziny: " + "; ".join(
            f"{k} -> {s(v.score)} / pokrycie {v.coverage:.2f} / wsparcie {v.supporting_origins} / {v.status}"
            for k, v in sorted(pf.ablation.items())) + ".")
    parts.append("Szczegóły: " + json.dumps(pf.details(), sort_keys=True, ensure_ascii=False))
    return " ".join(parts)


def to_pair_evidence(pf: PairFusion, cfg: Mapping[str, Any], run_id: str) -> PairEvidence:
    refs = pf.refs if pf.score is not None else {}
    return PairEvidence(run_id=run_id, pair=pf.pair, method_id=METHOD_ID, relation=RELATION, score=pf.score,
                        evidence_refs=refs, explanation=explain(pf, cfg))


# ------------------------------------------------------------------ cały zbiór

@dataclass(frozen=True)
class FusionResult:
    run: ClusterRun
    pairs: tuple[PairEvidence, ...]                    # fuzje par (PairEvidence relation=campaign_candidate)
    fusions: dict[tuple[str, str], PairFusion]
    clusters: tuple[ClusterEvidence, ...]
    below_threshold: tuple[Any, ...]
    ablation_groups: dict[str, list[tuple[str, ...]]]  # rodzina wyłączona -> grupy
    config: dict[str, Any]

    def assignment_evidence(self, material_id: str) -> list[dict[str, Any]]:
        """Dowody przypisania materiału: krawędzie jego grupy z wynikiem, pokryciem i ablacją."""
        by_id = {p.pair_evidence_id: p for p in self.pairs}
        out = []
        for c in self.clusters:
            if material_id in c.members:
                for e in c.edges:
                    pe = by_id[e.pair_evidence_id]
                    out.append({"cluster_id": c.cluster_id, "edge": [e.source, e.target], "weight": e.weight,
                                "pair_evidence_id": e.pair_evidence_id, "bridge": e in c.bridges,
                                "detail": self.fusions[pe.pair].details()})
        return out


def _run_id(cfg, seed, mats, ids_) -> str:
    blob = json.dumps([cfg, seed, list(mats), list(ids_)], sort_keys=True, default=str)
    return "run-fusion-" + hashlib.sha256(blob.encode()).hexdigest()[:12]


def _group_evidence(mats: Sequence[str], evidence: Sequence[PairEvidence], cfg):
    wanted = {fc["method_id"]: fc.get("relation") for fc in cfg["families"].values()}
    by_pair: dict[tuple[str, str], dict[str, list[PairEvidence]]] = {}
    matset = set(mats)
    for e in evidence:
        if e.method_id in wanted and wanted[e.method_id] in (None, e.relation.value):
            for c in e.pair:
                if c not in matset:
                    raise ValueError(f"PairEvidence {e.pair_evidence_id} wskazuje materiał spoza obserwacji: {c}")
            by_pair.setdefault(e.pair, {}).setdefault(e.method_id, []).append(e)
    return by_pair


def fuse_and_cluster(observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                     config: Mapping[str, Any], *, seed: int = 0) -> FusionResult:
    cfg = resolve_config(config)
    mats = sorted({o.canonical_id for o in observations})
    in_ids = sorted(e.pair_evidence_id for e in pair_evidence)
    run_id = _run_id(cfg, seed, mats, in_ids)
    kcfg = cluster_config(cfg)

    by_pair = _group_evidence(mats, pair_evidence, cfg)
    full = {p: fuse_pair(p, [e for v in evs.values() for e in v], cfg) for p, evs in by_pair.items()}
    pairs = [to_pair_evidence(full[p], cfg, run_id) for p in sorted(full)]
    res = build_result(observations, pairs, kcfg, seed=seed)
    abl = {}
    for name in sorted(cfg["families"]):  # grupy po wyłączeniu rodziny (te same dowody, inna fuzja)
        ps = [to_pair_evidence(pf.ablation[name], cfg, run_id) for pf in (full[p] for p in sorted(full))]
        abl[name] = [tuple(c.members) for c in build_result(observations, ps, kcfg, seed=seed).clusters]
    run = res.run.model_copy(update={
        "run_id": run_id,
        "config": {**res.run.config, "fusion": {k: v for k, v in cfg.items() if k != "dataset_version"},
                   "input_methods": sorted({fc["method_id"] for fc in cfg["families"].values()}),
                   "input_pair_evidence": len(in_ids)}})
    return FusionResult(run, tuple(pairs), full, res.clusters, res.below_threshold, abl, cfg)


# ------------------------------------------------------------------ komparator (ścieżka runnera) i helper

def collect_pairs(observations: Sequence[Observation], features: Mapping[str, Sequence[Any]],
                  comparators: Sequence[Any], *, run_id: str) -> list[PairEvidence]:
    """Wszystkie pary wskazanych komparatorów; features: observation_id -> cechy całego korpusu
    (potrzebne np. do IDF komparatora leksykalnego)."""
    obs = sorted({o.canonical_id: o for o in sorted(observations, key=lambda o: o.observation_id)}.values(),
                 key=lambda o: o.canonical_id)
    out: list[PairEvidence] = []
    for a, b in combinations(obs, 2):
        for c in comparators:
            out.extend(c.compare(a, b, features, run_id=run_id))
    return out


class FusionComparator:
    """Komparator fuzji dla runnera F03: sam woła komparatory składowe i wystawia PairEvidence campaign_candidate.
    Uwaga: runner przekazuje komparatorowi cechy tylko dwóch obserwacji, więc IDF komparatora leksykalnego
    liczony jest wtedy z pary; pełną wersję daje fuse_and_cluster na pairs z collect_pairs."""

    def __init__(self, config: Optional[Mapping[str, Any]] = None, comparators: Optional[Sequence[Any]] = None):
        from .config import DEFAULT_CONFIG
        self.cfg = resolve_config(config or DEFAULT_CONFIG, for_run=False)
        if comparators is None:
            from methadata.experiments.assets.adapters import AssetJaccardComparator
            from methadata.experiments.lexical.adapters import LexicalTfidfComparator
            comparators = [AssetJaccardComparator(), LexicalTfidfComparator()]
        self.comparators = list(comparators)
        self.spec = AdapterSpec(id=METHOD_ID, kind=AdapterKind.comparator, version=METHOD_VERSION,
                                description="Fuzja niezależnych rodzin dowodów: średnia ważona, pokrycie, wsparcie >= 2 źródeł")

    def compare(self, a, b, features, *, run_id, optional=None) -> Sequence[PairEvidence]:
        (ca, _), (cb, _) = sorted([(a.canonical_id, a), (b.canonical_id, b)], key=lambda t: t[0])
        evs = [e for c in self.comparators for e in c.compare(a, b, features, run_id=run_id)]
        pf = fuse_pair((ca, cb), evs, self.cfg)
        return [to_pair_evidence(pf, self.cfg, run_id)]
