"""Pomiar E11: czy wektory wizyjne wnoszą pary ponad pHash (E09). Scenariusze: przycięta ilustracja, inne zdjęcie
tej samej kategorii (inna oferta), obraz niepowiązany. Obrazy syntetyczne (próbka kontrolna) i opcjonalnie BACKUP."""
from __future__ import annotations

import random
from typing import Any, Mapping, Optional

from methadata.experiments.perceptual import bench as PB, phash as P, variants as V

from .adapters import _dot
from .backends import Backend, make_backend
from .config import DEFAULTS
from .crops import crop_rects


def synthetic_category(cat_seed: int, offer_seed: int, size: tuple[int, int] = (640, 480)):
    """Ta sama kategoria = wspólna paleta tła i kształtów; oferta = układ elementów (inne ziarno)."""
    from PIL import Image, ImageDraw
    import numpy as np
    rc, ro = random.Random(cat_seed), random.Random(offer_seed * 7919 + 1)
    w, h = size
    c0, c1 = np.array([rc.randrange(256) for _ in range(3)]), np.array([rc.randrange(256) for _ in range(3)])
    pal = [tuple(rc.randrange(256) for _ in range(3)) for _ in range(4)]
    t = np.linspace(0, 1, h)[:, None, None]
    im = Image.fromarray((c0 * (1 - t) + c1 * t).astype(np.uint8).repeat(w, axis=1))
    d = ImageDraw.Draw(im)
    for _ in range(25):
        x, y = ro.randrange(w), ro.randrange(h)
        box = [x, y, x + ro.randrange(30, 150), y + ro.randrange(30, 120)]
        (d.rectangle if ro.random() < 0.5 else d.ellipse)(box, fill=ro.choice(pal))
    return im


def best_cosine(backend: Backend, a_im, b_im, cfg: Mapping[str, Any]) -> tuple[float, str, str]:
    def vecs(im):
        rs = crop_rects(im.width, im.height, cfg)
        return rs, backend.embed_images([im.convert("RGB").crop((x, y, x + w, y + h)) for _, (x, y, w, h) in rs])
    ra, va = vecs(a_im)
    rb, vb = vecs(b_im)
    best = max(((_dot(u, v), ra[i][0], rb[j][0]) for i, u in enumerate(va) for j, v in enumerate(vb)),
               key=lambda t: t[0])
    return best


def scenario_row(name: str, backend: Backend, a_im, b_im, cfg: Mapping[str, Any]) -> dict[str, Any]:
    cos, ca, cb = best_cosine(backend, a_im, b_im, cfg)
    pa, pb = P.phash_gray(a_im.convert("L")), P.phash_gray(b_im.convert("L"))
    d = None if pa is None or pb is None else P.hamming(pa, pb)
    ph_hit = d is not None and d <= PB.DEFAULTS["phash_threshold"]
    emb_hit = cos >= cfg["cos_hit"]
    return {"scenario": name, "cosine": round(cos, 3), "crops": f"{ca}~{cb}", "emb_hit": emb_hit,
            "hamming": d, "phash_hit": ph_hit, "adds_over_phash": emb_hit and not ph_hit}


def run_scenarios(backend: Optional[Backend] = None, cfg: Mapping[str, Any] = DEFAULTS, seed: int = 1) -> list[dict]:
    be = backend or make_backend(cfg)
    base = synthetic_category(seed, 1)
    return [
        scenario_row("crop50", be, base, V.crop(base, 0.5, (0.2, 0.7))[0], cfg),
        scenario_row("jpeg_q40", be, base, V.jpeg(base, 40), cfg),
        scenario_row("same_category_other_offer", be, base, synthetic_category(seed, 2), cfg),
        scenario_row("unrelated", be, base, synthetic_category(seed + 100, 3), cfg),
    ]


def real_sample(backend: Backend, cfg: Mapping[str, Any] = DEFAULTS, n: int = 3) -> list[dict]:
    """Próbka kontrolna z BACKUP (zrzuty ekranu): oryginał vs jego 50% kadr oraz vs zrzut z innego katalogu."""
    from PIL import Image
    from methadata.devtools.realdata import BACKUP
    if not BACKUP.is_dir():
        return []
    files = sorted(BACKUP.rglob("*.png"))
    rows = []
    for i in range(min(n, len(files) - 1)):
        with Image.open(files[i]) as a, Image.open(files[-1 - i]) as b:
            a, b = a.convert("RGB"), b.convert("RGB")
            rows.append(scenario_row(f"real{i}_crop50", backend, a, V.crop(a, 0.5, (0.3, 0.6))[0], cfg))
            rows.append(scenario_row(f"real{i}_other_screenshot", backend, a, b, cfg))
    return rows
