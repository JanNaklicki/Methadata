"""K05: społeczności Leiden na ważonym grafie podobieństw (Traag, Waltman, van Eck 2019).

Konstrukcja grafu (wszystko jawne w config i w wyniku):
  * wierzchołek = materiał (canonical_id) z co najmniej jednym wykonanym porównaniem (score != None);
    materiały bez takiego porównania dostają status insufficient_data (brak pary != podobieństwo 0);
  * krawędź = para ze score >= min_edge_weight (dla wybranej relacji i metody); wiele dowodów tej samej pary:
    wygrywa najwyższy score (remis: mniejszy pair_evidence_id) - jak w K02;
  * obniżenie wagi popularnych śladów: popularność wierzchołka d_i = liczba sąsiadów w grafie (ślad wspólny dla
    wielu materiałów tworzy hub/klikę). Krawędź i-j mnożymy przez min(1, (popular_degree / sqrt(d_i d_j)))^popularity_alpha.
    popularity_alpha = 0 wyłącza obniżanie. Waga po obniżeniu ("adjusted") trafia do Leiden; ClusterRun.edges niesie
    surowy score, a korektę widać w Edge-dowodach wyniku (EdgeEvidence).
Podział: leidenalg, jakość CPM (rozdzielczość = minimalna gęstość podobieństwa wewnątrz grupy, ta sama skala co
próg K01/K02; większa = mniejsze grupy) albo modularity (RB z rozdzielczością gamma). Seed steruje losowością.
Zgodność między powtórzeniami: ARI par partycji oraz częstość współwystępowania każdej pary w powtórzeniach.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from methadata.contracts import (AdapterKind, AdapterSpec, Assignment, AssignmentStatus, ClusterRun, Edge,
                                 Observation, PairEvidence, Relation)

METHOD_ID = "leiden"
METHOD_VERSION = "1"
DEFAULT_RELATION = Relation.shared_template
QUALITIES = ("cpm", "modularity")


@dataclass(frozen=True)
class EdgeEvidence:
    source: str
    target: str
    pair_evidence_id: str
    score: float             # surowy score PairEvidence
    adjusted: float          # waga użyta w Leiden (po obniżeniu popularnych śladów)
    popularity_factor: float


@dataclass(frozen=True)
class MaterialEvidence:
    material_id: str
    cluster_id: Optional[str]
    degree: int
    to_own: float                              # suma wag adjusted do własnej grupy
    to_other: tuple[tuple[str, float], ...]    # (cluster_id|"singleton:<id>", suma wag) malejąco: alternatywne przypisania
    strongest_edges: tuple[EdgeEvidence, ...]  # krawędzie z największą wagą (dowody przypisania)


@dataclass(frozen=True)
class ClusterEvidence:
    cluster_id: str
    members: tuple[str, ...]
    edges: tuple[EdgeEvidence, ...]    # krawędzie wewnątrz grupy
    weakest: Optional[EdgeEvidence]
    cut_edges: tuple[EdgeEvidence, ...]  # krawędzie wychodzące (kandydaci na alternatywne przypisania)
    stability: float                   # średnia częstość współwystępowania par członków w powtórzeniach


@dataclass(frozen=True)
class LeidenResult:
    run: ClusterRun
    config: dict[str, Any]
    graph_edges: tuple[EdgeEvidence, ...]
    clusters: tuple[ClusterEvidence, ...]
    materials: tuple[MaterialEvidence, ...]
    excluded: tuple[str, ...]
    seeds: tuple[int, ...]
    partitions: tuple[tuple[tuple[str, ...], ...], ...]   # partycja każdego powtórzenia (pierwsza = seed biegu)
    agreement: Mapping[str, float]                        # mean_ari, min_ari, identical_fraction
    co_membership: Mapping[tuple[str, str], float]        # para -> częstość wspólnej grupy (tylko pary z krawędzią)
    quality: float


# ------------------------------------------------------------------ konfiguracja

def resolve_config(config: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("similarity_version", "dataset_version"):
        if config.get(key) in (None, ""):
            raise ValueError(f"config.{key} jest wymagane (wejście musi wskazywać wersję cech/podobieństw)")
    q = config.get("quality", "cpm")
    if q not in QUALITIES:
        raise ValueError(f"config.quality musi być jednym z {QUALITIES}")
    res = config.get("resolution", 0.5 if q == "cpm" else 1.0)
    if isinstance(res, bool) or not isinstance(res, (int, float)) or res < 0:
        raise ValueError("config.resolution musi być liczbą >= 0")
    floor = config.get("min_edge_weight", 0.0)
    if isinstance(floor, bool) or not isinstance(floor, (int, float)) or not (0.0 <= floor <= 1.0):
        raise ValueError("config.min_edge_weight musi należeć do [0, 1]")
    alpha = config.get("popularity_alpha", 1.0)
    pop = config.get("popular_degree", 5)
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or alpha < 0:
        raise ValueError("config.popularity_alpha musi być liczbą >= 0")
    if isinstance(pop, bool) or not isinstance(pop, (int, float)) or pop < 1:
        raise ValueError("config.popular_degree musi być liczbą >= 1")
    reps = config.get("repeats", 10)
    if isinstance(reps, bool) or not isinstance(reps, int) or reps < 1:
        raise ValueError("config.repeats musi być liczbą całkowitą >= 1")
    return {
        "similarity_version": str(config["similarity_version"]),
        "dataset_version": str(config["dataset_version"]),
        "relation": Relation(config.get("relation", DEFAULT_RELATION)).value,
        "method_id": config.get("method_id"),
        "quality": q, "resolution": float(res), "min_edge_weight": float(floor),
        "popularity_alpha": float(alpha), "popular_degree": float(pop), "repeats": reps,
    }


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
    best: dict[tuple[str, str], tuple[Optional[float], str]] = {}
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


# ------------------------------------------------------------------ graf

def build_graph(observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                config: Mapping[str, Any]):
    """-> (cfg, wierzchołki posortowane, wykluczone, krawędzie EdgeEvidence posortowane)."""
    cfg = resolve_config(config)
    mats = sorted({o.canonical_id for o in observations})
    best = _select_pairs(pair_evidence, cfg, set(mats))
    scored = {k for k, (s, _) in best.items() if s is not None}
    has = {m for k in scored for m in k}
    excluded = [m for m in mats if m not in has]
    nodes = [m for m in mats if m in has]
    raw = {k: best[k] for k in scored if best[k][0] >= cfg["min_edge_weight"] and best[k][0] > 0}
    deg: dict[str, int] = {m: 0 for m in nodes}
    for a, b in raw:
        deg[a] += 1
        deg[b] += 1
    edges = []
    for (a, b), (s, pid) in sorted(raw.items()):
        d = math.sqrt(deg[a] * deg[b])
        f = 1.0 if d <= cfg["popular_degree"] else (cfg["popular_degree"] / d) ** cfg["popularity_alpha"]
        edges.append(EdgeEvidence(a, b, pid, s, s * f, f))
    return cfg, nodes, excluded, edges


# ------------------------------------------------------------------ podział

def _libs():
    try:
        import igraph
        import leidenalg
    except ImportError as exc:  # brak extra `graph`
        raise RuntimeError("Leiden wymaga extra `graph`: pip install 'methadata[graph]' (leidenalg, igraph)") from exc
    return igraph, leidenalg


def _partition(nodes: Sequence[str], edges: Sequence[EdgeEvidence], cfg: Mapping[str, Any], seed: int):
    igraph, leidenalg = _libs()
    idx = {m: i for i, m in enumerate(nodes)}
    g = igraph.Graph(n=len(nodes), edges=[(idx[e.source], idx[e.target]) for e in edges])
    g.es["weight"] = [e.adjusted for e in edges]
    kw = {"weights": "weight", "resolution_parameter": cfg["resolution"]}
    cls = leidenalg.CPMVertexPartition if cfg["quality"] == "cpm" else leidenalg.RBConfigurationVertexPartition
    part = leidenalg.find_partition(g, cls, n_iterations=-1, seed=seed, **kw)
    groups: dict[int, list[str]] = {}
    for m, c in zip(nodes, part.membership):
        groups.setdefault(c, []).append(m)
    return tuple(sorted(tuple(sorted(v)) for v in groups.values())), part.quality()


def _labels(partition, nodes):
    lab = {}
    for i, g in enumerate(partition):
        for m in g:
            lab[m] = i
    return [lab[m] for m in nodes]


def _ari(a: Sequence[int], b: Sequence[int]) -> float:
    n = len(a)
    if n < 2:
        return 1.0
    from collections import Counter
    c2 = lambda x: x * (x - 1) / 2  # noqa: E731
    cont = Counter(zip(a, b))
    sa, sb = Counter(a), Counter(b)
    idx = sum(c2(v) for v in cont.values())
    ea, eb = sum(c2(v) for v in sa.values()), sum(c2(v) for v in sb.values())
    exp = ea * eb / c2(n)
    mx = (ea + eb) / 2
    if mx == exp:
        return 1.0
    return (idx - exp) / (mx - exp)


def partition_agreement(p1: Sequence[Sequence[str]], p2: Sequence[Sequence[str]]) -> float:
    """Skorygowany indeks Randa dwóch partycji tego samego zbioru materiałów."""
    nodes = sorted(m for g in p1 for m in g)
    if nodes != sorted(m for g in p2 for m in g):
        raise ValueError("partycje dotyczą różnych zbiorów materiałów")
    return _ari(_labels(p1, nodes), _labels(p2, nodes))


# ------------------------------------------------------------------ wynik

def build_result(observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                 config: Mapping[str, Any], *, seed: int = 0) -> LeidenResult:
    cfg, nodes, excluded, edges = build_graph(observations, pair_evidence, config)
    rel = Relation(cfg["relation"])
    seeds = tuple(range(seed, seed + cfg["repeats"]))
    parts, quals = [], []
    for s in seeds:
        p, q = _partition(nodes, edges, cfg, s) if nodes else ((), 0.0)
        parts.append(p)
        quals.append(q)
    main = parts[0]

    # zgodność między powtórzeniami
    labs = [_labels(p, nodes) for p in parts]
    aris = [_ari(labs[0], l) for l in labs[1:]] + [_ari(labs[i], labs[j]) for i in range(1, len(labs))
                                                    for j in range(i + 1, len(labs))]
    agreement = {"mean_ari": sum(aris) / len(aris) if aris else 1.0, "min_ari": min(aris) if aris else 1.0,
                 "identical_fraction": sum(1 for p in parts if p == main) / len(parts), "repeats": float(len(parts))}
    together = {}
    for e in edges:
        k = (e.source, e.target)
        together[k] = sum(1 for l in labs if l[nodes.index(k[0])] == l[nodes.index(k[1])]) / len(labs)

    emap = {(e.source, e.target): e for e in edges}
    cid_of: dict[str, str] = {}
    clusters, run_edges, assignments = [], [], []
    for m in excluded:
        assignments.append(Assignment(material_id=m, status=AssignmentStatus.insufficient_data))
    for g in main:
        if len(g) == 1:
            assignments.append(Assignment(material_id=g[0], status=AssignmentStatus.singleton))
            continue
        cid = _cluster_id(g)
        for m in g:
            cid_of[m] = cid
        gs = set(g)
        inner = tuple(e for e in edges if e.source in gs and e.target in gs)
        cut = tuple(e for e in edges if (e.source in gs) != (e.target in gs))
        stab = [together[(e.source, e.target)] for e in inner]
        clusters.append(ClusterEvidence(
            cluster_id=cid, members=g, edges=inner,
            weakest=min(inner, key=lambda e: (e.adjusted, e.source, e.target)) if inner else None,
            cut_edges=cut, stability=sum(stab) / len(stab) if stab else 0.0))
        run_edges += [Edge(source=e.source, target=e.target, relation=rel, pair_evidence_id=e.pair_evidence_id,
                           weight=e.score) for e in inner]
        assignments += [Assignment(material_id=m, cluster_ids=[cid], status=AssignmentStatus.grouped) for m in g]
    members_of = {c.cluster_id: set(c.members) for c in clusters}
    mats = []
    for m in nodes:
        mine = [e for e in edges if m in (e.source, e.target)]
        own = cid_of.get(m)
        to: dict[str, float] = {}
        for e in mine:
            o = e.target if e.source == m else e.source
            key = cid_of.get(o, f"singleton:{o}")
            to[key] = to.get(key, 0.0) + e.adjusted
        own_w = to.pop(own, 0.0) if own else 0.0
        mats.append(MaterialEvidence(
            material_id=m, cluster_id=own, degree=len(mine), to_own=own_w,
            to_other=tuple(sorted(to.items(), key=lambda kv: (-kv[1], kv[0]))),
            strongest_edges=tuple(sorted(mine, key=lambda e: (-e.adjusted, e.source, e.target))[:3])))
    assignments.sort(key=lambda a: a.material_id)

    run_cfg = dict(cfg)
    run_cfg.pop("dataset_version")
    run_cfg["seeds"] = list(seeds)
    run = ClusterRun(
        run_id=f"run-{METHOD_ID}-" + _digest([cfg, seed, [(e.source, e.target, e.pair_evidence_id, e.score)
                                                         for e in edges], nodes, excluded]),
        dataset_version=cfg["dataset_version"], method_id=METHOD_ID, method_version=METHOD_VERSION,
        config=run_cfg, seed=seed, relation=rel, assignments=assignments, edges=run_edges)
    return LeidenResult(run=run, config=cfg, graph_edges=tuple(edges), clusters=tuple(clusters),
                        materials=tuple(mats), excluded=tuple(excluded), seeds=seeds, partitions=tuple(parts),
                        agreement=agreement, co_membership=together, quality=quals[0])


def sweep(observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence], config: Mapping[str, Any],
          resolutions: Sequence[float], seeds: Sequence[int]) -> list[dict[str, Any]]:
    """Dla każdej rozdzielczości: liczba grup, rozmiar największej, zgodność (ARI) między seedami i partycja."""
    rows = []
    for r in resolutions:
        res = build_result(observations, pair_evidence, {**config, "resolution": r, "repeats": len(seeds)},
                           seed=min(seeds))
        sizes = sorted((len(g) for g in res.partitions[0]), reverse=True)
        rows.append({"resolution": r, "seeds": list(res.seeds), "n_groups": sum(1 for s in sizes if s > 1),
                     "n_singletons": sum(1 for s in sizes if s == 1), "largest": sizes[0] if sizes else 0,
                     "mean_ari": res.agreement["mean_ari"], "min_ari": res.agreement["min_ari"],
                     "partition": res.partitions[0]})
    return rows


def explain_pair(result: LeidenResult, a: str, b: str) -> dict[str, Any]:
    """Dowody dla pary (zwłaszcza granicznej): krawędź bezpośrednia, przypisania, alternatywy i stabilność."""
    a, b = sorted((a, b))
    mats = {m.material_id: m for m in result.materials}
    for m in (a, b):
        if m not in mats:
            raise ValueError(f"materiał {m} nie jest wierzchołkiem grafu")
    direct = next((e for e in result.graph_edges if (e.source, e.target) == (a, b)), None)
    same = mats[a].cluster_id is not None and mats[a].cluster_id == mats[b].cluster_id
    labs = {}
    for p in result.partitions:
        lab = {m: i for i, g in enumerate(p) for m in g}
        labs[p] = lab[a] == lab[b]
    freq = sum(1 for v in labs.values() if v) / len(labs)
    return {"pair": (a, b), "direct_edge": direct, "same_cluster": same,
            "co_membership_frequency": freq,
            "assignment": {m: {"cluster_id": mats[m].cluster_id, "to_own": mats[m].to_own,
                               "alternatives": mats[m].to_other, "strongest_edges": mats[m].strongest_edges}
                           for m in (a, b)}}


# ------------------------------------------------------------------ adaptery

class LeidenClusterer:
    spec = AdapterSpec(id="leiden-v1", kind=AdapterKind.clusterer, version=METHOD_VERSION,
                       description="Społeczności Leiden na ważonym grafie podobieństw z obniżaniem wagi popularnych śladów")

    def cluster(self, observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                config: Mapping[str, Any], *, seed: int = 0, optional: Mapping[str, Any] | None = None) -> ClusterRun:
        return build_result(observations, pair_evidence, config, seed=seed).run


def get_adapters():
    return [LeidenClusterer()]


ADAPTERS = get_adapters()
