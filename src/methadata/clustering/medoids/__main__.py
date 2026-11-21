"""Porównanie K04: k-medoids (macierz odległości) i k-means (znormalizowane wektory) na tych samych rekordach."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from itertools import combinations
from pathlib import Path

from methadata.clustering.density.__main__ import _control

from . import build_centroid_result, build_result, sweep_k


def _groups(run, labels):
    by_cluster = defaultdict(list)
    for a in run.assignments:
        for cid in a.cluster_ids:
            by_cluster[cid].append(labels[a.material_id])
    return sorted(sorted(g) for g in by_cluster.values())


def _pairs(groups):
    return {p for g in groups for p in combinations(g, 2)}


def _jaccard(a, b):
    left, right = _pairs(a), _pairs(b)
    return len(left & right) / len(left | right) if left | right else None


def _wrong_forced(result, labels, truth):
    """Błędne wymuszone przypisanie: materiał w rodzinie, której reprezentant ma inną etykietę prawdy
    (lub 'noise'). Reprezentant 'noise' nie tworzy rodziny."""
    rep = {e.material_id: e.representative_id for e in result.evidence}
    wrong = []
    for a in result.run.assignments:
        name = labels[a.material_id]
        if a.status.value == "grouped" and truth[name] != truth.get(labels[rep[a.material_id]]):
            wrong.append(name)
    return sorted(wrong)


def _describe(result, labels, truth, kind):
    reps = result.medoids if kind == "medoids" else result.representatives
    return {
        "groups": _groups(result.run, labels),
        "representatives": sorted(labels[m] for m in reps),
        "forced_rejected": sorted(labels[e.material_id] for e in result.evidence if e.forced),
        "wrong_forced_assignments": _wrong_forced(result, labels, truth),
        "run": result.run.model_dump(mode="json"),
    }


def compare(config):
    observations, pairs, labels = _control(config)
    by_name = {name: cid for cid, name in labels.items()}
    vectors = {by_name[n]: v for n, v in config["vectors"].items()}
    truth = config["truth"]
    base = {"dataset_version": config["dataset_version"], "similarity_version": "synthetic-sim-v1",
            "vector_version": config["vector_version"], "max_distance": config["max_distance"]}
    report = {"dataset_version": config["dataset_version"], "vector_version": config["vector_version"], "ks": {}}
    for k in config["ks"]:
        med = build_result(observations, pairs, {**base, "k": k})
        raw = build_result(observations, pairs, {**base, "k": k, "max_distance": 1.0})
        raw_cen = build_centroid_result(observations, vectors, {**base, "k": k, "max_distance": 1.0})
        cen = build_centroid_result(observations, vectors, {**base, "k": k})
        m, c = _describe(med, labels, truth, "medoids"), _describe(cen, labels, truth, "centroids")
        m["wrong_forced_without_threshold"] = _wrong_forced(raw, labels, truth)
        c["wrong_forced_without_threshold"] = _wrong_forced(raw_cen, labels, truth)
        report["ks"][str(k)] = {"medoids": m, "centroids": c,
                                "pair_agreement_medoids_vs_centroids": _jaccard(m["groups"], c["groups"])}
    sweep = sweep_k(observations, pairs, base, config["ks"])
    for row in sweep:
        row["medoids"] = sorted(labels[x] for x in row["medoids"])
    report["sweep"] = sweep
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/medoids/comparison.json"))
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    print(json.dumps({"control": compare(config["control"])}, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
