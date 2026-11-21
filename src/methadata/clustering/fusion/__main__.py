"""CLI: python -m methadata.clustering.fusion run --store DIR --baseline-config configs/baseline/baseline.json
        --config configs/fusion/fusion.json --out DIR"""
import argparse
import json
import sys

from .run import run_fusion, write_outputs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="methadata.clustering.fusion")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--store", required=True)
    r.add_argument("--baseline-config", required=True)
    r.add_argument("--config", required=True)
    r.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    base = json.load(open(a.baseline_config, encoding="utf-8"))
    fcfg = {k: v for k, v in json.load(open(a.config, encoding="utf-8")).items() if k != "name"}
    res = run_fusion(a.store, base, fcfg)
    write_outputs(res, a.out)
    rep = res["report"]
    print(f"run {rep['run_id']} dataset {rep['dataset_version']}")
    print("grupy:", ["{" + ",".join(g) + "}" for g in rep["groups"]])
    print("statusy par:", rep["status_counts"])
    for k, g in rep["ablation_groups"].items():
        print(f"bez rodziny {k}:", ["{" + ",".join(x) + "}" for x in g])
    return 0


if __name__ == "__main__":
    sys.exit(main())
