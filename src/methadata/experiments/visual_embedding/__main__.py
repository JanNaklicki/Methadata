"""python -m methadata.experiments.visual_embedding bench [--backend open_clip|color-layout] [--config PLIK] [--real]"""
from __future__ import annotations

import argparse
import json
import sys

from . import bench
from .backends import BackendUnavailable, make_backend
from .config import load_config


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="methadata.experiments.visual_embedding")
    ap.add_argument("cmd", choices=["bench"])
    ap.add_argument("--backend")
    ap.add_argument("--config")
    ap.add_argument("--real", action="store_true", help="dodaj próbkę kontrolną z BACKUP (wynik osobno)")
    a = ap.parse_args(argv)
    cfg = load_config(a.config, {"backend": a.backend} if a.backend else None)
    try:
        be = make_backend(cfg)
        res = {"backend": be.name, "version": be.version, "synthetic": bench.run_scenarios(be, cfg)}
        if a.real:
            res["real_control_sample"] = bench.real_sample(be, cfg)
    except (BackendUnavailable, ImportError) as e:
        print(json.dumps({"status": "unsupported", "detail": str(e)}, ensure_ascii=False))
        return 2
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
