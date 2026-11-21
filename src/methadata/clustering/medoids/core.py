"""K04: k-medoids na macierzy odległości (1 - score) oraz k-means na znormalizowanych wektorach."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Mapping, Sequence

from methadata.clustering.density.core import _comparable_materials, _select_pairs
from methadata.contracts import (
    AdapterKind, AdapterSpec, Assignment, AssignmentStatus, ClusterRun, Edge, Observation, PairEvidence, Relation,
)

METHOD_VERSION = "1"


@dataclass(frozen=True)
class MaterialEvidence:
    material_id: str
    reason: str  # medoid | member | forced_rejected | singleton | no_comparisons | incomplete_matrix | ...
    representative_id: str | None = None  # faktyczny materiał reprezentujący rodzinę
    distance_to_representative: float | None = None
    link: Edge | None = None  # dowód: PairEvidence łączące z medoidem
    forced: bool = False  # k-medoids przypisałby go wbrew max_distance
    missing_against: tuple[str, ...] = ()


@dataclass(frozen=True)
class MedoidResult:
    run: ClusterRun
    evidence: tuple[MaterialEvidence, ...]
    medoids: tuple[str, ...]
    total_cost: float


@dataclass(frozen=True)
class CentroidResult:
    run: ClusterRun
    evidence: tuple[MaterialEvidence, ...]
    representatives: tuple[str, ...]  # materiał najbliższy każdemu centroidowi
    centroids: tuple[tuple[float, ...], ...]
    inertia: float


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


def _params(config: Mapping[str, Any], version_key: str) -> dict[str, Any]:
    out = {}
    for key in ("dataset_version", version_key):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"config.{key} jest wymagane")
        out[key] = value
    k = config.get("k", 2)
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError("config.k musi być liczbą całkowitą ≥ 1")
    max_distance = config.get("max_distance", 0.5)
    if isinstance(max_distance, bool) or not isinstance(max_distance, (int, float)) or not 0 <= max_distance <= 1:
        raise ValueError("config.max_distance poza zakresem [0, 1]")
    out.update(k=k, max_distance=float(max_distance),
               relation=Relation(config.get("relation", Relation.shared_template)).value,
               method_id=config.get("method_id"))
    return out


def _numpy():
    try:
        import numpy as np
    except ImportError as exc:
        raise ImportError("K04 wymaga dodatku density. Zainstaluj: pip install '.[density]'") from exc
    return np


def _pam(matrix, k: int) -> tuple[list[int], list[int], float]:
    """PAM: zachłanny BUILD i SWAP; remisy rozstrzygają indeksy, więc wynik jest deterministyczny."""
    n = len(matrix)
    k = min(k, n)
    medoids = [int(matrix.sum(axis=1).argmin())]
    while len(medoids) < k:
        nearest = matrix[:, medoids].min(axis=1)
        gains = [(float((nearest - matrix[:, c]).clip(min=0).sum()), -c) if c not in medoids else (-1.0, 0)
                 for c in range(n)]
        medoids.append(-max(gains)[1])

    def cost(ms):
        return float(matrix[:, ms].min(axis=1).sum())

    best = cost(medoids)
    improved = True
    while improved:
        improved = False
        for position in range(k):
            for candidate in range(n):
                if candidate in medoids:
                    continue
                trial = medoids[:position] + [candidate] + medoids[position + 1:]
                if (c := cost(trial)) < best - 1e-12:
                    medoids, best, improved = trial, c, True
    labels = [int(matrix[i, medoids].argmin()) for i in range(n)]
    return medoids, labels, best


def _build_run(method_id, cfg, seed, materials, status, edges, chosen_digest, extra_config):
    return ClusterRun(
        run_id=f"run-{method_id}-" + _digest([cfg, extra_config, seed, materials, chosen_digest]),
        dataset_version=cfg["dataset_version"], method_id=method_id, method_version=METHOD_VERSION,
        config={**{k: v for k, v in cfg.items() if k != "dataset_version"}, **extra_config},
        seed=seed, relation=Relation(cfg["relation"]),
        assignments=[status[m] for m in materials], edges=edges,
    )


def build_result(observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                 config: Mapping[str, Any], *, seed: int = 0) -> MedoidResult:
    cfg = _params(config, "similarity_version")
    cfg.update(metric="precomputed", distance="1-score", matrix_policy="complete-greedy-v1")
    materials = sorted({o.canonical_id for o in observations})
    chosen = _select_pairs(pair_evidence, set(materials), cfg)
    scored = {key: p for key, p in chosen.items() if p.score is not None}
    comparable, base_evidence = _comparable_materials(materials, scored)
    evidence = {m: MaterialEvidence(e.material_id, e.reason, missing_against=e.missing_against)
                for m, e in base_evidence.items()}
    status = {m: Assignment(material_id=m, status=AssignmentStatus.insufficient_data) for m in evidence}
    np = _numpy()
    n = len(comparable)
    matrix = np.zeros((n, n))

    def edge(i, j):
        p = scored[tuple(sorted((comparable[i], comparable[j])))]
        return Edge(source=p.pair[0], target=p.pair[1], relation=p.relation,
                    pair_evidence_id=p.pair_evidence_id, weight=p.score)

    for i, j in combinations(range(n), 2):
        matrix[i, j] = matrix[j, i] = 1.0 - scored[tuple(sorted((comparable[i], comparable[j])))].score
    medoids, labels, total = _pam(matrix, cfg["k"]) if n else ([], [], 0.0)

    run_edges, members = {}, defaultdict(list)
    for i, label in enumerate(labels):
        members[label].append(i)
    for label, indices in members.items():
        medoid = medoids[label]
        accepted = [i for i in indices if i == medoid or matrix[i, medoid] <= cfg["max_distance"]]
        cluster_id = "cl-" + _digest(sorted(comparable[i] for i in accepted))
        for i in indices:
            m = comparable[i]
            d = float(matrix[i, medoid])
            link = edge(i, medoid) if i != medoid else None
            if i not in accepted:
                status[m] = Assignment(material_id=m, status=AssignmentStatus.noise)
                evidence[m] = MaterialEvidence(m, "forced_rejected", comparable[medoid], d, link, forced=True)
                continue
            if len(accepted) == 1:
                status[m] = Assignment(material_id=m, status=AssignmentStatus.singleton)
                evidence[m] = MaterialEvidence(m, "singleton", m, 0.0)
                continue
            status[m] = Assignment(material_id=m, cluster_ids=[cluster_id], status=AssignmentStatus.grouped)
            evidence[m] = MaterialEvidence(m, "medoid" if i == medoid else "member", comparable[medoid], d, link)
            if link:
                run_edges[link.source, link.target] = link
    cfg["k_effective"] = min(cfg["k"], n)
    run = _build_run("medoids-kmedoids", cfg, seed, materials, status,
                     [run_edges[key] for key in sorted(run_edges)],
                     [(key, p.pair_evidence_id, p.score) for key, p in sorted(chosen.items())],
                     {"algorithm": "pam"})
    return MedoidResult(run, tuple(evidence[m] for m in materials), tuple(comparable[i] for i in medoids), total)


def sweep_k(observations, pair_evidence, config, ks: Sequence[int], *, seed: int = 0) -> list[dict[str, Any]]:
    """Dla każdego k: koszt, medoidy i liczba wymuszonych przypisań odrzuconych przez max_distance."""
    rows = []
    for k in ks:
        r = build_result(observations, pair_evidence, {**config, "k": k}, seed=seed)
        rows.append({"k": k, "cost": r.total_cost, "medoids": list(r.medoids),
                     "forced_assignments": sum(e.forced for e in r.evidence),
                     "grouped": sum(a.status is AssignmentStatus.grouped for a in r.run.assignments)})
    return rows


def build_centroid_result(observations: Sequence[Observation], vectors: Mapping[str, Sequence[float]],
                          config: Mapping[str, Any], *, seed: int = 0) -> CentroidResult:
    """k-means na wektorach znormalizowanych L2; odległość = 1 - cosinus (= ||a-b||²/2)."""
    cfg = _params(config, "vector_version")
    try:
        from sklearn.cluster import KMeans
    except ImportError as exc:
        raise ImportError("K04 wymaga dodatku density. Zainstaluj: pip install '.[density]'") from exc
    np = _numpy()
    materials = sorted({o.canonical_id for o in observations})
    unknown = set(vectors) - set(materials)
    if unknown:
        raise ValueError(f"wektory dla materiałów spoza obserwacji: {sorted(unknown)}")
    comparable = [m for m in materials if m in vectors]
    status = {m: Assignment(material_id=m, status=AssignmentStatus.insufficient_data)
              for m in materials if m not in vectors}
    evidence = {m: MaterialEvidence(m, "no_vector") for m in status}
    centroids, reps, inertia = [], [], 0.0
    if comparable:
        x = np.array([vectors[m] for m in comparable], dtype=float)
        norms = np.linalg.norm(x, axis=1, keepdims=True)
        if not np.all(norms > 0):
            raise ValueError("wektor zerowy nie podlega normalizacji")
        x = x / norms
        k = min(cfg["k"], len(comparable))
        km = KMeans(n_clusters=k, n_init=10, random_state=seed).fit(x)
        inertia = float(km.inertia_)
        centers = km.cluster_centers_
        centroids = [tuple(float(v) for v in c) for c in centers]
        groups = defaultdict(list)
        for i, label in enumerate(km.labels_):
            groups[int(label)].append(i)
        for label in sorted(groups):
            idx = groups[label]
            c = centers[label] / (np.linalg.norm(centers[label]) or 1.0)
            dist = {i: float(1.0 - x[i] @ c) for i in idx}
            rep = min(idx, key=lambda i: (dist[i], i))
            reps.append(comparable[rep])
            accepted = [i for i in idx if dist[i] <= cfg["max_distance"]]
            cid = "cl-" + _digest(sorted(comparable[i] for i in accepted))
            for i in idx:
                m = comparable[i]
                if i not in accepted:
                    status[m] = Assignment(material_id=m, status=AssignmentStatus.noise)
                    evidence[m] = MaterialEvidence(m, "forced_rejected", comparable[rep], dist[i], forced=True)
                elif len(accepted) == 1:
                    status[m] = Assignment(material_id=m, status=AssignmentStatus.singleton)
                    evidence[m] = MaterialEvidence(m, "singleton", comparable[rep], dist[i])
                else:
                    status[m] = Assignment(material_id=m, cluster_ids=[cid], status=AssignmentStatus.grouped)
                    evidence[m] = MaterialEvidence(m, "member", comparable[rep], dist[i])
    cfg.update(metric="cosine", normalization="l2", n_init=10)
    run = _build_run("centroids-kmeans", cfg, seed, materials, status, [],
                     [(m, _digest(list(vectors[m]))) for m in comparable], {"algorithm": "kmeans"})
    return CentroidResult(run, tuple(evidence[m] for m in materials), tuple(reps), tuple(centroids), inertia)


class MedoidClusterer:
    spec = AdapterSpec(id="medoids-kmedoids-v1", kind=AdapterKind.clusterer, version=METHOD_VERSION,
                       description="k-medoids (PAM) na pełnej macierzy 1 - podobieństwo")

    def cluster(self, observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                config: Mapping[str, Any], *, seed: int = 0, optional: Mapping[str, Any] | None = None) -> ClusterRun:
        return build_result(observations, pair_evidence, config, seed=seed).run


def get_adapters() -> list[MedoidClusterer]:
    return [MedoidClusterer()]
