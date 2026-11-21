"""Pomiar E09: odporność (JPEG / skala / kadr osobno) i fałszywe trafienia na ikonach i awatarach.
Obrazy syntetyczne (deterministyczne, ziarno) oraz opcjonalnie zrzuty z BACKUP (próbka kontrolna, osobno)."""
from __future__ import annotations

import random
from typing import Any, Iterable, Mapping

from . import keypoints as K
from . import phash as P
from . import variants as V
from .config import DEFAULTS


def synthetic_illustration(seed: int, size: tuple[int, int] = (640, 480)):
    """Ilustracja: losowe kolorowe prostokąty, elipsy i linie na gradiencie (dużo narożników)."""
    import numpy as np
    from PIL import Image, ImageDraw
    r = random.Random(seed)
    w, h = size
    base = np.zeros((h, w, 3), np.uint8)
    c0, c1 = np.array([r.randrange(256) for _ in range(3)]), np.array([r.randrange(256) for _ in range(3)])
    t = np.linspace(0, 1, w)[None, :, None]
    base[:] = (c0 * (1 - t) + c1 * t).astype(np.uint8)
    im = Image.fromarray(base)
    d = ImageDraw.Draw(im)
    for _ in range(60):
        x, y = r.randrange(w), r.randrange(h)
        x2, y2 = x + r.randrange(20, 140), y + r.randrange(20, 120)
        col = tuple(r.randrange(256) for _ in range(3))
        (d.rectangle if r.random() < 0.5 else d.ellipse)([x, y, x2, y2], fill=col, outline=(0, 0, 0))
    for _ in range(25):
        d.line([r.randrange(w), r.randrange(h), r.randrange(w), r.randrange(h)],
               fill=tuple(r.randrange(256) for _ in range(3)), width=r.randrange(1, 4))
    return im


def synthetic_icon(seed: int, side: int = 64):
    """Ikona: jedna prosta figura na jednolitym tle (mało informacji -> ryzyko kolizji pHash)."""
    from PIL import Image, ImageDraw
    r = random.Random(seed)
    im = Image.new("RGB", (side, side), tuple(r.randrange(256) for _ in range(3)))
    d = ImageDraw.Draw(im)
    m = side // 6
    box = [m, m, side - m, side - m]
    col = tuple(r.randrange(256) for _ in range(3))
    kind = r.randrange(3)
    (d.ellipse if kind == 0 else d.rectangle if kind == 1 else d.polygon)(
        box if kind < 2 else [(side // 2, m), (side - m, side - m), (m, side - m)], fill=col)
    return im


def synthetic_avatar(seed: int, side: int = 160):
    """Awatar: gładka tarcza głowy i ramion na gradiencie (niski kontrast wysokich częstotliwości)."""
    from PIL import Image, ImageDraw, ImageFilter
    r = random.Random(seed)
    bg = tuple(r.randrange(80, 256) for _ in range(3))
    im = Image.new("RGB", (side, side), bg)
    d = ImageDraw.Draw(im)
    skin = tuple(r.randrange(120, 240) for _ in range(3))
    d.ellipse([side * .3, side * .15, side * .7, side * .55], fill=skin)
    d.ellipse([side * .15, side * .6, side * .85, side * 1.2], fill=tuple(r.randrange(256) for _ in range(3)))
    return im.filter(ImageFilter.GaussianBlur(2))


def _desc(im, cfg):
    g = im.convert("L")
    ph = P.phash_gray(g, cfg["phash_flat_std"])
    return ph, K.detect(g, cfg)


def verdict(a_im, b_im, cfg: Mapping[str, Any] = DEFAULTS) -> dict[str, Any]:
    pa, ka = _desc(a_im, cfg)
    pb, kb = _desc(b_im, cfg)
    d = None if pa is None or pb is None else P.hamming(pa, pb)
    m = K.match(ka, kb, cfg)
    n = 0 if m is None else m.inliers
    return {"hamming": d, "phash_hit": d is not None and d <= cfg["phash_threshold"],
            "inliers": n, "kp_hit": n >= cfg["kp_min_inliers"], "match": m}


def robustness(seeds: Iterable[int] = range(10), cfg: Mapping[str, Any] = DEFAULTS) -> dict[str, dict[str, Any]]:
    """Osobno: kompresja, skalowanie, kadrowanie. Zwraca {test: {n, phash_recall, kp_recall, mean_hamming}}."""
    import numpy as np
    tests = {
        "jpeg_q75": lambda im: V.jpeg(im, 75), "jpeg_q40": lambda im: V.jpeg(im, 40), "jpeg_q15": lambda im: V.jpeg(im, 15),
        "scale_0.5": lambda im: V.scale(im, 0.5), "scale_0.25": lambda im: V.scale(im, 0.25),
        "scale_1.5": lambda im: V.scale(im, 1.5),
        "crop_80pct": lambda im: V.crop(im, 0.8)[0], "crop_50pct": lambda im: V.crop(im, 0.5, (0.2, 0.7))[0],
        "crop_30pct": lambda im: V.crop(im, 0.3, (0.9, 0.1))[0],
    }
    out = {}
    for name, fn in tests.items():
        rows = [verdict(synthetic_illustration(s), fn(synthetic_illustration(s)), cfg) for s in seeds]
        ds = [r["hamming"] for r in rows if r["hamming"] is not None]
        out[name] = {"n": len(rows), "phash_recall": sum(r["phash_hit"] for r in rows) / len(rows),
                     "kp_recall": sum(r["kp_hit"] for r in rows) / len(rows),
                     "mean_hamming": round(float(np.mean(ds)), 1) if ds else None,
                     "mean_inliers": round(float(np.mean([r["inliers"] for r in rows])), 1)}
    return out


def false_hits(n: int = 60, cfg: Mapping[str, Any] = DEFAULTS) -> dict[str, dict[str, Any]]:
    """Fałszywe trafienia: pary RÓŻNYCH obrazów (inne ziarno). Ikony i awatary osobno; ilustracje jako odniesienie.
    pHash liczony dla wszystkich grup (to właśnie mierzymy); komparatory produktu pomijają ikony."""
    groups = {"icon": lambda s: synthetic_icon(s), "avatar": lambda s: synthetic_avatar(s),
              "illustration": lambda s: synthetic_illustration(s, (320, 240))}
    out = {}
    for g, mk in groups.items():
        ims = [mk(s) for s in range(n)]
        feats = [(P.phash_gray(i.convert("L"), cfg["phash_flat_std"]), i) for i in ims]
        pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
        flat = sum(1 for p, _ in feats if p is None)
        ph_hits = sum(1 for i, j in pairs if feats[i][0] is not None and feats[j][0] is not None
                      and P.hamming(feats[i][0], feats[j][0]) <= cfg["phash_threshold"])
        ph_hits_nonflat_cmp = sum(1 for i, j in pairs if feats[i][0] is not None and feats[j][0] is not None)
        kp = [K.detect(i.convert("L"), cfg) for i in ims] if g != "icon" else None
        kp_hits = None
        if kp is not None:
            kp_hits = 0
            for i, j in pairs[: min(len(pairs), 600)]:
                m = K.match(kp[i], kp[j], cfg)
                kp_hits += int(m is not None and m.inliers >= cfg["kp_min_inliers"])
        out[g] = {"images": n, "pairs": len(pairs), "flat_no_phash": flat,
                  "phash_comparable_pairs": ph_hits_nonflat_cmp, "phash_false_hits": ph_hits,
                  "phash_false_rate": round(ph_hits / max(1, ph_hits_nonflat_cmp), 4),
                  "kp_false_hits": kp_hits, "kp_pairs_checked": None if kp is None else min(len(pairs), 600)}
    return out


def render_match(a_im, b_im, m, out_path: str) -> None:
    """Obraz zestawienia: oryginał | wariant z zaznaczonym położeniem dopasowań i liniami między punktami."""
    from PIL import Image, ImageDraw
    a, b = a_im.convert("RGB"), b_im.convert("RGB")
    H = max(a.height, b.height)
    canvas = Image.new("RGB", (a.width + b.width + 10, H), (255, 255, 255))
    canvas.paste(a, (0, 0))
    canvas.paste(b, (a.width + 10, 0))
    d = ImageDraw.Draw(canvas)
    if m is not None and m.inliers:
        x, y, w, h = m.rect_a
        d.rectangle([x, y, x + w, y + h], outline=(255, 0, 0), width=3)
        x, y, w, h = m.rect_b
        o = a.width + 10
        d.rectangle([o + x, y, o + x + w, y + h], outline=(255, 0, 0), width=3)
        for (p, q) in m.pairs[:30]:
            d.line([p[0], p[1], o + q[0], q[1]], fill=(0, 160, 0), width=1)
    canvas.save(out_path)
