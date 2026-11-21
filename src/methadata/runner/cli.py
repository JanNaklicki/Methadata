"""CLI: python -m methadata.runner {run,import-labels,evaluate,compare}."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..evaluation import compare_reports, import_annotations, render_markdown
from .evaluate import evaluate_dir
from .run import load_config, run_from_config


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="methadata-runner")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="uruchom skonfigurowane adaptery i zapisz przebieg")
    r.add_argument("--config", required=True)
    r.add_argument("--out")
    i = sub.add_parser("import-labels", help="scal oceny dwóch anotatorów w referencję")
    i.add_argument("--a", required=True); i.add_argument("--b", required=True)
    i.add_argument("--adjudication"); i.add_argument("--out", required=True)
    i.add_argument("--lenient", action="store_true", help="nierozpoznane etykiety raportuj zamiast przerywać")
    e = sub.add_parser("evaluate", help="raport oceny par dla przebiegu")
    e.add_argument("--run", required=True); e.add_argument("--reference", required=True)
    e.add_argument("--manifest"); e.add_argument("--out", required=True)
    c = sub.add_parser("compare", help="porównaj dwa raporty (te same pary)")
    c.add_argument("--a", required=True); c.add_argument("--b", required=True); c.add_argument("--out")
    a = p.parse_args(argv)

    if a.cmd == "run":
        cfg = load_config(a.config)
        if a.out:
            cfg["out_dir"] = a.out
        res = run_from_config(cfg)
        print(f"run {res['record']['run_id']}: {res['record']['counts']} -> {cfg.get('out_dir', '(bez zapisu)')}")
    elif a.cmd == "import-labels":
        ref = import_annotations(a.a, a.b, a.adjudication, strict=not a.lenient)
        Path(a.out).write_text(json.dumps(ref.to_json(), ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"pary: {len(ref.pairs())}, rozbieżności: {len(ref.disagreements)}, nierozpoznane etykiety: {len(ref.invalid)}")
    elif a.cmd == "evaluate":
        rep = evaluate_dir(a.run, a.reference, a.manifest, a.out)
        print(render_markdown(rep))
    elif a.cmd == "compare":
        ra = json.loads((Path(a.a) / "report.json").read_text(encoding="utf-8"))
        rb = json.loads((Path(a.b) / "report.json").read_text(encoding="utf-8"))
        out = compare_reports(ra, rb)
        txt = json.dumps(out, indent=1, ensure_ascii=False)
        if a.out:
            Path(a.out).write_text(txt, encoding="utf-8")
        print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
