"""K07: nakładające się grupy - perkolacja klik (Palla i in. 2005), bez zewnętrznych zależności.

Krok po kroku (wszystko jawne w config i w wyniku):
  * krawędź = para ze score >= threshold (wybrana relacja i metoda); wiele dowodów tej samej pary: wygrywa
    najwyższy score (remis: mniejszy pair_evidence_id); score None = brak porównania, nie zero;
  * społeczność = suma maksymalnych klik o rozmiarze >= k, połączonych, gdy dzielą >= k-1 wierzchołków.
    Wierzchołek może należeć do wielu społeczności (Assignment.cluster_ids ma wiele ID);
  * wierzchołek z krawędzią, ale bez k-kliki: status singleton; bez żadnego wykonanego porównania:
    insufficient_data.
Oś relacji: ClusterRun ma jedną relację, więc dla wielu relacji powstaje osobny przebieg na relację
(build_axis), a przypisania są raportowane jako materiał -> relacja -> ID grup.
Współprzynależność pary: |G(a) & G(b)| / |G(a) | G(b)| (Jaccard zbiorów grup), razem z listą wspólnych grup.
small_family_loss: ile małych rodzin (składowych grafu krawędzi) nie ma k-kliki i znika przy wymogu trójkąta.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from methadata.contracts import (AdapterKind, AdapterSpec, Assignment, AssignmentStatus, ClusterRun, Edge,
                                 Observation, PairEvidence, Relation)

METHOD_ID = "overlapping"
METHOD_VERSION = "1"
DEFAULT_RELATION = Relation.shared_template


@dataclass(frozen=True)
class EdgeEvidence:
    source: str
    target: str
    pair_evidence_id: str
    score: float


@dataclass(frozen=True)
class ClusterEvidence:
    cluster_id: str
    members: tuple[str, ...]
    cliques: tuple[tuple[str, ...], ...]       # maksymalne kliki >= k tworzące społeczność
    edges: tuple[EdgeEvidence, ...]            # krawędzie między członkami
    weakest: Optional[EdgeEvidence]
    shared_with: tuple[tuple[str, tuple[str, ...]], ...]  # (inna grupa, wspólni członkowie) - nakładanie


@dataclass(frozen=True)
class MembershipEvidence:
    material_id: str
    cluster_ids: tuple[str, ...]
    cliques: tuple[tuple[str, tuple[str, ...]], ...]  # (cluster_id, klika, w której materiał uczestniczy)
    degree: int


@dataclass(frozen=True)
class OverlappingResult:
    run: ClusterRun
    config: dict[str, Any]
    graph_edges: tuple[EdgeEvidence, ...]
    clusters: tuple[ClusterEvidence, ...]
    materials: tuple[MembershipEvidence, ...]
    excluded: tuple[str, ...]
    overlapping: tuple[str, ...]               # materiały z >= 2 grupami
    co_membership: Mapping[tuple[str, str], float]  # pary z krawędzią lub wspólną grupą


def resolve_config(config: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("similarity_version", "dataset_version"):
        if config.get(key) in (None, ""):
            raise ValueError(f"config.{key} jest wymagane (wejście musi wskazywać wersję cech/podobieństw)")
    thr = config.get("threshold", 0.5)
    if isinstance(thr, bool) or not isinstance(thr, (int, float)) or not (0.0 <= thr <= 1.0):
        raise ValueError("config.threshold musi należeć do [0, 1]")
    k = config.get("k", 3)
    if isinstance(k, bool) or not isinstance(k, int) or k < 2:
        raise ValueError("config.k musi być liczbą całkowitą >= 2")
    rels = config.get("relations")
    rel = Relation(config.get("relation", DEFAULT_RELATION)).value
    return {"similarity_version": str(config["similarity_version"]), "dataset_version": str(config["dataset_version"]),
            "relation": rel, "relations": [Relation(r).value for r in rels] if rels else None,
            "method_id": config.get("method_id"), "threshold": float(thr), "k": k}


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _cluster_id(members: Sequence[str]) -> str:
    return "cl-" + hashlib.sha256("\x1f".join(sorted(members)).encode()).hexdigest()[:12]


def _graph(observations, pair_evidence, cfg, relation):
    mats = sorted({o.canonical_id for o in observations})
    mset = set(mats)
    sel = [p for p in pair_evidence if p.relation.value == relation
           and (cfg["method_id"] is None or p.method_id == cfg["method_id"])]
    methods = {p.method_id for p in sel}
    if cfg["method_id"] is None and len(methods) > 1:
        raise ValueError(f"wejście zawiera kilka metod {sorted(methods)}; ustaw config.method_id")
    best: dict[tuple[str, str], tuple[float, str]] = {}
    for p in sel:
        for c in p.pair:
            if c not in mset:
                raise ValueError(f"PairEvidence {p.pair_evidence_id} wskazuje materiał spoza obserwacji: {c}")
        if p.score is None:
            continue
        cand = (p.score, p.pair_evidence_id)
        cur = best.get(p.pair)
        if cur is None or cand[0] > cur[0] or (cand[0] == cur[0] and cand[1] < cur[1]):
            best[p.pair] = cand
    has = {m for k in best for m in k}
    excluded = [m for m in mats if m not in has]
    edges = [EdgeEvidence(a, b, pid, s) for (a, b), (s, pid) in sorted(best.items())
             if s >= cfg["threshold"] and s > 0]
    return mats, excluded, edges, has


def _maximal_cliques(adj: Mapping[str, set[str]]):
    out: list[tuple[str, ...]] = []

    def bk(r, p, x):
        if not p and not x:
            out.append(tuple(sorted(r)))
            return
        piv = max(p | x, key=lambda u: (len(p & adj[u]), u))
        for v in sorted(p - adj[piv]):
            bk(r | {v}, p & adj[v], x & adj[v])
            p = p - {v}
            x = x | {v}

    bk(set(), set(adj), set())
    return sorted(out)


def _percolate(edges: Sequence[EdgeEvidence], k: int) -> list[list[tuple[str, ...]]]:
    """-> lista społeczności; każda to lista maksymalnych klik (>= k) połączonych relacją k-1 wspólnych wierzchołków."""
    adj: dict[str, set[str]] = {}
    for e in edges:
        adj.setdefault(e.source, set()).add(e.target)
        adj.setdefault(e.target, set()).add(e.source)
    cl = [c for c in _maximal_cliques(adj) if len(c) >= k]
    parent = list(range(len(cl)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(cl)):
        for j in range(i + 1, len(cl)):
            if len(set(cl[i]) & set(cl[j])) >= k - 1:
                parent[find(i)] = find(j)
    groups: dict[int, list[tuple[str, ...]]] = {}
    for i, c in enumerate(cl):
        groups.setdefault(find(i), []).append(c)
    return sorted(groups.values(), key=lambda g: sorted(g))


def _components(nodes: Sequence[str], edges: Sequence[EdgeEvidence]) -> list[list[str]]:
    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for e in edges:
        parent[find(e.source)] = find(e.target)
    comps: dict[str, list[str]] = {}
    for n in nodes:
        comps.setdefault(find(n), []).append(n)
    return sorted(sorted(c) for c in comps.values())


def small_family_loss(observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                      config: Mapping[str, Any], *, max_size: int = 4) -> dict[str, Any]:
    """Ile małych rodzin (składowych grafu krawędzi o rozmiarze 2..max_size) znika przy wymogu k-kliki (k z config)."""
    cfg = resolve_config(config)
    _, _, edges, _ = _graph(observations, pair_evidence, cfg, cfg["relation"])
    nodes = sorted({m for e in edges for m in (e.source, e.target)})
    small = [c for c in _components(nodes, edges) if 2 <= len(c) <= max_size]
    kept = {m for com in _percolate(edges, cfg["k"]) for cl in com for m in cl}
    lost = [c for c in small if not set(c) & kept]
    return {"k": cfg["k"], "max_size": max_size, "small_families": len(small), "lost": len(lost),
            "kept": len(small) - len(lost), "lost_families": [tuple(c) for c in lost]}


def build_result(observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                 config: Mapping[str, Any], *, seed: int = 0, relation: Optional[str] = None) -> OverlappingResult:
    cfg = resolve_config(config)
    rel = Relation(relation or cfg["relation"])
    cfg = {**cfg, "relation": rel.value}
    mats, excluded, edges, has = _graph(observations, pair_evidence, cfg, rel.value)
    emap = {(e.source, e.target): e for e in edges}
    comms = _percolate(edges, cfg["k"])

    clusters_raw = []
    for com in comms:
        members = tuple(sorted({m for c in com for m in c}))
        clusters_raw.append((_cluster_id(members), members, tuple(sorted(com))))
    clusters_raw.sort()
    gid: dict[str, list[str]] = {}
    for cid, members, _ in clusters_raw:
        for m in members:
            gid.setdefault(m, []).append(cid)
    by_id = {cid: set(members) for cid, members, _ in clusters_raw}

    clusters, used_edges = [], {}
    for cid, members, cliques in clusters_raw:
        ms = set(members)
        inner = tuple(e for e in edges if e.source in ms and e.target in ms)
        for e in inner:
            used_edges[(e.source, e.target)] = e
        shared = tuple((o, tuple(sorted(ms & by_id[o]))) for o in sorted(by_id) if o != cid and ms & by_id[o])
        clusters.append(ClusterEvidence(
            cluster_id=cid, members=members, cliques=cliques, edges=inner,
            weakest=min(inner, key=lambda e: (e.score, e.source, e.target)) if inner else None, shared_with=shared))

    deg = {m: 0 for m in mats}
    for e in edges:
        deg[e.source] += 1
        deg[e.target] += 1
    assignments, materials = [], []
    for m in mats:
        if m in gid:
            assignments.append(Assignment(material_id=m, cluster_ids=sorted(gid[m]), status=AssignmentStatus.grouped))
        elif m in has:
            assignments.append(Assignment(material_id=m, status=AssignmentStatus.singleton))
        else:
            assignments.append(Assignment(material_id=m, status=AssignmentStatus.insufficient_data))
        cl = tuple((cid, c) for cid, _, cliques in clusters_raw for c in cliques if m in c)
        materials.append(MembershipEvidence(m, tuple(sorted(gid.get(m, []))), cl, deg[m]))

    co: dict[tuple[str, str], float] = {}
    keys = set(emap)
    for ids_ in by_id.values():
        s = sorted(ids_)
        keys |= {(a, b) for i, a in enumerate(s) for b in s[i + 1:]}
    for a, b in sorted(keys):
        co[(a, b)] = _jaccard(gid.get(a, []), gid.get(b, []))

    run_cfg = dict(cfg)
    run_cfg.pop("dataset_version")
    run = ClusterRun(
        run_id=f"run-{METHOD_ID}-" + _digest([cfg, seed, [(e.source, e.target, e.pair_evidence_id, e.score)
                                                         for e in edges], mats, excluded]),
        dataset_version=cfg["dataset_version"], method_id=METHOD_ID, method_version=METHOD_VERSION,
        config=run_cfg, seed=seed, relation=rel, assignments=assignments,
        edges=[Edge(source=e.source, target=e.target, relation=rel, pair_evidence_id=e.pair_evidence_id,
                    weight=e.score) for e in used_edges.values()])
    return OverlappingResult(run=run, config=cfg, graph_edges=tuple(edges), clusters=tuple(clusters),
                             materials=tuple(materials), excluded=tuple(excluded),
                             overlapping=tuple(m for m in mats if len(gid.get(m, [])) >= 2), co_membership=co)


def _jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if sa | sb else 0.0


def build_axis(observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
               config: Mapping[str, Any], *, seed: int = 0) -> dict[str, Any]:
    """Przebieg na każdą relację z config.relations: {'results': {relacja: wynik},
    'axis': {materiał: {relacja: [id grup]}}}. Ten sam materiał może mieć różne grupy na różnych relacjach."""
    cfg = resolve_config(config)
    rels = cfg["relations"] or [cfg["relation"]]
    results = {r: build_result(observations, pair_evidence, config, seed=seed, relation=r) for r in rels}
    axis: dict[str, dict[str, list[str]]] = {}
    for r, res in results.items():
        for a in res.run.assignments:
            axis.setdefault(a.material_id, {})[r] = list(a.cluster_ids)
    return {"results": results, "axis": axis}


def co_membership(result: OverlappingResult, a: str, b: str) -> float:
    a, b = sorted((a, b))
    mats = {m.material_id: m for m in result.materials}
    for m in (a, b):
        if m not in mats:
            raise ValueError(f"materiał {m} nie należy do obserwacji")
    return _jaccard(mats[a].cluster_ids, mats[b].cluster_ids)


def explain_pair(result: OverlappingResult, a: str, b: str) -> dict[str, Any]:
    a, b = sorted((a, b))
    score = co_membership(result, a, b)
    mats = {m.material_id: m for m in result.materials}
    shared = sorted(set(mats[a].cluster_ids) & set(mats[b].cluster_ids))
    return {"pair": (a, b), "direct_edge": next((e for e in result.graph_edges if (e.source, e.target) == (a, b)), None),
            "co_membership": score, "shared_clusters": shared,
            "memberships": {m: {"cluster_ids": mats[m].cluster_ids, "cliques": mats[m].cliques} for m in (a, b)}}


class CliquePercolationClusterer:
    spec = AdapterSpec(id="overlapping-v1", kind=AdapterKind.clusterer, version=METHOD_VERSION,
                       description="Nakładające się grupy: perkolacja klik, wiele ID grup na obserwację")

    def cluster(self, observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                config: Mapping[str, Any], *, seed: int = 0, optional: Mapping[str, Any] | None = None) -> ClusterRun:
        return build_result(observations, pair_evidence, config, seed=seed).run


def get_adapters():
    return [CliquePercolationClusterer()]


ADAPTERS = get_adapters()
