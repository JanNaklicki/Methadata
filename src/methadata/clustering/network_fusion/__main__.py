"""CLI: python -m methadata.clustering.network_fusion run --store DIR --baseline-config configs/baseline/baseline.json
        --config configs/network_fusion/snf.json --out DIR [--reference configs/fusion/eval.json]"""
import argparse
import json
import sys

from .run import run_snf, write_outputs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="methadata.clustering.network_fusion")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--store", required=True)
    r.add_argument("--baseline-config", required=True)
    r.add_argument("--config", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--reference", help="eval.json z grupami odniesienia (próbka kontrolna, raportowana osobno)")
    a = ap.parse_args(argv)
    base = json.load(open(a.baseline_config, encoding="utf-8"))
    cfg = {k: v for k, v in json.load(open(a.config, encoding="utf-8")).items() if k != "name"}
    ref = json.load(open(a.reference, encoding="utf-8"))["reference"]["families"] if a.reference else None
    res = run_snf(a.store, base, cfg, reference=ref)
    write_outputs(res, a.out)
    rep = res["report"]
    g = lambda gs: ["{" + ",".join(x) + "}" for x in gs]  # noqa: E731
    print(f"run {rep['run_id']} dataset {rep['dataset_version']}")
    print("podzbiór z kompletnymi widokami:", len(rep["subset"]), rep["subset"], "wykluczone:", rep["excluded_missing_views"])
    print("grupy SNF:", g(rep["snf_groups"]))
    print("grupy K09:", g(rep["k09"]["groups"]), "identyczne:", rep["k09"]["identical_to_snf"])
    print("krawędzie wzmocnione przez sąsiedztwo:", [(e["a"], e["b"]) for e in rep["amplified_edges"]])
    return 0


if __name__ == "__main__":
    sys.exit(main())
