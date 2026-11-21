"""K11: konsensus klastrów z wielu wersjonowanych ClusterRun.

Wejście: kilka ClusterRun tego samego zbioru (różne rodziny cech, metody, parametry). Każde uruchomienie musi wskazywać
wersję cech/podobieństw (config.similarity_version lub config.feature_version) i ten sam dataset_version.

Macierz zgodności: dla pary (a, b) licznik = uruchomienia (ważone), w których a i b dzielą klaster; mianownik = tylko
uruchomienia, w których OBA materiały były ocenialne (status != insufficient_data i obecne w przypisaniach). Para bez
żadnego ocenialnego uruchomienia nie ma zgodności (None), a nie zgodność 0.

Stabilne rdzenie: składowe grafu par o zgodności >= threshold, z min. liczbą ocenialnych uruchomień i min. liczbą rodzin
głosujących "razem"; potem strażnik łańcuchowy (członek musi być zgodny z co najmniej połową pozostałych).

Kontrola dominacji: waga rodziny metod jest dzielona na jej uruchomienia (weighting="family"), więc wiele podobnych
metod to jeden głos; dodatkowo odjęcie całej rodziny (leave-family-out) oraz test pojedynczego głosu (które krawędzie
rdzenia znikają po usunięciu jednego uruchomienia).
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Mapping, Optional, Sequence

from methadata.contracts import (AdapterKind, AdapterSpec, ArtifactRef, Assignment, AssignmentStatus, ClusterRun, Edge,
                                 Observation, PairEvidence, Relation)

METHOD_ID = "consensus-cores"
METHOD_VERSION = "1"
DEFAULT_RELATION = Relation.campaign_candidate
_VERSION_KEYS = ("similarity_version", "feature_version")


@dataclass(frozen=True)
class RunVote:
    run_id: str
    family: str
    weight: float
    together: bool


@dataclass(frozen=True)
class PairConsensus:
    pair: tuple[str, str]
    together: int                      # surowa liczba uruchomień, w których para dzieli klaster
    evaluable_runs: int                # liczba uruchomień, w których OBIE obserwacje były ocenialne
    agreement: Optional[float]         # ważony odsetek wspólnych przypisań (None: brak ocenialnych uruchomień)
    families_together: tuple[str, ...]
    families_evaluable: tuple[str, ...]
    votes: tuple[RunVote, ...]         # głosy tylko z uruchomień ocenialnych
    decisive_runs: tuple[str, ...]     # uruchomienia, których usunięcie zmienia decyzję o krawędzi
    source_pair_evidence_ids: tuple[str, ...]
    is_edge: bool


@dataclass(frozen=True)
class CoreEvidence:
    cluster_id: str
    members: tuple[str, ...]
    edges: tuple[Edge, ...]
    weakest: Optional[Edge]
    min_agreement: float
    mean_agreement: float
    min_evaluable_runs: int            # najmniejsza liczba uruchomień ocenialnych dla pary wewnątrz rdzenia
    dropped_by_chain_guard: tuple[str, ...]


@dataclass(frozen=True)
class FamilyAblation:
    family: str
    runs_removed: int
    core_pair_retention: Optional[float]   # odsetek par rdzenia nadal w rdzeniu po odjęciu rodziny
    groups_without: tuple[tuple[str, ...], ...]
    lost_groups: tuple[tuple[str, ...], ...]
    dominant: bool


@dataclass(frozen=True)
class ConsensusResult:
    run: ClusterRun
    pair_evidence: tuple[PairEvidence, ...]      # PairEvidence konsensusu (cel Edge.pair_evidence_id)
    cores: tuple[CoreEvidence, ...]
    pairs: dict[tuple[str, str], PairConsensus]  # pary z co najmniej jednym wspólnym przypisaniem
    material_evaluable_runs: dict[str, int]
    family_shares: dict[str, float]
    family_run_counts: dict[str, int]
    ablations: tuple[FamilyAblation, ...]
    single_vote: dict[str, dict[str, Any]]       # run_id -> {decisive_edges, share_of_edges}
    warnings: tuple[str, ...]
    config: dict[str, Any]

    @property
    def dominance_ok(self) -> bool:
        return not self.warnings


# ------------------------------------------------------------------ konfiguracja

def resolve_config(config: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("threshold", "similarity_version", "dataset_version"):
        if config.get(key) in (None, ""):
            raise ValueError(f"config.{key} jest wymagane (wejście musi wskazywać wersję cech/podobieństw)")

    def num(name, v, lo, hi, lo_open=False):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not ((lo < v if lo_open else lo <= v) and v <= hi):
            raise ValueError(f"config.{name} poza zakresem: {v!r}")
        return float(v)

    def integer(name, v, lo):
        if isinstance(v, bool) or not isinstance(v, int) or v < lo:
            raise ValueError(f"config.{name} musi być liczbą całkowitą >= {lo}")
        return v

    weighting = config.get("weighting", "family")
    if weighting not in ("family", "run"):
        raise ValueError("config.weighting: 'family' albo 'run'")
    fam = config.get("families", {})
    if not isinstance(fam, Mapping):
        raise ValueError("config.families: mapa method_id -> rodzina")
    return {
        "threshold": num("threshold", config["threshold"], 0.0, 1.0, True),
        "similarity_version": str(config["similarity_version"]),
        "dataset_version": str(config["dataset_version"]),
        "relation": Relation(config.get("relation", DEFAULT_RELATION)).value,
        "min_evaluable": integer("min_evaluable", config.get("min_evaluable", 2), 1),
        "min_families": integer("min_families", config.get("min_families", 1), 1),
        "weighting": weighting,
        "families": {str(k): str(v) for k, v in sorted(fam.items())},
        "max_family_dominance": num("max_family_dominance", config.get("max_family_dominance", 0.5), 0.0, 1.0),
        "max_decisive_fraction": num("max_decisive_fraction", config.get("max_decisive_fraction", 0.5), 0.0, 1.0),
    }


def _digest(prefix: str, obj: Any) -> str:
    return prefix + hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _cluster_id(members: Sequence[str]) -> str:
    return "cl-" + hashlib.sha256("\x1f".join(sorted(members)).encode()).hexdigest()[:12]


# ------------------------------------------------------------------ wejście

@dataclass(frozen=True)
class _Run:
    run_id: str
    family: str
    version: str
    method_id: str
    labels: dict[str, frozenset]   # material -> zbiór klastrów, tylko ocenialne


def _prepare(runs: Sequence[ClusterRun], mats: set[str], cfg: Mapping[str, Any]) -> list[_Run]:
    if not runs:
        raise ValueError("konsensus wymaga co najmniej jednego ClusterRun")
    seen, out = set(), []
    for r in sorted(runs, key=lambda r: r.run_id):
        if r.run_id in seen:
            raise ValueError(f"powtórzony run_id {r.run_id}")
        seen.add(r.run_id)
        if r.dataset_version != cfg["dataset_version"]:
            raise ValueError(f"run {r.run_id} ma dataset_version {r.dataset_version!r} != {cfg['dataset_version']!r}")
        ver = next((str(r.config[k]) for k in _VERSION_KEYS if r.config.get(k)), None)
        if ver is None:
            raise ValueError(f"run {r.run_id} nie wskazuje wersji cech/podobieństw (config.similarity_version/feature_version)")
        labels = {}
        for a in r.assignments:
            if a.material_id not in mats:
                raise ValueError(f"run {r.run_id}: materiał spoza obserwacji: {a.material_id}")
            if a.status is not AssignmentStatus.insufficient_data:
                labels[a.material_id] = frozenset((r.run_id, c) for c in a.cluster_ids)
        fam = cfg["families"].get(r.method_id) or r.config.get("method_family") or r.method_id
        out.append(_Run(r.run_id, str(fam), ver, r.method_id, labels))
    return out


def _weights(runs: Sequence[_Run], weighting: str) -> dict[str, float]:
    if weighting == "run":
        return {r.run_id: 1.0 for r in runs}
    cnt: dict[str, int] = {}
    for r in runs:
        cnt[r.family] = cnt.get(r.family, 0) + 1
    return {r.run_id: 1.0 / cnt[r.family] for r in runs}


# ------------------------------------------------------------------ zgodność par

def _raw_pairs(runs: Sequence[_Run]) -> dict[tuple[str, str], set[int]]:
    """pary, które dzielą klaster -> indeksy uruchomień (rzadko: tylko pary ze wspólnym klastrem)."""
    together: dict[tuple[str, str], set[int]] = {}
    for i, r in enumerate(runs):
        by_cl: dict[Any, list[str]] = {}
        for m, cls in r.labels.items():
            for c in cls:
                by_cl.setdefault(c, []).append(m)
        for ms in by_cl.values():
            for a, b in combinations(sorted(ms), 2):
                together.setdefault((a, b), set()).add(i)
    return together


def _decide(runs, idx_eval, idx_tog, w, cfg) -> tuple[Optional[float], bool]:
    den = sum(w[runs[i].run_id] for i in idx_eval)
    if den <= 0:
        return None, False
    num = sum(w[runs[i].run_id] for i in idx_tog)
    agr = num / den
    fams = {runs[i].family for i in idx_tog}
    ok = (agr >= cfg["threshold"] - 1e-12 and len(idx_eval) >= cfg["min_evaluable"]
          and len(fams) >= cfg["min_families"])
    return agr, ok


def _pair_table(runs: Sequence[_Run], cfg):
    w = _weights(runs, cfg["weighting"])
    table = {}
    for (a, b), tog in sorted(_raw_pairs(runs).items()):
        ev = [i for i, r in enumerate(runs) if a in r.labels and b in r.labels]
        agr, ok = _decide(runs, ev, sorted(tog), w, cfg)
        table[(a, b)] = (agr, ok, ev, sorted(tog))
    return w, table


def _components(nodes: Sequence[str], edges: Sequence[tuple[str, str]]) -> list[list[str]]:
    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    g: dict[str, list[str]] = {}
    for n in nodes:
        g.setdefault(find(n), []).append(n)
    return sorted((sorted(v) for v in g.values()), key=lambda v: v[0])


def _cores(mats: Sequence[str], edges: set[tuple[str, str]]):
    """Składowe + strażnik łańcuchowy -> ([rdzeń], {materiał odrzucony: ...})."""
    live = set(edges)
    dropped: set[str] = set()
    while True:
        comps = [c for c in _components(mats, sorted(live)) if len(c) > 1]
        bad = []
        for c in comps:
            for m in c:
                deg = sum(1 for o in c if o != m and tuple(sorted((m, o))) in live)
                if deg < math.ceil((len(c) - 1) / 2):
                    bad.append(m)
        if not bad:
            return comps, dropped
        dropped.update(bad)
        live = {e for e in live if e[0] not in set(bad) and e[1] not in set(bad)}


def _core_pairs(cores) -> set[tuple[str, str]]:
    return {p for c in cores for p in combinations(sorted(c), 2)}


# ------------------------------------------------------------------ wynik

def build_result(observations: Sequence[Observation], runs: Sequence[ClusterRun],
                 pair_evidence: Sequence[PairEvidence] = (), config: Mapping[str, Any] = None, *,
                 seed: int = 0) -> ConsensusResult:
    cfg = resolve_config(config or {})
    obs_by_c: dict[str, Observation] = {}
    for o in sorted(observations, key=lambda o: o.observation_id):
        obs_by_c.setdefault(o.canonical_id, o)
    mats = sorted(obs_by_c)
    prep = _prepare(runs, set(mats), cfg)
    w, table = _pair_table(prep, cfg)
    rel = Relation(cfg["relation"])

    edge_set = {p for p, (_, ok, _, _) in table.items() if ok}
    cores, dropped = _cores(mats, edge_set)
    in_core = {m for c in cores for m in c}

    # ablacje: cała rodzina odjęta
    fam_counts: dict[str, int] = {}
    for r in prep:
        fam_counts[r.family] = fam_counts.get(r.family, 0) + 1
    base_pairs = _core_pairs(cores)
    wsum = sum(w.values())
    shares = {f: round(sum(w[r.run_id] for r in prep if r.family == f) / wsum, 6) for f in sorted(fam_counts)}
    warnings: list[str] = []
    ablations = []
    for f in sorted(fam_counts):
        rest = [r for r in prep if r.family != f]
        if rest:
            # wymóg min_families nie może sam unieważniać rdzeni po odjęciu rodziny
            cfg2 = {**cfg, "min_families": min(cfg["min_families"], len(fam_counts) - 1)}
            _, t2 = _pair_table(rest, cfg2)
            c2, _ = _cores(mats, {p for p, v in t2.items() if v[1]})
        else:
            c2 = []
        kept = _core_pairs(c2) & base_pairs
        ret = (len(kept) / len(base_pairs)) if base_pairs else None
        lost = tuple(tuple(c) for c in cores if not any(set(c) <= set(x) for x in c2))
        dom = ret is not None and (1.0 - ret) > cfg["max_family_dominance"]
        if dom:
            warnings.append(f"rodzina {f!r} dominuje: po jej odjęciu zostaje {ret:.2f} par rdzeni")
        ablations.append(FamilyAblation(f, fam_counts[f], None if ret is None else round(ret, 6),
                                        tuple(tuple(c) for c in c2), lost, dom))
    if len(fam_counts) == 1 and base_pairs:
        warnings.append("wszystkie uruchomienia należą do jednej rodziny: konsensus nie jest niezależny")

    # pojedynczy głos
    decisive_by_run: dict[str, set[tuple[str, str]]] = {r.run_id: set() for r in prep}
    decisive_pair: dict[tuple[str, str], list[str]] = {}
    for p in sorted(edge_set):
        _, _, ev, tog = table[p]
        for i in ev:
            ev2 = [j for j in ev if j != i]
            tog2 = [j for j in tog if j != i]
            _, ok2 = _decide(prep, ev2, tog2, _weights([prep[k] for k in range(len(prep)) if k != i], cfg["weighting"]), cfg)
            if not ok2:
                decisive_by_run[prep[i].run_id].add(p)
                decisive_pair.setdefault(p, []).append(prep[i].run_id)
    core_edges = {p for p in edge_set if p[0] in in_core and p[1] in in_core}
    single = {}
    for rid in sorted(decisive_by_run):
        d = decisive_by_run[rid] & core_edges
        share = len(d) / len(core_edges) if core_edges else 0.0
        single[rid] = {"decisive_edges": len(d), "share_of_edges": round(share, 6)}
        if len(prep) > 1 and share > cfg["max_decisive_fraction"]:
            warnings.append(f"pojedynczy głos {rid} rozstrzyga {len(d)}/{len(core_edges)} krawędzi rdzeni")

    # dowody źródłowe
    src_by_pair: dict[tuple[str, str], list[str]] = {}
    for e in pair_evidence:
        if e.pair in table:
            src_by_pair.setdefault(e.pair, []).append(e.pair_evidence_id)

    run_id = _digest("run-consensus-", [cfg, seed, [(r.run_id, r.family, r.version) for r in prep], mats])

    def refs(c):
        arts = sorted(obs_by_c[c].artifacts, key=lambda a: a.artifact_id)
        if not arts:
            raise ValueError(f"obserwacja {c} nie ma artefaktów (brak dowodu dla PairEvidence konsensusu)")
        return [ArtifactRef(artifact_id=arts[0].artifact_id)]

    pairs: dict[tuple[str, str], PairConsensus] = {}
    pes: list[PairEvidence] = []
    for p, (agr, ok, ev, tog) in table.items():
        votes = tuple(RunVote(prep[i].run_id, prep[i].family, round(w[prep[i].run_id], 6), i in set(tog)) for i in ev)
        pairs[p] = PairConsensus(
            pair=p, together=len(tog), evaluable_runs=len(ev), agreement=None if agr is None else round(agr, 6),
            families_together=tuple(sorted({prep[i].family for i in tog})),
            families_evaluable=tuple(sorted({prep[i].family for i in ev})), votes=votes,
            decisive_runs=tuple(sorted(decisive_pair.get(p, []))),
            source_pair_evidence_ids=tuple(sorted(src_by_pair.get(p, []))), is_edge=p in core_edges)

    def edge_for(p):
        pc = pairs[p]
        pe = PairEvidence(
            run_id=run_id, pair=p, method_id=METHOD_ID, relation=rel, score=pc.agreement,
            evidence_refs={p[0]: refs(p[0]), p[1]: refs(p[1])},
            explanation=(f"konsensus: {pc.together}/{pc.evaluable_runs} ocenialnych uruchomień razem; zgodność ważona "
                         f"{pc.agreement:.3f}; rodziny razem: {', '.join(pc.families_together) or '-'}"))
        pes.append(pe)
        return Edge(source=p[0], target=p[1], relation=rel, pair_evidence_id=pe.pair_evidence_id, weight=pc.agreement)

    assignments, evid = [], []
    mat_eval = {m: sum(1 for r in prep if m in r.labels) for m in mats}
    for c in cores:
        cid = _cluster_id(c)
        es = tuple(edge_for(p) for p in sorted(combinations(c, 2)) if p in core_edges)
        agrs = [pairs[(e.source, e.target)].agreement for e in es]
        evid.append(CoreEvidence(
            cluster_id=cid, members=tuple(c), edges=es, weakest=min(es, key=lambda e: (e.weight, e.source, e.target)),
            min_agreement=min(agrs), mean_agreement=round(sum(agrs) / len(agrs), 6),
            min_evaluable_runs=min(pairs[(e.source, e.target)].evaluable_runs for e in es),
            dropped_by_chain_guard=tuple(sorted(m for m in dropped if any(
                tuple(sorted((m, x))) in edge_set for x in c)))))
        assignments += [Assignment(material_id=m, cluster_ids=[cid], status=AssignmentStatus.grouped) for m in c]
    for m in mats:
        if m in in_core:
            continue
        has = any(m in p and pc.evaluable_runs >= cfg["min_evaluable"] for p, pc in pairs.items())
        known = mat_eval[m] >= cfg["min_evaluable"] and (has or mat_eval[m] > 0)
        assignments.append(Assignment(material_id=m, status=AssignmentStatus.singleton if known
                                      else AssignmentStatus.insufficient_data))
    assignments.sort(key=lambda a: a.material_id)

    run_cfg = {k: v for k, v in cfg.items() if k != "dataset_version"}
    run_cfg["input_runs"] = [{"run_id": r.run_id, "method_id": r.method_id, "family": r.family,
                              "similarity_version": r.version, "weight": round(w[r.run_id], 6)} for r in prep]
    run_cfg["family_shares"] = shares
    run = ClusterRun(run_id=run_id, dataset_version=cfg["dataset_version"], method_id=METHOD_ID,
                     method_version=METHOD_VERSION, config=run_cfg, seed=seed, relation=rel,
                     assignments=assignments, edges=[e for c in evid for e in c.edges])
    return ConsensusResult(run=run, pair_evidence=tuple(pes), cores=tuple(evid), pairs=pairs,
                           material_evaluable_runs=mat_eval, family_shares=shares, family_run_counts=fam_counts,
                           ablations=tuple(ablations), single_vote=single, warnings=tuple(warnings), config=cfg)


def sweep(observations: Sequence[Observation], runs: Sequence[ClusterRun], config: Mapping[str, Any],
          thresholds: Sequence[float]) -> list[dict[str, Any]]:
    """Wpływ progu na rdzenie."""
    rows = []
    for t in thresholds:
        r = build_result(observations, runs, (), {**config, "threshold": t})
        rows.append({"threshold": t, "cores": [list(c.members) for c in r.cores],
                     "grouped": sum(len(c.members) for c in r.cores), "warnings": list(r.warnings)})
    return rows


def explain_pair(result: ConsensusResult, a: str, b: str) -> dict[str, Any]:
    p = tuple(sorted((a, b)))
    mats = {m for c in result.cores for m in c.members} | set(result.material_evaluable_runs)
    for m in p:
        if m not in mats:
            raise ValueError(f"materiał {m} spoza wyniku")
    pc = result.pairs.get(p)
    if pc is None:
        return {"pair": p, "together": 0, "agreement": 0.0 if result.material_evaluable_runs else None,
                "edge": False, "note": "żadne uruchomienie nie połączyło tej pary (lub brak ocenialnych)"}
    return {"pair": p, "together": pc.together, "evaluable_runs": pc.evaluable_runs, "agreement": pc.agreement,
            "edge": pc.is_edge, "votes": [(v.run_id, v.family, v.weight, v.together) for v in pc.votes],
            "decisive_runs": list(pc.decisive_runs), "source_pair_evidence_ids": list(pc.source_pair_evidence_ids)}


# ------------------------------------------------------------------ wielokrotne klastrowanie

def generate_runs(observations: Sequence[Observation], evidence_sets: Mapping[str, Sequence[PairEvidence]],
                  specs: Sequence[Mapping[str, Any]], adapters: Mapping[str, Any], *, dataset_version: str,
                  seed: int = 0) -> list[ClusterRun]:
    """Klastruje wielokrotnie. spec: {clusterer: id adaptera, evidence: klucz zbioru dowodów (wersja podobieństw),
    config: parametry, family: opcjonalna rodzina metod}. Do configu dopisywane są dataset_version, similarity_version
    oraz relation (gdy zbiór dowodów ma jedną relację)."""
    out = []
    for s in specs:
        ev_key = s["evidence"]
        if ev_key not in evidence_sets:
            raise ValueError(f"brak zbioru dowodów {ev_key!r}")
        cl = adapters[s["clusterer"]]
        rels = {e.relation.value for e in evidence_sets[ev_key]}
        cfg = {"similarity_version": ev_key, "dataset_version": dataset_version,
               **({"relation": next(iter(rels))} if len(rels) == 1 else {}), **dict(s.get("config", {}))}
        run = cl.cluster(observations, list(evidence_sets[ev_key]), cfg, seed=seed)
        extra = {"similarity_version": run.config.get("similarity_version") or ev_key}
        if s.get("family"):
            extra["method_family"] = s["family"]
        out.append(run.model_copy(update={"config": {**run.config, **extra}}))
    return out


# ------------------------------------------------------------------ adapter

class ConsensusClusterer:
    spec = AdapterSpec(id="consensus-cores-v1", kind=AdapterKind.clusterer, version=METHOD_VERSION,
                       description="Konsensus wielu ClusterRun: macierz zgodności (tylko pary ocenialne), stabilne rdzenie")

    def cluster(self, observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                config: Mapping[str, Any], *, seed: int = 0, optional: Mapping[str, Any] | None = None) -> ClusterRun:
        runs = (optional or {}).get("runs")
        if not runs:
            raise ValueError("optional['runs'] (lista ClusterRun) jest wymagane")
        return build_result(observations, runs, pair_evidence, config, seed=seed).run


def get_adapters():
    return [ConsensusClusterer()]


ADAPTERS = get_adapters()
