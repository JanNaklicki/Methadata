"""K03: klastrowanie gęstościowe na wersjonowanych porównaniach par."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from math import isfinite
from typing import Any, Mapping, Sequence

from methadata.contracts import (
    AdapterKind, AdapterSpec, Assignment, AssignmentStatus, ClusterRun, Edge,
    Observation, PairEvidence, Relation,
)

METHOD_VERSION = "1"
ALGORITHMS = ("dbscan", "hdbscan", "optics")


@dataclass(frozen=True)
class MaterialEvidence:
    material_id: str
    reason: str
    missing_against: tuple[str, ...] = ()
    core_distance: float | None = None
    core_neighbor: Edge | None = None
    neighbors_within_eps: int | None = None
    membership_strength: float | None = None
    order: int | None = None
    reachability: float | None = None
    predecessor: Edge | None = None


@dataclass(frozen=True)
class DensityResult:
    run: ClusterRun
    evidence: tuple[MaterialEvidence, ...]


def _int_param(config: Mapping[str, Any], name: str, default: int, minimum: int) -> int:
    value = config.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"config.{name} musi być liczbą całkowitą ≥ {minimum}")
    return value


def _float_param(config: Mapping[str, Any], name: str, default: float,
                 *, allow_zero: bool = False, allow_one: bool = True) -> float:
    value = config.get(name, default)
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1
            or (value == 0 and not allow_zero) or (value == 1 and not allow_one)):
        raise ValueError(f"config.{name} poza zakresem {'[' if allow_zero else '('}0, 1{']' if allow_one else ')'}")
    return float(value)


def _resolve_config(config: Mapping[str, Any], algorithm: str) -> dict[str, Any]:
    if algorithm not in ALGORITHMS:
        raise ValueError(f"algorithm musi być jednym z {ALGORITHMS}")
    result = {}
    for key in ("dataset_version", "similarity_version"):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"config.{key} jest wymagane")
        result[key] = value
    result.update(
        relation=Relation(config.get("relation", Relation.shared_template)).value,
        method_id=config.get("method_id"),
        min_samples=_int_param(config, "min_samples", 2, 2 if algorithm == "optics" else 1),
        metric="precomputed",
        distance="1-score",
        matrix_policy="complete-greedy-v1",
    )
    if algorithm == "dbscan":
        result["eps"] = _float_param(config, "eps", 0.25)
    else:
        result["min_cluster_size"] = _int_param(config, "min_cluster_size", 2, 2)
        if algorithm == "hdbscan":
            result["cluster_selection_epsilon"] = _float_param(config, "cluster_selection_epsilon", 0.0,
                                                               allow_zero=True)
            result["cluster_selection_method"] = "eom"
            result["allow_single_cluster"] = False
        else:
            result["max_eps"] = _float_param(config, "max_eps", 1.0)
            result["xi"] = _float_param(config, "xi", 0.05, allow_one=False)
            result["cluster_method"] = "xi"
            result["predecessor_correction"] = True
    return result


def _select_pairs(pairs: Sequence[PairEvidence], materials: set[str],
                  config: dict[str, Any]) -> dict[tuple[str, str], PairEvidence]:
    relation, method = config["relation"], config["method_id"]
    candidates = [p for p in pairs if p.relation.value == relation and (method is None or p.method_id == method)]
    methods = {p.method_id for p in candidates}
    if len(methods) > 1:
        raise ValueError(f"wejście zawiera kilka metod {sorted(methods)}; ustaw config.method_id")
    if method is None and methods:
        config["method_id"] = next(iter(methods))

    selected = {}
    for pair in sorted(candidates, key=lambda p: (p.score is None, -(p.score or 0), p.pair_evidence_id)):
        if not materials.issuperset(pair.pair):
            raise ValueError(f"PairEvidence {pair.pair_evidence_id} wskazuje materiał spoza obserwacji")
        selected.setdefault(pair.pair, pair)
    return selected


def _comparable_materials(materials: list[str], pairs: Mapping[tuple[str, str], PairEvidence]
                          ) -> tuple[list[str], dict[str, MaterialEvidence]]:
    partners = defaultdict(set)
    for a, b in pairs:
        partners[a].add(b)
        partners[b].add(a)

    evidence = {m: MaterialEvidence(m, "no_comparisons") for m in materials if m not in partners}
    # Dict zachowuje kolejność canonical_id. max() zwraca pierwszy remis.
    missing_counts = {m: len(partners) - 1 - len(partners[m]) for m in materials if m in partners}
    while missing_counts:
        worst = max(missing_counts, key=missing_counts.__getitem__)
        if missing_counts[worst] == 0:
            break
        del missing_counts[worst]
        missing = tuple(m for m in missing_counts if m not in partners[worst])
        evidence[worst] = MaterialEvidence(worst, "incomplete_matrix", missing)
        for material in missing:
            missing_counts[material] -= 1
    comparable = list(missing_counts)
    if len(comparable) == 1:
        material = comparable.pop()
        evidence[material] = MaterialEvidence(material, "no_remaining_comparisons")
    return comparable, evidence


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


def _backend():
    try:
        import numpy as np
        import sklearn
        from sklearn.cluster import DBSCAN, HDBSCAN, OPTICS
    except ImportError as exc:
        raise ImportError("K03 wymaga dodatku density. Zainstaluj: pip install '.[density]'") from exc
    return np, sklearn.__version__, {"dbscan": DBSCAN, "hdbscan": HDBSCAN, "optics": OPTICS}


def _fit(estimator, matrix, algorithm: str, config: Mapping[str, Any]):
    common = {"metric": "precomputed", "min_samples": config["min_samples"], "n_jobs": 1}
    if algorithm == "dbscan":
        return estimator(**common, eps=config["eps"], algorithm="brute").fit(matrix)
    if max(config["min_samples"], config["min_cluster_size"]) > len(matrix):
        # Żadna grupa nie spełni ograniczeń. Estymatory odrzucają część takich wejść.
        return None
    if algorithm == "hdbscan":
        return estimator(
            **common, min_cluster_size=config["min_cluster_size"], algorithm="brute", copy=True,
            cluster_selection_method=config["cluster_selection_method"],
            cluster_selection_epsilon=config["cluster_selection_epsilon"],
            allow_single_cluster=config["allow_single_cluster"],
        ).fit(matrix)
    return estimator(
        **common, min_cluster_size=config["min_cluster_size"], algorithm="brute", max_eps=config["max_eps"],
        xi=config["xi"], cluster_method=config["cluster_method"],
        predecessor_correction=config["predecessor_correction"],
    ).fit(matrix)


def _mutual_reachability_tree(indices: list[int], matrix, core_distances: list[float | None]):
    """Drzewo wsparcia wewnątrz już wybranej grupy. Remisy rozstrzygają indeksy macierzy."""
    first, *others = indices

    def weight(a: int, b: int) -> float:
        return max(core_distances[a], core_distances[b], matrix[a, b])

    nearest = {b: (weight(first, b), first) for b in others}
    while nearest:
        next_index = min(nearest, key=lambda i: (*nearest[i], i))
        _, source = nearest.pop(next_index)
        yield source, next_index
        for index, previous in nearest.items():
            nearest[index] = min(previous, (weight(next_index, index), next_index))


def build_result(observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                 config: Mapping[str, Any], *, algorithm: str = "dbscan", seed: int = 0) -> DensityResult:
    cfg = _resolve_config(config, algorithm)
    materials = sorted({o.canonical_id for o in observations})
    chosen = _select_pairs(pair_evidence, set(materials), cfg)
    scored = {key: p for key, p in chosen.items() if p.score is not None}
    comparable, evidence = _comparable_materials(materials, scored)
    np, version, estimators = _backend()
    cfg["sklearn_version"] = version
    matrix = np.zeros((len(comparable), len(comparable)), dtype=np.float64)

    def raw_pair(i: int, j: int) -> PairEvidence:
        return scored[tuple(sorted((comparable[i], comparable[j])))]

    def edge(i: int, j: int) -> Edge:
        pair = raw_pair(i, j)
        return Edge(source=pair.pair[0], target=pair.pair[1], relation=pair.relation,
                    pair_evidence_id=pair.pair_evidence_id, weight=pair.score)

    for i, j in combinations(range(len(comparable)), 2):
        matrix[i, j] = matrix[j, i] = 1.0 - raw_pair(i, j).score

    model = None
    if comparable and algorithm == "optics":
        # Xi wykorzystuje ilorazy z zerową osiągalnością dla identycznych materiałów.
        with np.errstate(divide="ignore", invalid="ignore"):
            model = _fit(estimators[algorithm], matrix, algorithm, cfg)
    elif comparable:
        model = _fit(estimators[algorithm], matrix, algorithm, cfg)
    labels = model.labels_ if model is not None else [-1] * len(comparable)
    groups = defaultdict(list)
    for i, label in enumerate(labels):
        if label >= 0:
            groups[label].append(i)

    core_count = cfg["min_samples"] - 1
    core_neighbors, core_distances = [], []
    for i in range(len(comparable)):
        # Własny punkt zawsze zajmuje pierwszą pozycję, także przy score=1 dla innych.
        order_by_distance = sorted((matrix[i, j], j) for j in range(len(comparable)) if j != i)
        distance, neighbor = (0.0, None) if core_count == 0 else (
            order_by_distance[core_count - 1] if core_count <= len(order_by_distance) else (None, None)
        )
        core_distances.append(float(distance) if distance is not None else None)
        core_neighbors.append(edge(i, neighbor) if neighbor is not None else None)

    dbscan_core = set(model.core_sample_indices_) if model is not None and algorithm == "dbscan" else set()
    optics_order = {point: rank for rank, point in enumerate(model.ordering_)} if (
        model is not None and algorithm == "optics"
    ) else {}
    status = {m: Assignment(material_id=m, status=AssignmentStatus.insufficient_data) for m in evidence}
    run_edges = {}
    for indices in groups.values():
        members = [comparable[i] for i in indices]
        cluster_id = "cl-" + _digest(members)
        for material in members:
            status[material] = Assignment(
                material_id=material, cluster_ids=[cluster_id] if len(members) > 1 else [],
                status=AssignmentStatus.grouped if len(members) > 1 else AssignmentStatus.singleton,
            )
        if algorithm == "dbscan":
            links = ((i, j) for i, j in combinations(indices, 2)
                     if matrix[i, j] <= cfg["eps"] and (i in dbscan_core or j in dbscan_core))
        elif algorithm == "hdbscan":
            links = _mutual_reachability_tree(indices, matrix, core_distances)
        else:
            links = ((i, j) for i in indices if (j := model.predecessor_[i]) >= 0 and labels[j] == labels[i])
        for i, j in links:
            link = edge(i, j)
            run_edges[link.source, link.target] = link

    for i, (material, label) in enumerate(zip(comparable, labels)):
        if label == -1:
            status[material] = Assignment(material_id=material, status=AssignmentStatus.noise)
        reason = "density_noise" if label == -1 else "density_cluster"
        values = {}
        if algorithm == "dbscan":
            if label >= 0:
                reason = "core" if i in dbscan_core else "border"
            values["neighbors_within_eps"] = int(np.count_nonzero(matrix[i] <= cfg["eps"]))
        elif model is None:
            reason = "too_few_samples"
        elif algorithm == "hdbscan":
            values["membership_strength"] = float(model.probabilities_[i])
        else:
            previous = model.predecessor_[i]
            values.update(
                order=optics_order[i], reachability=float(model.reachability_[i]) if isfinite(model.reachability_[i])
                else None, predecessor=edge(i, previous) if previous >= 0 else None,
            )
        distance, neighbor = core_distances[i], core_neighbors[i]
        if algorithm == "optics" and distance is not None and distance > cfg["max_eps"]:
            distance, neighbor = None, None
        evidence[material] = MaterialEvidence(
            material, reason, core_distance=distance, core_neighbor=neighbor, **values,
        )

    method_id = f"density-{algorithm}"
    run = ClusterRun(
        run_id=f"run-{method_id}-" + _digest(
            [cfg, seed, materials, [(key, p.pair_evidence_id, p.score) for key, p in sorted(chosen.items())]]
        ),
        dataset_version=cfg["dataset_version"], method_id=method_id, method_version=METHOD_VERSION,
        config={key: value for key, value in cfg.items() if key != "dataset_version"},
        seed=seed, relation=Relation(cfg["relation"]),
        assignments=[status[m] for m in materials], edges=[run_edges[key] for key in sorted(run_edges)],
    )
    return DensityResult(run, tuple(evidence[m] for m in materials))


class DensityClusterer:
    def __init__(self, algorithm: str):
        if algorithm not in ALGORITHMS:
            raise ValueError(f"algorithm musi być jednym z {ALGORITHMS}")
        self.algorithm = algorithm
        self.spec = AdapterSpec(
            id=f"density-{algorithm}-v1", kind=AdapterKind.clusterer, version=METHOD_VERSION,
            description=f"{algorithm.upper()} na pełnej macierzy 1 - podobieństwo",
        )

    def cluster(self, observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                config: Mapping[str, Any], *, seed: int = 0, optional: Mapping[str, Any] | None = None) -> ClusterRun:
        return build_result(observations, pair_evidence, config, algorithm=self.algorithm, seed=seed).run


def get_adapters() -> list[DensityClusterer]:
    return [DensityClusterer(algorithm) for algorithm in ALGORITHMS]
