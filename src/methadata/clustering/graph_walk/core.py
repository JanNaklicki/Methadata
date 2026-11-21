"""K15: spacery losowe z restartem (ranking sąsiadów) i Infomap (społeczności) na grafie dwudzielnym materiał–ślad.

Wejście: eksport E14 (`methadata.rare_motifs.matrix/1`, artefakt role=graph_edges; pole graph.edges + traces[df, idf])
oraz wersja cech/podobieństw (config.similarity_version, config.dataset_version - wymagane).
Graf: wierzchołki materiałów i śladów; krawędź materiał–ślad z wagą 1 ("binary") albo idf śladu ("idf").
Obniżanie popularnych węzłów: ślady o df < min_trace_df lub df > max_trace_df albo wskazane w drop_traces są pomijane
(popularny ślad łączy wszystko ze wszystkim i zaśmieca zarówno ranking, jak i społeczności).

Dwa osobne wyniki (nie ma jednego "klastra z rankingu"):
  * ranking: dla każdego materiału personalizowany PageRank (restart c do materiału startowego), wynik = ranking
    pozostałych materiałów; prawdopodobieństwo spaceru, nie podobieństwo;
  * podział: igraph.community_infomap na całym grafie dwudzielnym (ślady są wierzchołkami; grupa = materiały
    społeczności, >= 2 materiały). Seed + powtórzenia dają zgodność (ARI) między biegami.
Ścieżka materiał–ślad–materiał (i pośrednia materiał–ślad–materiał–ślad–materiał) z prawdopodobieństwem spaceru
i identyfikatorami źródłowymi krawędzi (observation_id, feature_ids, evidence_refs z E14).
"""
from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from methadata.contracts import (AdapterKind, AdapterSpec, Assignment, AssignmentStatus, ClusterRun, Edge,
                                 Observation, OptionalInput, PairEvidence, Relation)

METHOD_ID = "graph-walk"
METHOD_VERSION = "1"
EXPORT_SCHEMA = "methadata.rare_motifs.matrix/1"
WEIGHTINGS = ("binary", "idf")


# ------------------------------------------------------------------ konfiguracja

def resolve_config(config: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("similarity_version", "dataset_version"):
        if config.get(key) in (None, ""):
            raise ValueError(f"config.{key} jest wymagane (wejście musi wskazywać wersję cech/podobieństw)")
    w = config.get("weighting", "idf")
    if w not in WEIGHTINGS:
        raise ValueError(f"config.weighting musi być jednym z {WEIGHTINGS}")
    restart = config.get("restart", 0.3)
    if isinstance(restart, bool) or not isinstance(restart, (int, float)) or not (0.0 < restart < 1.0):
        raise ValueError("config.restart musi należeć do (0, 1)")
    out = {"similarity_version": str(config["similarity_version"]), "dataset_version": str(config["dataset_version"]),
           "weighting": w, "restart": float(restart), "relation": Relation(config.get("relation", "shared_template")).value}
    for key, default, lo in (("min_trace_df", 2, 1), ("max_trace_df", None, 2), ("trials", 10, 1), ("repeats", 5, 1),
                             ("top_k", 5, 1), ("max_paths", 5, 1)):
        v = config.get(key, default)
        if v is None and key == "max_trace_df":
            out[key] = None
            continue
        if isinstance(v, bool) or not isinstance(v, int) or v < lo:
            raise ValueError(f"config.{key} musi być liczbą całkowitą >= {lo}")
        out[key] = v
    drop = config.get("drop_traces", ())
    if isinstance(drop, str) or not all(isinstance(t, str) for t in drop):
        raise ValueError("config.drop_traces musi być listą identyfikatorów śladów")
    out["drop_traces"] = sorted(set(drop))
    return out


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _cluster_id(members: Sequence[str]) -> str:
    return "cl-" + hashlib.sha256("\x1f".join(sorted(members)).encode()).hexdigest()[:12]


# ------------------------------------------------------------------ graf

@dataclass(frozen=True)
class EdgeRef:
    """Krawędź materiał–ślad z identyfikatorami źródłowymi E14."""
    material: str
    trace: str
    weight: float
    observation_id: str
    feature_ids: tuple[str, ...]
    evidence_refs: tuple[Any, ...]


class BipartiteGraph:
    def __init__(self, materials: Sequence[str], traces: Mapping[str, dict], edges: Sequence[EdgeRef]):
        self.materials = sorted(materials)
        self.traces = dict(traces)                       # trace_id -> {kind,label,df}
        self.edges = {(e.material, e.trace): e for e in edges}
        self.of_material: dict[str, dict[str, float]] = {m: {} for m in self.materials}
        self.of_trace: dict[str, dict[str, float]] = {t: {} for t in self.traces}
        for e in edges:
            self.of_material[e.material][e.trace] = e.weight
            self.of_trace[e.trace][e.material] = e.weight
        self.of_trace = {t: v for t, v in self.of_trace.items() if v}
        self.traces = {t: v for t, v in self.traces.items() if t in self.of_trace}

    def strength(self, node: str) -> float:
        return sum((self.of_material.get(node) or self.of_trace.get(node) or {}).values())

    def neighbours(self, node: str) -> dict[str, float]:
        return self.of_material[node] if node in self.of_material else self.of_trace[node]

    def is_material(self, node: str) -> bool:
        return node in self.of_material


def build_graph(export: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[dict[str, Any], BipartiteGraph]:
    cfg = resolve_config(config)
    if export.get("schema") != EXPORT_SCHEMA:
        raise ValueError(f"nieznany schemat eksportu grafu: {export.get('schema')!r} (oczekiwano {EXPORT_SCHEMA})")
    mats = [n["id"] for n in export["graph"]["nodes"] if n["part"] == "material"]
    info = {t["trace_id"]: t for t in export["traces"]}
    drop = set(cfg["drop_traces"])
    kept = {t for t, i in info.items() if t not in drop and i["df"] >= cfg["min_trace_df"]
            and (cfg["max_trace_df"] is None or i["df"] <= cfg["max_trace_df"])}
    edges = []
    for e in export["graph"]["edges"]:
        if e["trace"] not in kept:
            continue
        w = 1.0 if cfg["weighting"] == "binary" else float(info[e["trace"]]["idf"])
        if w <= 0:       # ślad obecny w każdym materiale (idf=0) nie niesie informacji
            continue
        edges.append(EdgeRef(e["material"], e["trace"], w, e.get("observation_id", ""),
                             tuple(e.get("feature_ids", ())), tuple(e.get("evidence_refs", ()))))
    traces = {t: {"kind": info[t]["kind"], "label": info[t]["label"], "df": info[t]["df"]} for t in kept}
    return cfg, BipartiteGraph(mats, traces, edges)


def popular_traces(export: Mapping[str, Any], *, top: int) -> list[str]:
    """Identyfikatory `top` najpopularniejszych śladów (df malejąco, remis: id) - kandydaci do usunięcia."""
    info = sorted(export["traces"], key=lambda t: (-t["df"], t["trace_id"]))
    return [t["trace_id"] for t in info[:top]]


# ------------------------------------------------------------------ spacer losowy z restartem

def walk_scores(g: BipartiteGraph, start: str, restart: float, *, tol: float = 1e-10, max_iter: int = 1000) -> dict[str, float]:
    """Stacjonarny rozkład spaceru z restartem do `start` (iteracja potęgowa; krok proporcjonalny do wag krawędzi)."""
    if start not in g.of_material:
        raise ValueError(f"{start} nie jest materiałem grafu")
    p = {start: 1.0}
    strength = {}
    for _ in range(max_iter):
        nxt: dict[str, float] = {start: restart}
        for node, mass in p.items():
            nb = g.neighbours(node)
            if not nb:
                nxt[start] = nxt.get(start, 0.0) + (1 - restart) * mass
                continue
            s = strength.get(node)
            if s is None:
                s = strength[node] = sum(nb.values())
            for n2, w in nb.items():
                nxt[n2] = nxt.get(n2, 0.0) + (1 - restart) * mass * w / s
        diff = sum(abs(nxt.get(k, 0.0) - p.get(k, 0.0)) for k in set(p) | set(nxt))
        p = nxt
        if diff < tol:
            break
    return p


def rank_neighbours(g: BipartiteGraph, start: str, restart: float, top_k: int) -> list[tuple[str, float]]:
    sc = walk_scores(g, start, restart)
    ranked = [(m, s) for m, s in sc.items() if g.is_material(m) and m != start]
    ranked.sort(key=lambda kv: (-kv[1], kv[0]))
    return ranked[:top_k]


# ------------------------------------------------------------------ Infomap

def _infomap(g: BipartiteGraph, trials: int, seed: int) -> tuple[tuple[tuple[str, ...], ...], dict[str, int]]:
    try:
        import igraph
    except ImportError as exc:
        raise RuntimeError("Infomap wymaga extra `graph`: pip install 'methadata[graph]' (igraph)") from exc
    nodes = [m for m in g.materials if g.of_material[m]] + sorted(g.of_trace)
    if not nodes:
        return (), {}
    idx = {n: i for i, n in enumerate(nodes)}
    es = sorted(g.edges)
    gr = igraph.Graph(n=len(nodes), edges=[(idx[m], idx[t]) for m, t in es if m in idx])
    weights = [g.edges[k].weight for k in es if k[0] in idx]
    state = random.getstate()
    random.seed(seed)
    try:
        part = gr.community_infomap(edge_weights=weights, trials=trials)
    finally:
        random.setstate(state)
    comm: dict[int, list[str]] = {}
    for n, c in zip(nodes, part.membership):
        comm.setdefault(c, []).append(n)
    mat_groups = sorted(tuple(sorted(n for n in v if g.is_material(n))) for v in comm.values())
    mat_groups = tuple(x for x in mat_groups if x)
    label = {n: i for i, grp in enumerate(mat_groups) for n in grp}
    return mat_groups, label


def _ari(a: Sequence[int], b: Sequence[int]) -> float:
    n = len(a)
    if n < 2:
        return 1.0
    c2 = lambda x: x * (x - 1) / 2  # noqa: E731
    idx = sum(c2(v) for v in Counter(zip(a, b)).values())
    ea, eb = sum(c2(v) for v in Counter(a).values()), sum(c2(v) for v in Counter(b).values())
    exp = ea * eb / c2(n)
    mx = (ea + eb) / 2
    return 1.0 if mx == exp else (idx - exp) / (mx - exp)


# ------------------------------------------------------------------ ścieżki

@dataclass(frozen=True)
class Path:
    kind: str                       # "direct" (M-T-M) | "indirect" (M-T-M-T-M)
    nodes: tuple[str, ...]
    probability: float              # prawdopodobieństwo przejścia spaceru wzdłuż ścieżki (bez restartu)
    edges: tuple[EdgeRef, ...]      # krawędzie ze źródłowymi identyfikatorami


def find_paths(g: BipartiteGraph, a: str, b: str, *, max_paths: int = 5) -> list[Path]:
    """Ścieżki materiał–ślad–materiał (direct) oraz pośrednie a–t1–m–t2–b (indirect) bez powtórzeń wierzchołków,
    malejąco wg prawdopodobieństwa spaceru (iloczyn wag/siły na każdym kroku)."""
    for m in (a, b):
        if m not in g.of_material:
            raise ValueError(f"{m} nie jest materiałem grafu")
    out: list[Path] = []
    sm = {m: g.strength(m) for m in g.of_material}
    st = {t: g.strength(t) for t in g.of_trace}
    for t1, w1 in g.of_material[a].items():
        p1 = w1 / sm[a]
        if b in g.of_trace[t1]:
            out.append(Path("direct", (a, t1, b), p1 * g.of_trace[t1][b] / st[t1],
                            (g.edges[(a, t1)], g.edges[(b, t1)])))
        for m, wm in g.of_trace[t1].items():
            if m in (a, b):
                continue
            p2 = p1 * wm / st[t1]
            for t2, w2 in g.of_material[m].items():
                if t2 == t1 or b not in g.of_trace[t2]:
                    continue
                prob = p2 * w2 / sm[m] * g.of_trace[t2][b] / st[t2]
                out.append(Path("indirect", (a, t1, m, t2, b), prob,
                                (g.edges[(a, t1)], g.edges[(m, t1)], g.edges[(m, t2)], g.edges[(b, t2)])))
    out.sort(key=lambda p: (-p.probability, p.nodes))
    return out[:max_paths]


# ------------------------------------------------------------------ wynik

@dataclass(frozen=True)
class Neighbour:
    material_id: str
    score: float
    path: Optional[Path]            # najlepsza ścieżka wyjaśniająca wynik


@dataclass(frozen=True)
class MaterialEvidence:
    material_id: str
    cluster_id: Optional[str]
    ranking: tuple[Neighbour, ...]                 # ranking sąsiadów ze spaceru z restartem (niezależny od podziału)
    traces: tuple[tuple[str, str], ...]            # (trace_id, label) śladów materiału w grafie


@dataclass(frozen=True)
class ClusterEvidence:
    cluster_id: str
    members: tuple[str, ...]
    traces: tuple[tuple[str, int, int], ...]       # (trace_id, członkowie grupy ze śladem, df w korpusie)
    stability: float                               # średnia częstość wspólnej grupy par członków w powtórzeniach


@dataclass(frozen=True)
class GraphWalkResult:
    run: ClusterRun
    config: dict[str, Any]
    graph: BipartiteGraph
    ranking: Mapping[str, tuple[Neighbour, ...]]
    partition: tuple[tuple[str, ...], ...]         # podział Infomap (grupy materiałów, także 1-elementowe)
    clusters: tuple[ClusterEvidence, ...]
    materials: tuple[MaterialEvidence, ...]
    excluded: tuple[str, ...]
    seeds: tuple[int, ...]
    partitions: tuple[tuple[tuple[str, ...], ...], ...]
    agreement: Mapping[str, float]


def build_result(observations: Sequence[Observation], export: Mapping[str, Any], config: Mapping[str, Any], *,
                 seed: int = 0, pair_evidence: Sequence[PairEvidence] = ()) -> GraphWalkResult:
    cfg, g = build_graph(export, config)
    obs_mats = {o.canonical_id for o in observations}
    unknown = [m for m in g.materials if m not in obs_mats]
    if unknown:
        raise ValueError(f"graf wskazuje materiały spoza obserwacji: {unknown[:3]}")
    rel = Relation(cfg["relation"])
    mats = sorted(obs_mats)
    linked = [m for m in mats if g.of_material.get(m)]
    excluded = [m for m in mats if m not in set(linked)]

    seeds = tuple(range(seed, seed + cfg["repeats"]))
    parts = [_infomap(g, cfg["trials"], s) for s in seeds]
    main, label = parts[0]
    labs = [[p[1].get(m, -1) for m in linked] for p in parts]
    aris = [_ari(labs[i], labs[j]) for i in range(len(labs)) for j in range(i + 1, len(labs))]
    agreement = {"mean_ari": sum(aris) / len(aris) if aris else 1.0, "min_ari": min(aris) if aris else 1.0,
                 "identical_fraction": sum(1 for p in parts if p[0] == main) / len(parts), "repeats": float(len(parts))}

    ranking = {}
    for m in linked:
        ranking[m] = tuple(Neighbour(n, s, (find_paths(g, m, n, max_paths=1) or [None])[0])
                           for n, s in rank_neighbours(g, m, cfg["restart"], cfg["top_k"]))

    pe_ids = {p.pair: p.pair_evidence_id for p in pair_evidence if p.pair_evidence_id}
    clusters, assignments, run_edges, cid_of = [], [], [], {}
    for m in excluded:
        assignments.append(Assignment(material_id=m, status=AssignmentStatus.insufficient_data))
    for grp in main:
        if len(grp) == 1:
            assignments.append(Assignment(material_id=grp[0], status=AssignmentStatus.singleton))
            continue
        cid = _cluster_id(grp)
        for m in grp:
            cid_of[m] = cid
        gs = set(grp)
        tr = sorted(((t, len(gs & set(g.of_trace[t])), g.traces[t]["df"]) for t in g.of_trace
                     if len(gs & set(g.of_trace[t])) >= 2), key=lambda x: (-x[1], x[2], x[0]))
        inner_pairs = [(a, b) for i, a in enumerate(grp) for b in grp[i + 1:]]
        stab = [sum(1 for l in labs if l[linked.index(a)] == l[linked.index(b)]) / len(labs) for a, b in inner_pairs]
        clusters.append(ClusterEvidence(cid, grp, tuple(tr), sum(stab) / len(stab)))
        assignments += [Assignment(material_id=m, cluster_ids=[cid], status=AssignmentStatus.grouped) for m in grp]
        for a, b in inner_pairs:
            if set(g.of_material[a]) & set(g.of_material[b]):
                pid = pe_ids.get((a, b)) or "gw-" + _digest([a, b, cfg["similarity_version"]])
                run_edges.append(Edge(source=a, target=b, relation=rel, pair_evidence_id=pid, weight=None))
    assignments.sort(key=lambda a: a.material_id)
    mev = tuple(MaterialEvidence(m, cid_of.get(m), ranking[m],
                                 tuple(sorted((t, g.traces[t]["label"]) for t in g.of_material[m]))) for m in linked)

    run_cfg = {k: v for k, v in cfg.items() if k != "dataset_version"}
    run_cfg["seeds"] = list(seeds)
    run = ClusterRun(
        run_id=f"run-{METHOD_ID}-" + _digest([cfg, seed, sorted((k, e.weight) for k, e in g.edges.items()), mats]),
        dataset_version=cfg["dataset_version"], method_id=METHOD_ID, method_version=METHOD_VERSION,
        config=run_cfg, seed=seed, relation=rel, assignments=assignments, edges=run_edges)
    return GraphWalkResult(run=run, config=cfg, graph=g, ranking=ranking, partition=main, clusters=tuple(clusters),
                           materials=mev, excluded=tuple(excluded), seeds=seeds,
                           partitions=tuple(p[0] for p in parts), agreement=agreement)


# ------------------------------------------------------------------ wyjaśnienie pary i odporność

def explain_pair(result: GraphWalkResult, a: str, b: str, *, max_paths: Optional[int] = None) -> dict[str, Any]:
    """Ścieżki a–b, ranking (pozycja b w rankingu a i odwrotnie) i przypisania do grup (osobno od rankingu)."""
    a, b = sorted((a, b))
    g = result.graph
    paths = find_paths(g, a, b, max_paths=max_paths or result.config["max_paths"])
    cid = {m.material_id: m.cluster_id for m in result.materials}
    full = {m: [n for n, _ in rank_neighbours(g, m, result.config["restart"], len(g.materials))] for m in (a, b)}
    return {"pair": (a, b), "paths": paths, "same_cluster": cid.get(a) is not None and cid.get(a) == cid.get(b),
            "cluster_ids": (cid.get(a), cid.get(b)),
            "rank_of": {a: full[a].index(b) + 1 if b in full[a] else None,
                        b: full[b].index(a) + 1 if a in full[b] else None}}


def rerun_without_popular(observations: Sequence[Observation], export: Mapping[str, Any], config: Mapping[str, Any],
                          before: GraphWalkResult, a: str, b: str, *, drop_top: int, seed: int = 0) -> dict[str, Any]:
    """Powtórzenie po usunięciu `drop_top` najpopularniejszych śladów: czy para nadal jest powiązana ścieżką
    i wspólną grupą; zgodność (ARI) podziałów na materiałach obecnych w obu grafach."""
    dropped = popular_traces(export, top=drop_top)
    cfg2 = {**config, "drop_traces": sorted(set(config.get("drop_traces", ())) | set(dropped))}
    after = build_result(observations, export, cfg2, seed=seed)
    common = sorted(set(before.graph.materials) & set(after.graph.materials)
                    - set(before.excluded) - set(after.excluded))
    lb = {m: i for i, grp in enumerate(before.partition) for m in grp}
    la = {m: i for i, grp in enumerate(after.partition) for m in grp}
    ari = _ari([lb[m] for m in common if m in lb and m in la], [la[m] for m in common if m in lb and m in la])
    return {"dropped_traces": dropped, "before": explain_pair(before, a, b),
            "after": explain_pair(after, a, b) if a in after.graph.of_material and b in after.graph.of_material
            and after.graph.of_material[a] and after.graph.of_material[b] else None,
            "partition_ari": ari, "result": after}


# ------------------------------------------------------------------ adaptery

def load_export(obj: Any) -> Mapping[str, Any]:
    if isinstance(obj, (bytes, bytearray, str)):
        return json.loads(obj)
    return obj


class GraphWalkClusterer:
    spec = AdapterSpec(
        id="graph-walk-v1", kind=AdapterKind.clusterer, version=METHOD_VERSION, extras=["graph"],
        description="Ranking sąsiadów spacerem losowym z restartem i społeczności Infomap na grafie materiał–ślad (E14)",
        optional_inputs=[OptionalInput(name="graph_edges", kind="graph",
                                       description="eksport E14 (methadata.rare_motifs.matrix/1), artefakt role=graph_edges")])

    def cluster(self, observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                config: Mapping[str, Any], *, seed: int = 0, optional: Mapping[str, Any] | None = None) -> ClusterRun:
        graph = (optional or {}).get("graph_edges")
        if graph is None:   # bez grafu: brak podstaw do grupowania, nie "brak podobieństwa"
            cfg = resolve_config(config)
            return ClusterRun(run_id=f"run-{METHOD_ID}-" + _digest([cfg, seed, "no-graph"]),
                              dataset_version=cfg["dataset_version"], method_id=METHOD_ID,
                              method_version=METHOD_VERSION, config={**cfg, "graph": None}, seed=seed,
                              relation=Relation(cfg["relation"]),
                              assignments=[Assignment(material_id=m, status=AssignmentStatus.insufficient_data)
                                           for m in sorted({o.canonical_id for o in observations})])
        return build_result(observations, load_export(graph), config, seed=seed, pair_evidence=pair_evidence).run


def get_adapters():
    return [GraphWalkClusterer()]


ADAPTERS = get_adapters()
