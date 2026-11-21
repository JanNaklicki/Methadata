"""python -m methadata.experiments.learned_pairs --input in.json --out DIR
in.json: {"methods":[..], "scores":[[a,b,{method:score|null}],..], "labels":[[a,b,"yes|no|unknown"],..], "families":{material:rodzina}}
Dane syntetyczne oznacz "source":"synthetic"."""
import argparse
import json

from . import core


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="learned_pairs")
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    d = json.load(open(a.input, encoding="utf-8"))
    scores = {core.norm_pair(x, y): s for x, y, s in d["scores"]}
    labels = {core.norm_pair(x, y): v for x, y, v in d["labels"]}
    rep = core.run(scores, labels, d["families"], d["methods"], seed=a.seed, source=d.get("source", "real"))
    print(core.write_report(rep, a.out), rep["status"])
    for m in rep.get("missing", []):
        print("brakuje:", m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
