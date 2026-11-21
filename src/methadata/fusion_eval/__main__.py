"""CLI: python -m methadata.fusion_eval run --store DIR --baseline-config configs/baseline/baseline.json
        --config configs/fusion/eval.json --out DIR"""
import argparse
import json
import sys

from .core import load_eval_config, run_eval, write_outputs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="methadata.fusion_eval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--store", required=True)
    r.add_argument("--baseline-config", required=True)
    r.add_argument("--config", required=True)
    r.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    base = json.load(open(a.baseline_config, encoding="utf-8"))
    res = run_eval(a.store, base, load_eval_config(a.config))
    out = write_outputs(res, a.out)
    print(res["markdown"])
    print(f"zapisano: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
