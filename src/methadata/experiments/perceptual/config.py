"""Progi E09. Jedyne źródło wartości domyślnych; konfiguracja `configs/perceptual/perceptual.json` je odzwierciedla."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

DEFAULTS: dict[str, Any] = {
    "icon_max_side": 256,            # grupa kontrolna: ikony i awatary (max bok <= wartość); pHash daje na nich
                                     # 16-28% fałszywych trafień (bench), więc nie wchodzą do dowodów
    "phash_threshold": 10,           # odległość Hamminga (z 64 bitów) uznawana za trafienie
    "phash_flat_std": 2.0,           # obraz o odchyleniu jasności poniżej tego nie ma pHash (płaski)
    "kp_max_features": 300,          # punkty ORB na obraz
    "kp_work_side": 640,             # bok roboczy obrazu przy detekcji punktów
    "kp_ratio": 0.75,                # test Lowe'a dla dopasowań deskryptorów
    "kp_ransac_px": 4.0,             # próg reprojekcji RANSAC (piksele skali roboczej)
    "kp_min_inliers": 15,            # minimalna liczba zgodnych geometrycznie dopasowań
    "kp_target_inliers": 40,         # liczba inlierów dająca wynik 1.0
    "kp_scale_range": [0.1, 10.0],   # dopuszczalna skala transformacji podobieństwa
    "max_images": 8,                 # maks. grafik porównywanych na obserwację
}


def load_config(path: str | Path | None = None, override: Mapping[str, Any] | None = None) -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    if path:
        cfg.update(json.loads(Path(path).read_text(encoding="utf-8")))
    cfg.update(override or {})
    return cfg
