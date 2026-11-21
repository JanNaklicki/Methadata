"""CLI: python -m methadata.clustering.incremental run --store DIR --baseline-config configs/baseline/baseline.json
        --eval-config configs/fusion/eval.json --config configs/incremental/incremental.json --out DIR"""
import argparse
import json
import sys
from pathlib import Path

from methadata.fusion_eval.core import load_eval_config

from .scenario import load_config, run_scenario


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="methadata.clustering.incremental")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--store", required=True)
    r.add_argument("--baseline-config", default="configs/baseline/baseline.json")
    r.add_argument("--eval-config", default="configs/fusion/eval.json")
    r.add_argument("--config", default="configs/incremental/incremental.json")
    r.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    base = json.loads(Path(a.baseline_config).read_text(encoding="utf-8"))
    rep = run_scenario(a.store, base, load_eval_config(a.eval_config), load_config(a.config))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "incremental.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    for h in rep["holdout"]:
        print(f"{h['held_out']}: {h['decision']} -> {h['assigned_to']}; identyczne z pełnym: {h['new_group_identical']}; "
              f"porównań {h['cost']['incremental_pair_comparisons']} vs pełne {h['cost']['full_all_pairs']}")
    print("leave-one-out:", json.dumps(rep["leave_one_out_summary"], ensure_ascii=False))
    for e in rep["order_experiments"]:
        print("kolejność", e["holdout"], "niezależna:", e["order_independent"], "zgodne z pełnym:", e["orders_identical_to_full"])
    print(f"zapisano: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
