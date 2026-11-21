"""Uruchomienie: python -m methadata.clustering.bicluster --export graph_edges.json --config configs/bicluster/bicluster.json"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import build_result, load_export


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="K08: biklastrowanie macierzy E14")
    ap.add_argument("--export", required=True, help="eksport E14 (JSON, artefakt graph_edges)")
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", help="plik wyjściowy JSON (domyślnie stdout)")
    a = ap.parse_args(argv)
    res = build_result(load_export(Path(a.export).read_bytes()), json.loads(Path(a.config).read_text("utf-8")))
    out = {"run": res.run.model_dump(mode="json"), "config": res.config, "n_materials": res.n_materials,
           "coverage": res.coverage, "truncated": res.truncated,
           "biclusters": [b.to_dict() | {"evidence": [{"material": e.material, "trace": e.trace,
                                                        "observation_id": e.observation_id,
                                                        "feature_ids": list(e.feature_ids),
                                                        "evidence_refs": list(e.evidence_refs)}
                                                       for e in b.evidence]} for b in res.biclusters]}
    text = json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True)
    if a.out:
        Path(a.out).write_text(text, "utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
