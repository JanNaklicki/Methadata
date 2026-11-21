"""CLI: python -m methadata.experiments.counterfactual run --store DIR --baseline-config configs/baseline/baseline.json
        --eval-config configs/fusion/eval.json --config configs/counterfactual/e18.json --out DIR"""
import argparse
import json
import sys

from methadata.fusion_eval.core import load_eval_config

from .core import load_config, run_counterfactual, write_outputs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="methadata.experiments.counterfactual")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--store", required=True)
    r.add_argument("--baseline-config", required=True)
    r.add_argument("--eval-config", required=True)
    r.add_argument("--config", required=True)
    r.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    base = json.load(open(a.baseline_config, encoding="utf-8"))
    res = run_counterfactual(a.store, base, load_eval_config(a.eval_config), load_config(a.config))
    out = write_outputs(res, a.out)
    print(res["markdown"])
    print(f"zapisano: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
