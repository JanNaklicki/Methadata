"""Generowanie wersjonowanego JSON Schema: `python -m methadata.contracts.schema [katalog]`."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import BaseModel

from . import models as m

RECORDS: dict[str, type[BaseModel]] = {
    "observation": m.Observation, "artifact": m.Artifact, "feature": m.Feature,
    "extraction_status": m.ExtractionStatus, "pair_evidence": m.PairEvidence,
    "cluster_run": m.ClusterRun, "artifact_ref": m.ArtifactRef, "bundle": m.Bundle,
}
DEFAULT_DIR = Path(__file__).parent / "schemas" / f"v{m.SCHEMA_VERSION}"


def build() -> dict[str, dict]:
    out = {}
    for name, cls in RECORDS.items():
        s = cls.model_json_schema(mode="validation")
        s["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        s["$id"] = f"https://methadata.local/schemas/v{m.SCHEMA_VERSION}/{name}.json"
        out[name] = s
    return out


def write(directory: Path = DEFAULT_DIR) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, s in build().items():
        p = directory / f"{name}.json"
        p.write_text(json.dumps(s, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        paths.append(p)
    return paths


if __name__ == "__main__":
    for p in write(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DIR):
        print(p)
