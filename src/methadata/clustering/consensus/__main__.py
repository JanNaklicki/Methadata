"""CLI: python -m methadata.clustering.consensus run --store DIR --baseline-config configs/baseline/baseline.json
        --fusion-config configs/fusion/fusion.json --config configs/consensus/consensus.json --out DIR"""
import argparse
import json
import sys

from .run import run_consensus, write_outputs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="methadata.clustering.consensus")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    for a in ("store", "baseline-config", "fusion-config", "config", "out"):
        r.add_argument("--" + a, required=True)
    a = ap.parse_args(argv)
    load = lambda p: json.load(open(p, encoding="utf-8"))  # noqa: E731
    fcfg = {k: v for k, v in load(a.fusion_config).items() if k != "name"}
    res = run_consensus(a.store, load(a.baseline_config), fcfg, load(a.config))
    write_outputs(res, a.out)
    rep = res["report"]
    print(f"run {rep['run_id']} dataset {rep['dataset_version']}; uruchomień wejściowych: {len(rep['input_runs'])}")
    for c in rep["cores"]:
        print("rdzeń:", "{" + ",".join(c["members"]) + "}", "min_zgodność", c["min_agreement"],
              "min_ocenialne", c["min_evaluable_runs"])
    for x in rep["ablations"]:
        print(f"bez rodziny {x['family']}: retencja {x['core_pair_retention']} dominuje={x['dominant']}")
    print("ostrzeżenia:", rep["warnings"] or "brak")
    return 0


if __name__ == "__main__":
    sys.exit(main())
