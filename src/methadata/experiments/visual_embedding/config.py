"""Zamrożona konfiguracja E11: wersja modelu i reguły wyboru wycinków. `configs/visual_embedding/visual_embedding.json` ją odzwierciedla."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

DEFAULTS: dict[str, Any] = {
    "backend": "open_clip",                 # open_clip | color-layout (bazowy, bez modelu, nieseman.)
    "model": "ViT-B-32",                    # zamrożone: architektura open_clip
    "pretrained": "laion2b_s34b_b79k",      # zamrożone: wagi (publiczne)
    "model_cache_env": "METHADATA_MODEL_CACHE",  # katalog cache wag (poza repo); domyślnie katalog tymczasowy
    "icon_max_side": 256,                   # jak E09: ikony/awatary to grupa kontrolna, bez wektorów
    "crop_rules": ["whole", "grid2x2", "center50"],  # zamrożone reguły wycinków (kolejność = identyfikatory)
    "center_area": 0.5,                     # pole wycinka środkowego (ułamek całości)
    "min_crop_side": 64,                    # wycinki o krótszym boku pomijane
    "max_images": 8,                        # maks. grafik na obserwację
    "cos_floor": 0.5,                       # cosinus odpowiadający score=0
    "cos_hit": 0.80,                        # cosinus uznawany za trafienie (opis w wyjaśnieniu)
    "cross_lo": 0.15,                       # wariant podpis-obraz: cosinus tekst-obraz dający score=0
    "cross_hi": 0.35,                       # ... i score=1 (rząd wielkości dla CLIP)
    "vec_decimals": 5,
}


def load_config(path: str | Path | None = None, override: Mapping[str, Any] | None = None) -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    if path:
        cfg.update(json.loads(Path(path).read_text(encoding="utf-8")))
    cfg.update(override or {})
    return cfg
