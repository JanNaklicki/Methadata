"""K12: aglomeracja average-link z ograniczeniami analityka (must-link / cannot-link) dla jednej rodzaju relacji.

Ograniczenia podaje się jawnie (config.constraints, niepusta lista). Ograniczenie dotyczy konkretnej relacji
(domyślnie config.relation); ograniczenia innych relacji są pomijane i zgłaszane w wyniku (ignored_constraints).
Sprzeczność (ta sama para must i cannot albo cannot wewnątrz domknięcia przechodniego must-link) kończy się
ConstraintConflict - nic nie jest po cichu rozstrzygane.

Algorytm: grupy startowe = składowe must-link (scalenia wymuszone, dowód = ograniczenie). Dalej average-link na
podobieństwach par o wykonanym porównaniu (brak/score=null jest pomijany w średniej, nie jest 0 ani 1); scala się
najwyższe łączenie >= próg (remis: porządek leksykograficzny), o ile między grupami nie ma cannot-link. Zablokowane
scalenia (które inaczej by zaszły) trafiają do `blocked` z wskazanym ograniczeniem - to dowód zakazu na moście A-B-C.
Materiał bez żadnego porównania i bez ograniczenia -> insufficient_data.

Ocena: evaluate_hidden() ukrywa pary użyte jako ograniczenia (jawne oraz wynikające z domknięcia must-link /
cannot-link między składowymi) i liczy metryki F03 wyłącznie na pozostałych parach referencji.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from methadata.contracts import (AdapterKind, AdapterSpec, Assignment, AssignmentStatus, ClusterRun, Edge,
                                 Observation, PairEvidence, Relation)

METHOD_VERSION = "1"
KINDS = ("must", "cannot")
_EPS = 1e-12
Pair = tuple[str, str]


class ConstraintConflict(ValueError):
    def __init__(self, conflicts: Sequence[dict]):
        self.conflicts = list(conflicts)
        super().__init__("sprzeczne ograniczenia: " + "; ".join(c["detail"] for c in self.conflicts))


@dataclass(frozen=True)
class Constraint:
    a: str
    b: str
    kind: str                   # "must" | "cannot"
    relation: str
    analyst: str = ""
    note: str = ""

    @property
    def pair(self) -> Pair:
        return (self.a, self.b) if self.a < self.b else (self.b, self.a)

    def to_dict(self) -> dict:
        return {"a": self.pair[0], "b": self.pair[1], "kind": self.kind, "relation": self.relation,
                "analyst": self.analyst, "note": self.note}


@dataclass(frozen=True)
class Merge:
    step: int
    left: tuple[str, ...]
    right: tuple[str, ...]
    basis: str                  # "must_link" | "similarity"
    similarity: Optional[float]
    constraints: tuple[Constraint, ...]   # ograniczenia must-link, które wymusiły scalenie
    edges: tuple[Edge, ...]               # pary o wykonanym porównaniu między grupami (dowody)


@dataclass(frozen=True)
class Blocked:
    left: tuple[str, ...]
    right: tuple[str, ...]
    similarity: float
    constraints: tuple[Constraint, ...]   # cannot-link, które zablokowały scalenie


@dataclass(frozen=True)
class ClusterEvidence:
    cluster_id: str
    members: tuple[str, ...]
    merges: tuple[Merge, ...]
    constraints: tuple[Constraint, ...]   # must-link wewnątrz grupy
    separated_by: tuple[Constraint, ...]  # cannot-link dotyczące członków tej grupy (dowód rozdzielenia od innych)
    edges: tuple[Edge, ...]


@dataclass(frozen=True)
class ConstrainedResult:
    run: ClusterRun
    config: dict
    clusters: tuple[ClusterEvidence, ...]
    dendrogram: tuple[Merge, ...]
    blocked: tuple[Blocked, ...]
    excluded: tuple[str, ...]
    constraints: tuple[Constraint, ...]
    ignored_constraints: tuple[Constraint, ...]   # inna relacja


# ------------------------------------------------------------------ ograniczenia

def parse_constraints(raw: Any, default_relation: str) -> list[Constraint]:
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ValueError("config.constraints jest wymagane: jawna, niepusta lista ograniczeń")
    out: list[Constraint] = []
    for i, r in enumerate(raw):
        if isinstance(r, Constraint):
            c = r
        else:
            kind = r.get("kind")
            if kind not in KINDS:
                raise ValueError(f"constraints[{i}]: kind musi być jednym z {KINDS}")
            c = Constraint(str(r["a"]), str(r["b"]), kind, Relation(r.get("relation", default_relation)).value,
                           str(r.get("analyst", "")), str(r.get("note", "")))
        if c.a == c.b:
            raise ValueError(f"constraints[{i}]: ograniczenie dotyczy jednego materiału")
        out.append(Constraint(c.pair[0], c.pair[1], c.kind, c.relation, c.analyst, c.note))
    seen, uniq = set(), []
    for c in out:   # duplikaty identyczne są zbędne, ale nie sprzeczne
        k = (c.pair, c.kind, c.relation)
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    return uniq


class _UF:
    def __init__(self):
        self.p: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: str, b: str):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def find_conflicts(constraints: Sequence[Constraint]) -> list[dict]:
    """Sprzeczności w obrębie jednej relacji: (1) para i must, i cannot; (2) cannot wewnątrz składowej must-link."""
    out: list[dict] = []
    for rel in sorted({c.relation for c in constraints}):
        cs = [c for c in constraints if c.relation == rel]
        must = [c for c in cs if c.kind == "must"]
        cannot = [c for c in cs if c.kind == "cannot"]
        mp = {c.pair for c in must}
        uf = _UF()
        for c in must:
            uf.union(*c.pair)
        for c in sorted(cannot, key=lambda c: c.pair):
            if c.pair in mp:
                out.append({"relation": rel, "pair": list(c.pair), "type": "direct",
                            "detail": f"{c.pair[0]}-{c.pair[1]} ({rel}) oznaczona jednocześnie must-link i cannot-link"})
            elif uf.find(c.a) == uf.find(c.b):
                path = sorted(m.pair for m in must if uf.find(m.a) == uf.find(c.a))
                out.append({"relation": rel, "pair": list(c.pair), "type": "transitive", "must_chain": [list(p) for p in path],
                            "detail": f"cannot-link {c.pair[0]}-{c.pair[1]} ({rel}) leży w domknięciu must-link {path}"})
    return out


# ------------------------------------------------------------------ konfiguracja

def resolve_config(config: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("threshold", "similarity_version", "dataset_version"):
        if config.get(key) in (None, ""):
            raise ValueError(f"config.{key} jest wymagane (wejście musi wskazywać wersję cech/podobieństw)")
    thr = config["threshold"]
    if isinstance(thr, bool) or not isinstance(thr, (int, float)) or not (0.0 < thr <= 1.0):
        raise ValueError("config.threshold musi należeć do (0, 1]")
    rel = Relation(config.get("relation", Relation.shared_template)).value
    return {"threshold": float(thr), "similarity_version": str(config["similarity_version"]),
            "dataset_version": str(config["dataset_version"]), "relation": rel,
            "method_id": config.get("method_id"),
            "constraints": parse_constraints(config.get("constraints"), rel)}


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _cluster_id(members: Sequence[str]) -> str:
    return "cl-" + hashlib.sha256("\x1f".join(sorted(members)).encode()).hexdigest()[:12]


def _select_pairs(pairs: Sequence[PairEvidence], cfg: Mapping[str, Any], mats: set[str]):
    sel = [p for p in pairs if p.relation.value == cfg["relation"]
           and (cfg["method_id"] is None or p.method_id == cfg["method_id"])]
    methods = {p.method_id for p in sel}
    if cfg["method_id"] is None and len(methods) > 1:
        raise ValueError(f"wejście zawiera kilka metod {sorted(methods)}; ustaw config.method_id")
    best: dict[Pair, tuple[Optional[float], str]] = {}
    for p in sel:
        for c in p.pair:
            if c not in mats:
                raise ValueError(f"PairEvidence {p.pair_evidence_id} wskazuje materiał spoza obserwacji: {c}")
        cand = (p.score, p.pair_evidence_id)
        cur = best.get(p.pair)
        if cur is None:
            best[p.pair] = cand
        elif cand[0] is not None and (cur[0] is None or cand[0] > cur[0] or (cand[0] == cur[0] and cand[1] < cur[1])):
            best[p.pair] = cand
        elif cand[0] is None and cur[0] is None and cand[1] < cur[1]:
            best[p.pair] = cand
    return best


# ------------------------------------------------------------------ wynik

def build_result(observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                 config: Mapping[str, Any], *, seed: int = 0) -> ConstrainedResult:
    cfg = resolve_config(config)
    rel = Relation(cfg["relation"])
    thr = cfg["threshold"]
    mats = sorted({o.canonical_id for o in observations})
    matset = set(mats)
    all_cons: list[Constraint] = cfg["constraints"]
    for c in all_cons:
        for m in c.pair:
            if m not in matset:
                raise ValueError(f"ograniczenie wskazuje materiał spoza obserwacji: {m}")
    cons = [c for c in all_cons if c.relation == cfg["relation"]]
    ignored = tuple(c for c in all_cons if c.relation != cfg["relation"])
    conflicts = find_conflicts(all_cons)
    if conflicts:
        raise ConstraintConflict(conflicts)

    best = _select_pairs(pair_evidence, cfg, matset)
    sim = {k: s for k, (s, _) in best.items() if s is not None}
    pid = {k: p for k, (_, p) in best.items() if k in sim}
    cmats = {m for c in cons for m in c.pair}
    has = {m for k in sim for m in k}
    excluded = tuple(m for m in mats if m not in has and m not in cmats)
    keep = [m for m in mats if m not in excluded]

    must = [c for c in cons if c.kind == "must"]
    cannot = [c for c in cons if c.kind == "cannot"]

    def edge(a, b):
        return Edge(source=a, target=b, relation=rel, pair_evidence_id=pid[(a, b)], weight=sim[(a, b)])

    def edges_between(g, h):
        return tuple(sorted((edge(*sorted((a, b))) for a in g for b in h if tuple(sorted((a, b))) in sim),
                            key=lambda e: (e.source, e.target)))

    uf = _UF()
    for m in keep:
        uf.find(m)
    for c in must:
        uf.union(*c.pair)
    comps: dict[str, list[str]] = {}
    for m in keep:
        comps.setdefault(uf.find(m), []).append(m)
    groups: list[tuple[str, ...]] = sorted(tuple(sorted(v)) for v in comps.values())
    made: dict[tuple[str, ...], list[Merge]] = {g: [] for g in groups}
    dendro: list[Merge] = []
    blocked: list[Blocked] = []
    blocked_seen: set = set()

    for g in list(groups):   # scalenia wymuszone must-link (każda składowa w jednym kroku z pełnym dowodem)
        if len(g) > 1:
            used = tuple(sorted((c for c in must if c.a in g), key=lambda c: c.pair))
            mg = Merge(step=len(dendro) + 1, left=g[:1], right=g[1:], basis="must_link", similarity=None,
                       constraints=used, edges=edges_between(g[:1], g[1:]))
            dendro.append(mg)
            made[g] = [mg]

    def cannot_between(g, h):
        gs, hs = set(g), set(h)
        return tuple(sorted((c for c in cannot if (c.a in gs and c.b in hs) or (c.a in hs and c.b in gs)),
                            key=lambda c: c.pair))

    def link(g, h):
        vals = [sim[tuple(sorted((a, b)))] for a in g for b in h if tuple(sorted((a, b))) in sim]
        return sum(vals) / len(vals) if vals else None

    while True:
        cands = []
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                v = link(groups[i], groups[j])
                if v is not None and v >= thr - _EPS:
                    cands.append((-v, groups[i], groups[j], v))
        cands.sort()
        pick = None
        for _, g, h, v in cands:
            cb = cannot_between(g, h)
            if cb:
                key = (g, h)
                if key not in blocked_seen:
                    blocked_seen.add(key)
                    blocked.append(Blocked(g, h, v, cb))
                continue
            pick = (g, h, v)
            break
        if pick is None:
            break
        g, h, v = pick
        new = tuple(sorted(g + h))
        mg = Merge(step=len(dendro) + 1, left=g, right=h, basis="similarity", similarity=v, constraints=(),
                   edges=edges_between(g, h))
        dendro.append(mg)
        made[new] = made.pop(g) + made.pop(h) + [mg]
        groups = sorted([x for x in groups if x not in (g, h)] + [new])

    assignments = [Assignment(material_id=m, status=AssignmentStatus.insufficient_data) for m in excluded]
    clusters, run_edges = [], []
    for g in sorted(made):
        if len(g) == 1:
            assignments.append(Assignment(material_id=g[0], status=AssignmentStatus.singleton))
            continue
        cid = _cluster_id(g)
        gs = set(g)
        es = tuple(edge(a, b) for i, a in enumerate(g) for b in g[i + 1:] if (a, b) in sim)
        clusters.append(ClusterEvidence(
            cluster_id=cid, members=g, merges=tuple(made[g]),
            constraints=tuple(sorted((c for c in must if c.a in gs), key=lambda c: c.pair)),
            separated_by=tuple(sorted((c for c in cannot if (c.a in gs) != (c.b in gs)), key=lambda c: c.pair)),
            edges=es))
        run_edges += es
        assignments += [Assignment(material_id=m, cluster_ids=[cid], status=AssignmentStatus.grouped) for m in g]
    assignments.sort(key=lambda a: a.material_id)

    run_cfg = {k: v for k, v in cfg.items() if k not in ("dataset_version", "constraints")}
    run_cfg["constraints"] = [c.to_dict() for c in cons]
    run_cfg["ignored_constraints"] = [c.to_dict() for c in ignored]
    method_id = "constrained-average"
    run = ClusterRun(
        run_id=f"run-{method_id}-" + _digest([{k: v for k, v in cfg.items() if k != "constraints"},
                                             [c.to_dict() for c in all_cons], seed, mats,
                                             sorted((k, v) for k, v in best.items())]),
        dataset_version=cfg["dataset_version"], method_id=method_id, method_version=METHOD_VERSION,
        config=run_cfg, seed=seed, relation=rel, assignments=assignments, edges=run_edges)
    return ConstrainedResult(run=run, config=run_cfg, clusters=tuple(clusters), dendrogram=tuple(dendro),
                             blocked=tuple(blocked), excluded=excluded, constraints=tuple(cons),
                             ignored_constraints=ignored)


# ------------------------------------------------------------------ ocena na nieujawnionych parach

def hidden_pairs(constraints: Sequence[Constraint], relation: str, materials: Sequence[str]) -> set[Pair]:
    """Pary, których rozstrzygnięcie wynika z ograniczeń: jawne, domknięcie must-link oraz cannot-link
    rozciągnięty na całe składowe must-link."""
    cs = [c for c in constraints if c.relation == relation]
    uf = _UF()
    for c in cs:
        if c.kind == "must":
            uf.union(*c.pair)
    out = {c.pair for c in cs}
    mem: dict[str, list[str]] = {}
    for m in set(materials) | {x for c in cs for x in c.pair}:
        mem.setdefault(uf.find(m), []).append(m)
    for v in mem.values():
        out |= {(a, b) for a in v for b in v if a < b}
    for c in cs:
        if c.kind == "cannot":
            out |= {tuple(sorted((a, b))) for a in mem[uf.find(c.a)] for b in mem[uf.find(c.b)]}
    return out


def evaluate_hidden(result: ConstrainedResult, reference: Any, axis: str) -> dict[str, Any]:
    """Metryki par F03 na parach referencji NIEużytych jako ograniczenia. `reference` = evaluation.Reference."""
    from methadata.evaluation import axis_metrics, decisions_from_run

    ref = reference.axis(axis)
    mats = [a.material_id for a in result.run.assignments]
    hid = hidden_pairs(result.constraints, result.config["relation"], mats)
    rest = {p for p in ref if p not in hid}
    metrics = axis_metrics(ref, decisions_from_run(result.run, set(ref)), rest)
    return {"axis": axis, "relation": result.config["relation"], "reference_pairs": len(ref),
            "hidden_pairs": len([p for p in ref if p in hid]), "evaluated_pairs": len(rest),
            "metrics": metrics.to_dict(),
            "note": "pary użyte jako ograniczenia (i z nich wynikające) pominięte w metrykach"}


# ------------------------------------------------------------------ adapter

class ConstrainedClusterer:
    spec = AdapterSpec(id="constrained-average-v1", kind=AdapterKind.clusterer, version=METHOD_VERSION,
                       description="Average-link z ograniczeniami analityka must-link/cannot-link dla jednej relacji")

    def cluster(self, observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                config: Mapping[str, Any], *, seed: int = 0, optional: Mapping[str, Any] | None = None) -> ClusterRun:
        return build_result(observations, pair_evidence, config, seed=seed).run


def get_adapters():
    return [ConstrainedClusterer()]
