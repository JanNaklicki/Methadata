"""python -m methadata.experiments.perceptual {bench|demo} [--out DIR] [--config PLIK.json] [--real]"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import bench, variants as V
from .config import load_config


def _real_sources():
    from methadata.devtools.realdata import BACKUP
    if not BACKUP.is_dir():
        return []
    return sorted(BACKUP.rglob("*.png"))[:5]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="methadata.experiments.perceptual")
    ap.add_argument("cmd", choices=["bench", "demo"])
    ap.add_argument("--out", help="katalog wyników (poza repo)")
    ap.add_argument("--config")
    ap.add_argument("--real", action="store_true", help="dodaj próbkę kontrolną z BACKUP (wynik osobno)")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    out = Path(a.out) if a.out else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
    if a.cmd == "bench":
        res = {"config": cfg, "robustness_synthetic": bench.robustness(cfg=cfg),
               "false_hits_synthetic": bench.false_hits(cfg=cfg)}
        if a.real:
            from PIL import Image
            rows = []
            for p in _real_sources():
                im = Image.open(p).convert("RGB")
                for name, v in (("jpeg_q40", V.jpeg(im, 40)), ("scale_0.5", V.scale(im, 0.5)),
                                ("crop_50pct", V.crop(im, 0.5, (0.3, 0.6))[0])):
                    r = bench.verdict(im, v, cfg)
                    rows.append({"sample": "real_control", "variant": name, "hamming": r["hamming"],
                                 "inliers": r["inliers"], "phash_hit": r["phash_hit"], "kp_hit": r["kp_hit"]})
            res["real_control_sample"] = rows
        txt = json.dumps(res, indent=2, ensure_ascii=False)
        (out / "bench.json").write_text(txt, encoding="utf-8") if out else None
        print(txt)
        return 0
    base = bench.synthetic_illustration(7)
    for name, v in (("jpeg_q30", V.jpeg(base, 30)), ("scale_0.5", V.scale(base, 0.5)),
                    ("crop_40pct", V.crop(base, 0.4, (0.7, 0.3))[0])):
        r = bench.verdict(base, v, cfg)
        print(f"{name}: hamming={r['hamming']} phash_hit={r['phash_hit']} inliers={r['inliers']} kp_hit={r['kp_hit']}")
        if out:
            bench.render_match(base, v, r["match"], str(out / f"demo_{name}.png"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
