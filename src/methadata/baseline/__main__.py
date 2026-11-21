"""CLI: python -m methadata.baseline run --store DIR --config configs/baseline/baseline.json --out DIR"""
import argparse
import sys

from .core import BaselineError, load_baseline_config, run_baseline, write_outputs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="methadata.baseline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="importer(Store) -> assets-jaccard -> components -> weryfikacja pomiaru")
    r.add_argument("--store", required=True, help="magazyn utworzony przez `python -m methadata.ingest import`")
    r.add_argument("--config", required=True)
    r.add_argument("--out", required=True, help="katalog wyników (poza repozytorium)")
    r.add_argument("--no-verify", action="store_true", help="pomiń porównanie z sekcją `expected`")
    a = ap.parse_args(argv)
    try:
        cfg = load_baseline_config(a.config)
        res = run_baseline(a.store, cfg, verify=not a.no_verify)
    except BaselineError as e:
        print(f"BŁĄD: {e}", file=sys.stderr)
        return 2
    write_outputs(res, a.out)
    rep = res["report"]
    print(f"run {rep['run_id']}: materiały={rep['counts']['materials']} pary={rep['counts']['pairs']} "
          f"pary ze wspólnymi grafikami={rep['counts']['pairs_with_shared_images']}")
    print("grupy:", ["{" + ",".join(g) + "}" for g in rep["groups"]])
    print("singletony:", ",".join(rep["singletons"]))
    print("samodzielne URL (poza zbiorem):", len(rep["standalone_urls"]))
    for c in rep["checks"]:
        print(("OK   " if c["ok"] else "FAIL ") + c["name"] + (f" — {c['detail']}" if c.get("detail") else ""))
    return 0 if all(c["ok"] for c in rep["checks"]) else 1


if __name__ == "__main__":
    sys.exit(main())
