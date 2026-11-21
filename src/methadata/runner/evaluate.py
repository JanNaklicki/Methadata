"""Spięcie runnera z oceną: przebieg + referencja (+ manifest) -> raport."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional

from ..contracts import Bundle
from ..evaluation import Reference, build_report, load_reference, render_markdown


def load_manifest(path: str | Path) -> dict:
    """Manifest: {dataset_version, materials:[{id, family, sha256?, aliases?}]}. Zwraca z polem `families`."""
    m = json.loads(Path(path).read_text(encoding="utf-8"))
    m["families"] = {x["id"]: x["family"] for x in m.get("materials", []) if x.get("family")}
    return m


def evaluate_run(record: Mapping[str, Any], bundle: Bundle, reference: Reference, *,
                 manifest: Optional[Mapping[str, Any]] = None, newcomer_queries=()) -> dict:
    cfg = record.get("config", {})
    sp = record.get("split") or {}
    rep = build_report(bundle.runs[0], reference, observations=bundle.observations, evidence=bundle.pairs,
                       families=(manifest or {}).get("families") or None, split_seed=sp.get("seed", record["seed"]),
                       heldout_fraction=sp.get("heldout_fraction", 0.3),
                       newcomer_queries=newcomer_queries or cfg.get("newcomer_queries", []),
                       meta={"timings": record["timings"], "adapters": record["adapters"],
                             "feature_versions": record["feature_versions"], "models": record["models"],
                             "config_hash": record["config_hash"], "manifest_dataset_version": (manifest or {}).get("dataset_version")})
    return rep


def evaluate_dir(run_dir: str | Path, reference_path: str | Path, manifest_path: str | Path | None = None,
                 out: str | Path | None = None) -> dict:
    from .run import load_run
    record, bundle = load_run(run_dir)
    rep = evaluate_run(record, bundle, load_reference(reference_path),
                       manifest=load_manifest(manifest_path) if manifest_path else None)
    if out:
        o = Path(out)
        o.mkdir(parents=True, exist_ok=True)
        (o / "report.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        (o / "report.md").write_text(render_markdown(rep), encoding="utf-8")
    return rep
