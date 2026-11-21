"""Porównanie K03 na kontroli syntetycznej i na zamrożonym wejściu F04."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict
from itertools import combinations
from pathlib import Path
from time import perf_counter

from methadata.baseline.core import load_baseline_config, run_baseline
from methadata.contracts import (
    Artifact, ArtifactRef, ArtifactRole, AssignmentStatus, InputKind, Observation, PairEvidence, Relation, ids,
)

from . import ALGORITHMS, build_result
from .core import _backend


def _control(config):
    observations, labels = [], {}
    for name in sorted(config["materials"]):
        url = f"https://{name.lower()}.example.invalid/"
        source = f"control:{name}"
        oid = ids.observation_id(source, "url_only")
        digest = ids.sha256_bytes(url.encode())
        artifact = Artifact(
            artifact_id=ids.artifact_id(oid, "input_url", digest), observation_id=oid,
            role=ArtifactRole.input_url, sha256=digest, url=url,
        )
        observation = Observation(
            observation_id=oid, canonical_id=ids.canonical_id([], url), source_ref=source,
            input_kind=InputKind.url_only, artifacts=[artifact], tags=["synthetic"],
        )
        observations.append(observation)
        labels[observation.canonical_id] = name
    pairs = []
    for first, second in combinations(observations, 2):
        pair = tuple(sorted((first.canonical_id, second.canonical_id)))
        names = sorted(labels[m] for m in pair)
        missing = set(names).intersection(config["unobserved"])
        score = None if missing else config["comparisons"].get("".join(names), 0.0)
        refs = {} if missing else {
            o.canonical_id: [ArtifactRef(artifact_id=o.artifacts[0].artifact_id)] for o in (first, second)
        }
        pairs.append(PairEvidence(
            run_id=config["dataset_version"], pair=pair, method_id="synthetic-sim-v1",
            relation=Relation.shared_template, score=score, evidence_refs=refs, explanation="Próba kontrolna K03.",
        ))
    return observations, pairs, labels


def _summarize(run, labels):
    groups = defaultdict(list)
    by_status = defaultdict(list)
    for assignment in run.assignments:
        material = labels[assignment.material_id]
        by_status[assignment.status].append(material)
        for cluster_id in assignment.cluster_ids:
            groups[cluster_id].append(material)
    grouped = sorted(sorted(g) for g in groups.values())
    statuses = {status.value: sorted(by_status[status]) for status in AssignmentStatus}
    counts = {status: len(members) for status, members in statuses.items()}
    comparable = len(run.assignments) - counts["insufficient_data"]
    return {
        "groups": grouped,
        "largest_group": max(map(len, grouped), default=0),
        "materials_by_status": statuses,
        "status_counts": counts,
        "noise_share": counts["noise"] / comparable if comparable else None,
    }


def _grouped_pairs(summary):
    return {pair for group in summary["groups"] for pair in combinations(group, 2)}


def _agreement(a, b):
    left, right = _grouped_pairs(a), _grouped_pairs(b)
    return len(left & right) / len(left | right) if left or right else None


def _compare(observations, pairs, labels, config, profiles, *, seed=0):
    results = {}
    for algorithm in ALGORITHMS:
        runs = {}
        for name, overrides in profiles[algorithm].items():
            cfg = {**config, **profiles[algorithm]["base"], **overrides}
            start = perf_counter()
            result = build_result(observations, pairs, cfg, algorithm=algorithm, seed=seed)
            elapsed = perf_counter() - start
            runs[name] = {
                **_summarize(result.run, labels), "elapsed_seconds": elapsed,
                "run": result.run.model_dump(mode="json"),
                "evidence": {labels[e.material_id]: asdict(e) for e in result.evidence},
            }
        for summary in runs.values():
            summary["pair_agreement_with_algorithm_base"] = _agreement(runs["base"], summary)
        results[algorithm] = runs

    return {
        "dataset_version": config["dataset_version"], "labels": labels,
        "pairs": [p.model_dump(mode="json") for p in pairs],
        "comparison_counts": Counter("missing" if p.score is None else "scored" for p in pairs),
        "algorithms": results,
        "base_pair_agreement": {
            f"{left}/{right}": _agreement(results[left]["base"], results[right]["base"])
            for left, right in combinations(ALGORITHMS, 2)
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/density/comparison.json"))
    parser.add_argument("--baseline-config", type=Path, default=Path("configs/baseline/baseline.json"))
    parser.add_argument("--store", type=Path, help="Magazyn F02 dla oryginalnych zapisów F04.")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    _backend()  # Pierwszy odczyt modułu jest poza czasem klastrowania.
    control = config["control"]
    report = {"control": _compare(
        *_control(control), {"dataset_version": control["dataset_version"], "similarity_version": "synthetic-sim-v1"},
        config["profiles"],
    )}
    if args.store:
        baseline_cfg = load_baseline_config(args.baseline_config)
        f04 = run_baseline(args.store, baseline_cfg)
        if failed := [check["name"] for check in f04["report"]["checks"] if not check["ok"]]:
            parser.error(f"magazyn nie przeszedł kontroli F04: {', '.join(failed)}")
        bundle = f04["bundle"]
        pattern = baseline_cfg["corpus"]["label_pattern"]
        aliases = defaultdict(set)
        for o in bundle.observations:
            match = re.fullmatch(pattern, o.source_ref)
            aliases[o.canonical_id].add(match.group(1) if match else o.source_ref)
        labels = {material: "|".join(sorted(names)) for material, names in aliases.items()}
        base_run = bundle.runs[0]
        real = _compare(
            bundle.observations, bundle.pairs, labels,
            {**baseline_cfg["run"]["cluster_config"], "dataset_version": base_run.dataset_version,
             "relation": base_run.relation.value},
            config["profiles"], seed=base_run.seed,
        )
        baseline = _summarize(base_run, labels)
        for runs in real["algorithms"].values():
            for summary in runs.values():
                summary["pair_agreement_with_f04"] = _agreement(baseline, summary)
        report["f04"] = {**real, "reference": baseline}
    print(json.dumps(report, default=lambda edge: edge.model_dump(mode="json"), ensure_ascii=False, indent=2,
                     allow_nan=False))


if __name__ == "__main__":
    main()
