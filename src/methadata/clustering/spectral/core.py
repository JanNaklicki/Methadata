"""K06: klastrowanie spektralne na symetrycznym grafie wzajemnych najbliższych sąsiadów (mutual kNN).

Graf (jawny w config i wyniku):
  * punkt wyjścia = ten sam graf ważony co w K05 (leiden.build_graph: wybór relacji/metody, najlepszy score pary,
    próg min_edge_weight, obniżanie wagi popularnych śladów). Waga "adjusted" jest wagą afiniczną;
  * każdy wierzchołek zachowuje `neighbors` (k) najsilniejszych krawędzi (remis: id sąsiada); krawędź przeżywa, gdy
    jest w obu listach ("mutual", domyślnie) albo w co najmniej jednej ("union"). Wynik jest symetryczny;
  * wierzchołek bez przeżywającej krawędzi jest izolowany (status singleton); materiał bez żadnego porównania
    z score != None to insufficient_data (brak pary != podobieństwo 0).
Spektrum: osobno dla każdej spójnej składowej - znormalizowany Laplasjan L = I - D^-1/2 W D^-1/2, wektory własne
dla k najmniejszych wartości, normalizacja wierszy, k-means (seed). Liczba grup: `n_clusters` = całkowita liczba
grup (składowe rozłączne są zawsze osobnymi grupami; nadwyżka rozdzielana jest między składowe wg szczeliny
własnej) albo "auto" = największa szczelina własna w każdej składowej (poniżej `min_eigengap` -> 1 grupa).
Ten sam graf można porównać z Leiden (compare_with_leiden): Leiden dostaje dokładnie te same krawędzie i wagi.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from methadata.clustering.leiden import core as _leiden
from methadata.clustering.leiden.core import ClusterEvidence, EdgeEvidence, MaterialEvidence
from methadata.contracts import (AdapterKind, AdapterSpec, Assignment, AssignmentStatus, ClusterRun, Edge,
                                 Observation, PairEvidence, Relation)

METHOD_ID = "spectral"
METHOD_VERSION = "1"
NEIGHBOR_MODES = ("mutual", "union")
MAX_DENSE = 4000   # największa składowa (gęsta macierz); powyżej - jawny błąd


@dataclass(frozen=True)
class ComponentEvidence:
    index: int
    members: tuple[str, ...]
    eigenvalues: tuple[float, ...]   # najmniejsze wartości własne L (do max_clusters+1)
    eigengaps: tuple[float, ...]     # różnice kolejnych wartości własnych
    n_clusters: int                  # przyznana liczba grup w składowej
    chosen_by: str                   # "auto" | "allocated" | "trivial"


@dataclass(frozen=True)
class SpectralResult:
    run: ClusterRun
    config: dict[str, Any]
    graph_edges: tuple[EdgeEvidence, ...]      # krawędzie mutual-kNN (to samo wejście dla Leiden)
    dropped_edges: tuple[EdgeEvidence, ...]    # krawędzie bazowego grafu odrzucone przez regułę kNN
    components: tuple[ComponentEvidence, ...]
    clusters: tuple[ClusterEvidence, ...]
    materials: tuple[MaterialEvidence, ...]
    excluded: tuple[str, ...]                  # insufficient_data
    isolated: tuple[str, ...]                  # bez krawędzi po regule kNN
    notes: tuple[str, ...]                     # np. n_clusters przycięte
    seeds: tuple[int, ...]
    partitions: tuple[tuple[tuple[str, ...], ...], ...]
    agreement: Mapping[str, float]
    co_membership: Mapping[tuple[str, str], float]


# ------------------------------------------------------------------ konfiguracja

def resolve_config(config: Mapping[str, Any]) -> dict[str, Any]:
    base = _leiden.resolve_config({**config, "quality": "cpm"})
    k = config.get("neighbors", 5)
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError("config.neighbors musi być liczbą całkowitą >= 1")
    mode = config.get("neighbor_mode", "mutual")
    if mode not in NEIGHBOR_MODES:
        raise ValueError(f"config.neighbor_mode musi być jednym z {NEIGHBOR_MODES}")
    nc = config.get("n_clusters", "auto")
    if nc != "auto" and (isinstance(nc, bool) or not isinstance(nc, int) or nc < 1):
        raise ValueError("config.n_clusters musi być liczbą całkowitą >= 1 albo 'auto'")
    maxk = config.get("max_clusters", 10)
    if isinstance(maxk, bool) or not isinstance(maxk, int) or maxk < 2:
        raise ValueError("config.max_clusters musi być liczbą całkowitą >= 2")
    gap = config.get("min_eigengap", 0.1)
    if isinstance(gap, bool) or not isinstance(gap, (int, float)) or gap < 0:
        raise ValueError("config.min_eigengap musi być liczbą >= 0")
    base = {k_: v for k_, v in base.items() if k_ not in ("quality", "resolution")}
    base.update(neighbors=k, neighbor_mode=mode, n_clusters=nc, max_clusters=maxk, min_eigengap=float(gap))
    return base


# ------------------------------------------------------------------ graf

def _knn_filter(edges: Sequence[EdgeEvidence], nodes: Sequence[str], k: int, mode: str):
    adj: dict[str, list[tuple[float, str, EdgeEvidence]]] = {m: [] for m in nodes}
    for e in edges:
        adj[e.source].append((e.adjusted, e.target, e))
        adj[e.target].append((e.adjusted, e.source, e))
    keep: dict[str, set[tuple[str, str]]] = {}
    for m, lst in adj.items():
        lst.sort(key=lambda t: (-t[0], t[1]))
        keep[m] = {(e.source, e.target) for _, _, e in lst[:k]}
    kept, dropped = [], []
    for e in edges:
        key = (e.source, e.target)
        a, b = key in keep[e.source], key in keep[e.target]
        (kept if (a and b if mode == "mutual" else a or b) else dropped).append(e)
    return kept, dropped


def build_graph(observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                config: Mapping[str, Any]):
    """-> (cfg, wierzchołki, wykluczone, krawędzie kNN, odrzucone, izolowane)."""
    cfg = resolve_config(config)
    _, nodes, excluded, edges = _leiden.build_graph(observations, pair_evidence, config)
    kept, dropped = _knn_filter(edges, nodes, cfg["neighbors"], cfg["neighbor_mode"])
    touched = {m for e in kept for m in (e.source, e.target)}
    isolated = [m for m in nodes if m not in touched]
    return cfg, nodes, excluded, kept, dropped, isolated


def _components(nodes: Sequence[str], edges: Sequence[EdgeEvidence]) -> list[list[str]]:
    parent = {m: m for m in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for e in edges:
        ra, rb = find(e.source), find(e.target)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    comp: dict[str, list[str]] = {}
    for m in nodes:
        comp.setdefault(find(m), []).append(m)
    return sorted(sorted(v) for v in comp.values())


# ------------------------------------------------------------------ spektrum

def _np():
    try:
        import numpy
        from sklearn.cluster import KMeans
    except ImportError as exc:
        raise RuntimeError("Klastrowanie spektralne wymaga extra `density`: pip install 'methadata[density]' "
                           "(numpy, scikit-learn)") from exc
    return numpy, KMeans


def _spectrum(members: Sequence[str], edges: Sequence[EdgeEvidence], max_k: int):
    np, _ = _np()
    n = len(members)
    if n > MAX_DENSE:
        raise ValueError(f"składowa ma {n} wierzchołków (> {MAX_DENSE}); zmniejsz neighbors lub podnieś min_edge_weight")
    idx = {m: i for i, m in enumerate(members)}
    w = np.zeros((n, n))
    for e in edges:
        if e.source in idx:
            w[idx[e.source], idx[e.target]] = w[idx[e.target], idx[e.source]] = e.adjusted
    d = w.sum(axis=1)
    inv = 1.0 / np.sqrt(d)
    lap = np.eye(n) - inv[:, None] * w * inv[None, :]
    vals, vecs = np.linalg.eigh((lap + lap.T) / 2)
    vals = np.clip(vals, 0.0, 2.0)
    return vals, vecs


def _allocate(spectra, cfg, n_comp_nontrivial_extra):
    """Liczba grup w każdej składowej. spectra: lista (n, vals) dla składowych."""
    maxk = cfg["max_clusters"]
    ks, how, notes = [], [], []
    if cfg["n_clusters"] == "auto":
        for n, vals in spectra:
            if n < 3:
                ks.append(1)
                how.append("trivial")
                continue
            top = min(maxk, n - 1)
            gaps = [vals[i + 1] - vals[i] for i in range(top)]
            best = max(range(top), key=lambda i: (gaps[i], -i))
            ks.append(best + 1 if gaps[best] >= cfg["min_eigengap"] else 1)
            how.append("auto")
        return ks, how, notes
    total = cfg["n_clusters"]
    ks = [1] * len(spectra)
    how = ["trivial" if n < 3 else "allocated" for n, _ in spectra]
    if total < len(spectra):
        notes.append(f"n_clusters={total} < liczby składowych rozłącznych ({len(spectra)}); składowe pozostają osobnymi grupami")
    extra = total - len(spectra)
    while extra > 0:
        cand = []
        for i, (n, vals) in enumerate(spectra):
            k = ks[i]
            if n >= 3 and k < n - 1 and k + 1 <= max(maxk, total):
                cand.append((vals[k + 1] - vals[k] if k + 1 < n else 0.0, -i, i))
        if not cand:
            notes.append(f"n_clusters={total} przycięte do {sum(ks)} (zbyt mało wierzchołków w składowych)")
            break
        ks[max(cand)[2]] += 1
        extra -= 1
    return ks, how, notes


def _kmeans_labels(vecs, k: int, seed: int):
    np, KMeans = _np()
    if k <= 1:
        return [0] * vecs.shape[0]
    u = vecs[:, :k]
    norm = np.linalg.norm(u, axis=1, keepdims=True)
    norm[norm == 0] = 1.0
    km = KMeans(n_clusters=k, n_init=10, random_state=seed)
    return [int(x) for x in km.fit_predict(u / norm)]


# ------------------------------------------------------------------ wynik

def build_result(observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                 config: Mapping[str, Any], *, seed: int = 0) -> SpectralResult:
    cfg, nodes, excluded, edges, dropped, isolated = build_graph(observations, pair_evidence, config)
    rel = Relation(cfg["relation"])
    seeds = tuple(range(seed, seed + cfg["repeats"]))
    iso = set(isolated)
    active = [m for m in nodes if m not in iso]
    comps = _components(active, edges)
    spectra, vecs_of = [], []
    for c in comps:
        if len(c) < 3:
            spectra.append((len(c), [0.0] * len(c)))
            vecs_of.append(None)
        else:
            vals, vecs = _spectrum(c, edges, cfg["max_clusters"])
            spectra.append((len(c), list(vals)))
            vecs_of.append(vecs)
    ks, how, notes = _allocate(spectra, cfg, 0)
    comp_ev = tuple(ComponentEvidence(
        index=i, members=tuple(c), eigenvalues=tuple(round(float(v), 6) for v in spectra[i][1][:cfg["max_clusters"] + 1]),
        eigengaps=tuple(round(float(spectra[i][1][j + 1] - spectra[i][1][j]), 6)
                        for j in range(min(cfg["max_clusters"], len(c) - 1))),
        n_clusters=ks[i], chosen_by=how[i]) for i, c in enumerate(comps))

    def partition(s: int):
        groups: list[tuple[str, ...]] = [(m,) for m in isolated]
        for i, c in enumerate(comps):
            lab = _kmeans_labels(vecs_of[i], ks[i], s) if vecs_of[i] is not None and ks[i] > 1 else [0] * len(c)
            g: dict[int, list[str]] = {}
            for m, l in zip(c, lab):
                g.setdefault(l, []).append(m)
            groups += [tuple(sorted(v)) for v in g.values()]
        return tuple(sorted(groups))

    parts = [partition(s) for s in seeds]
    main = parts[0]
    labs = [_leiden._labels(p, nodes) for p in parts]
    aris = [_leiden._ari(labs[0], l) for l in labs[1:]] + [_leiden._ari(labs[i], labs[j]) for i in range(1, len(labs))
                                                            for j in range(i + 1, len(labs))]
    agreement = {"mean_ari": sum(aris) / len(aris) if aris else 1.0, "min_ari": min(aris) if aris else 1.0,
                 "identical_fraction": sum(1 for p in parts if p == main) / len(parts), "repeats": float(len(parts))}
    pos = {m: i for i, m in enumerate(nodes)}
    together = {(e.source, e.target): sum(1 for l in labs if l[pos[e.source]] == l[pos[e.target]]) / len(labs)
                for e in edges}

    cid_of: dict[str, str] = {}
    clusters, run_edges, assignments = [], [], []
    for m in excluded:
        assignments.append(Assignment(material_id=m, status=AssignmentStatus.insufficient_data))
    for g in main:
        if len(g) == 1:
            assignments.append(Assignment(material_id=g[0], status=AssignmentStatus.singleton))
            continue
        cid = _leiden._cluster_id(g)
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
    run_cfg["components"] = [{"size": len(c.members), "n_clusters": c.n_clusters, "chosen_by": c.chosen_by}
                             for c in comp_ev]
    run = ClusterRun(
        run_id=f"run-{METHOD_ID}-" + _leiden._digest([cfg, seed, [(e.source, e.target, e.pair_evidence_id, e.score)
                                                                 for e in edges], nodes, excluded]),
        dataset_version=cfg["dataset_version"], method_id=METHOD_ID, method_version=METHOD_VERSION,
        config=run_cfg, seed=seed, relation=rel, assignments=assignments, edges=run_edges)
    return SpectralResult(run=run, config=cfg, graph_edges=tuple(edges), dropped_edges=tuple(dropped),
                          components=comp_ev, clusters=tuple(clusters), materials=tuple(mats),
                          excluded=tuple(excluded), isolated=tuple(isolated), notes=tuple(notes), seeds=seeds,
                          partitions=tuple(parts), agreement=agreement, co_membership=together)


# ------------------------------------------------------------------ porównanie, sweep, wyjaśnienie

def compare_with_leiden(result: SpectralResult, *, resolution: float = 0.5, quality: str = "cpm",
                        seed: int = 0) -> dict[str, Any]:
    """Leiden na dokładnie tych samych wierzchołkach i krawędziach (waga adjusted) co klastrowanie spektralne."""
    nodes = sorted(m for g in result.partitions[0] for m in g)
    cfg = {"quality": quality, "resolution": float(resolution)}
    lpart, qual = _leiden._partition(nodes, result.graph_edges, cfg, seed) if result.graph_edges else (
        tuple((m,) for m in nodes), 0.0)
    spec = result.partitions[0]
    multi = lambda p: sorted(g for g in p if len(g) > 1)  # noqa: E731
    return {"edges": len(result.graph_edges), "nodes": len(nodes),
            "spectral_groups": multi(spec), "leiden_groups": multi(lpart),
            "ari": _leiden.partition_agreement(spec, lpart) if nodes else 1.0,
            "identical": multi(spec) == multi(lpart), "leiden_quality": qual,
            "leiden_config": cfg}


def sweep(observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence], config: Mapping[str, Any],
          neighbors: Sequence[int], n_clusters: Sequence[Any] = ("auto",),
          seeds: Sequence[int] = (0,)) -> list[dict[str, Any]]:
    """Wpływ liczby sąsiadów k i liczby grup: grupy, izolowane węzły, największa grupa, zgodność między seedami."""
    rows = []
    for k in neighbors:
        for nc in n_clusters:
            r = build_result(observations, pair_evidence,
                             {**config, "neighbors": k, "n_clusters": nc, "repeats": len(seeds)}, seed=min(seeds))
            sizes = sorted((len(g) for g in r.partitions[0]), reverse=True)
            rows.append({"neighbors": k, "n_clusters": nc, "edges": len(r.graph_edges),
                         "dropped_edges": len(r.dropped_edges), "components": len(r.components),
                         "isolated": len(r.isolated), "n_groups": sum(1 for s in sizes if s > 1),
                         "largest": sizes[0] if sizes else 0, "mean_ari": r.agreement["mean_ari"],
                         "partition": r.partitions[0]})
    return rows


def explain_pair(result: SpectralResult, a: str, b: str) -> dict[str, Any]:
    a, b = sorted((a, b))
    mats = {m.material_id: m for m in result.materials}
    for m in (a, b):
        if m not in mats:
            raise ValueError(f"materiał {m} nie jest wierzchołkiem grafu")
    direct = next((e for e in result.graph_edges if (e.source, e.target) == (a, b)), None)
    dropped = next((e for e in result.dropped_edges if (e.source, e.target) == (a, b)), None)
    freq = 0.0
    for p in result.partitions:
        lab = {m: i for i, g in enumerate(p) for m in g}
        freq += lab[a] == lab[b]
    freq /= len(result.partitions)
    return {"pair": (a, b), "direct_edge": direct, "dropped_by_knn": dropped,
            "same_cluster": mats[a].cluster_id is not None and mats[a].cluster_id == mats[b].cluster_id,
            "co_membership_frequency": freq,
            "assignment": {m: {"cluster_id": mats[m].cluster_id, "to_own": mats[m].to_own,
                               "alternatives": mats[m].to_other, "strongest_edges": mats[m].strongest_edges}
                           for m in (a, b)}}


# ------------------------------------------------------------------ adaptery

class SpectralClusterer:
    spec = AdapterSpec(id="spectral-v1", kind=AdapterKind.clusterer, version=METHOD_VERSION,
                       description="Klastrowanie spektralne na symetrycznym grafie wzajemnych najbliższych sąsiadów")

    def cluster(self, observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                config: Mapping[str, Any], *, seed: int = 0, optional: Mapping[str, Any] | None = None) -> ClusterRun:
        return build_result(observations, pair_evidence, config, seed=seed).run


def get_adapters():
    return [SpectralClusterer()]


ADAPTERS = get_adapters()
