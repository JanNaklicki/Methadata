"""K01: klastry jako składowe spójne grafu progowego.

Krawędź istnieje, gdy score PairEvidence >= threshold (dla wybranej relacji i metody). Składowa spójna = grupa.
Każda krawędź niesie pair_evidence_id, więc przypisanie da się wyjaśnić. Wynik nie zależy od kolejności wejścia.

Statusy: grouped (>=2 materiały), singleton (porównania wykonano, żadne nie przekroczyło progu),
insufficient_data (brak jakiegokolwiek wykonanego porównania: wszystkie score=None lub brak par).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from methadata.contracts import (AdapterKind, AdapterSpec, Assignment, AssignmentStatus, ClusterRun, Edge,
                                 Observation, PairEvidence, Relation)

METHOD_ID = "components-threshold"
METHOD_VERSION = "1"
DEFAULT_RELATION = Relation.shared_template


@dataclass(frozen=True)
class ClusterEvidence:
    cluster_id: str
    members: tuple[str, ...]
    edges: tuple[Edge, ...]            # krawędzie powyżej progu wewnątrz grupy (każda z pair_evidence_id)
    bridges: tuple[Edge, ...]          # krawędzie, po których usunięciu grupa się rozpada
    weakest: Optional[Edge]            # najsłabsza krawędź grupy


@dataclass(frozen=True)
class ComponentsResult:
    run: ClusterRun
    clusters: tuple[ClusterEvidence, ...]
    below_threshold: tuple[Edge, ...]  # wykonane porównania poniżej progu (dowód, dlaczego nie połączono)


# ------------------------------------------------------------------ konfiguracja

def resolve_config(config: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("threshold", "similarity_version", "dataset_version"):
        if config.get(key) in (None, ""):
            raise ValueError(f"config.{key} jest wymagane (wejście musi wskazywać wersję cech/podobieństw)")
    thr = config["threshold"]
    if isinstance(thr, bool) or not isinstance(thr, (int, float)) or not (0.0 < thr <= 1.0):
        raise ValueError("config.threshold musi należeć do (0, 1]")
    return {
        "threshold": float(thr),
        "similarity_version": str(config["similarity_version"]),
        "dataset_version": str(config["dataset_version"]),
        "relation": Relation(config.get("relation", DEFAULT_RELATION)).value,
        "method_id": config.get("method_id"),  # None = dopuszczalna dokładnie jedna metoda w wejściu
    }


def _run_id(cfg: Mapping[str, Any], seed: int, mats: Sequence[str], pairs: Sequence[tuple]) -> str:
    blob = json.dumps([cfg, seed, list(mats), [list(p) for p in pairs]], sort_keys=True, default=str)
    return "run-components-" + hashlib.sha256(blob.encode()).hexdigest()[:12]


def _cluster_id(members: Sequence[str]) -> str:
    return "cl-" + hashlib.sha256("\x1f".join(sorted(members)).encode()).hexdigest()[:12]


# ------------------------------------------------------------------ graf

def _select_pairs(pairs: Sequence[PairEvidence], cfg: Mapping[str, Any], mats: set[str]):
    """-> {(a,b): (score|None, pair_evidence_id)}; przy kilku dowodach tej samej pary wygrywa najwyższy score
    (remis: mniejszy pair_evidence_id), a null tylko gdy wszystkie są null."""
    sel = [p for p in pairs if p.relation.value == cfg["relation"]
           and (cfg["method_id"] is None or p.method_id == cfg["method_id"])]
    methods = {p.method_id for p in sel}
    if cfg["method_id"] is None and len(methods) > 1:
        raise ValueError(f"wejście zawiera kilka metod {sorted(methods)}; ustaw config.method_id")
    best: dict[tuple[str, str], tuple[Optional[float], str]] = {}
    for p in sel:
        for c in p.pair:
            if c not in mats:
                raise ValueError(f"PairEvidence {p.pair_evidence_id} wskazuje materiał spoza obserwacji: {c}")
        cur = best.get(p.pair)
        cand = (p.score, p.pair_evidence_id)
        if cur is None:
            best[p.pair] = cand
        else:
            key = lambda t: (t[0] is not None, t[0] if t[0] is not None else 0.0, [-ord(ch) for ch in t[1]])  # noqa: E731
            best[p.pair] = max(cur, cand, key=key)
    return best


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
    groups: dict[str, list[str]] = {}
    for n in nodes:
        groups.setdefault(find(n), []).append(n)
    return sorted((sorted(g) for g in groups.values()), key=lambda g: g[0])


def find_bridges(nodes: Sequence[str], edges: Sequence[tuple[str, str]]) -> set[tuple[str, str]]:
    """Mosty (krawędzie, których usunięcie zwiększa liczbę składowych). Krawędzie jako uporządkowane pary."""
    base = len(_components(nodes, edges))
    out = set()
    for e in edges:
        rest = [x for x in edges if x != e]
        if len(_components(nodes, rest)) > base:
            out.add(e)
    return out


# ------------------------------------------------------------------ wynik

def build_result(observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                 config: Mapping[str, Any], *, seed: int = 0) -> ComponentsResult:
    cfg = resolve_config(config)
    mats = sorted({o.canonical_id for o in observations})
    matset = set(mats)
    best = _select_pairs(pair_evidence, cfg, matset)
    thr = cfg["threshold"]
    rel = Relation(cfg["relation"])

    strong, weak = {}, {}
    comparable: set[str] = set()
    for (a, b), (score, pid) in sorted(best.items()):
        if score is None:
            continue
        comparable.update((a, b))
        edge = Edge(source=a, target=b, relation=rel, pair_evidence_id=pid, weight=score)
        (strong if score >= thr else weak)[(a, b)] = edge

    comps = _components(mats, list(strong))
    assignments, clusters = [], []
    bridge_pairs = find_bridges(mats, list(strong))
    for members in comps:
        if len(members) == 1:
            m = members[0]
            st = AssignmentStatus.singleton if m in comparable else AssignmentStatus.insufficient_data
            assignments.append(Assignment(material_id=m, status=st))
            continue
        cid = _cluster_id(members)
        ms = set(members)
        es = tuple(e for k, e in strong.items() if k[0] in ms)
        clusters.append(ClusterEvidence(
            cluster_id=cid, members=tuple(members), edges=es,
            bridges=tuple(e for e in es if (e.source, e.target) in bridge_pairs),
            weakest=min(es, key=lambda e: (e.weight, e.source, e.target))))
        assignments += [Assignment(material_id=m, cluster_ids=[cid], status=AssignmentStatus.grouped) for m in members]
    assignments.sort(key=lambda a: a.material_id)

    run_cfg = {k: v for k, v in cfg.items() if k != "dataset_version"}
    run = ClusterRun(
        run_id=_run_id(cfg, seed, mats, [(a, b, s, pid) for (a, b), (s, pid) in sorted(best.items())]),
        dataset_version=cfg["dataset_version"], method_id=METHOD_ID, method_version=METHOD_VERSION,
        config=run_cfg, seed=seed, relation=rel, assignments=assignments, edges=list(strong.values()))
    return ComponentsResult(run=run, clusters=tuple(clusters), below_threshold=tuple(weak.values()))


def sweep(observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
          config: Mapping[str, Any], *, seed: int = 0) -> list[dict[str, Any]]:
    """Przesuwa próg przez zaobserwowane, dodatnie wyniki (malejąco). Dla każdego progu: grupy oraz
    zdarzenia scalenia (które krawędzie połączyły które poprzednie grupy; merge_edges==1 -> pojedynczy most)."""
    cfg = resolve_config({**config, "threshold": 1.0})
    mats = sorted({o.canonical_id for o in observations})
    best = _select_pairs(pair_evidence, cfg, set(mats))
    levels = sorted({s for s, _ in best.values() if s is not None and s > 0}, reverse=True)
    steps, prev = [], {m: (m,) for m in mats}
    for t in levels:
        res = build_result(observations, pair_evidence, {**config, "threshold": t}, seed=seed)
        groups = [tuple(c.members) for c in res.clusters]
        cur = dict(prev)
        for g in groups:
            for m in g:
                cur[m] = g
        merges = []
        for g in groups:
            old = sorted({prev[m] for m in g})
            if len(old) > 1 or old[0] != g:
                if len(old) > 1:
                    es = [e for e in next(c for c in res.clusters if tuple(c.members) == g).edges
                          if prev[e.source] != prev[e.target]]
                    merges.append({"into": g, "from": old, "joining_edges": [(e.source, e.target, e.weight) for e in es],
                                   "single_bridge": len(es) == 1 and all(len(o) > 1 for o in old)})
        steps.append({"threshold": t, "groups": groups, "merges": merges})
        prev = cur
    return steps


# ------------------------------------------------------------------ adapter

class ComponentsClusterer:
    spec = AdapterSpec(id="components-threshold-v1", kind=AdapterKind.clusterer, version=METHOD_VERSION,
                       description="Składowe spójne grafu progowego (score >= threshold)")

    def cluster(self, observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                config: Mapping[str, Any], *, seed: int = 0, optional: Mapping[str, Any] | None = None) -> ClusterRun:
        return build_result(observations, pair_evidence, config, seed=seed).run
