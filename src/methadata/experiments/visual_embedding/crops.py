"""Reguły wyboru wycinków (zamrożone): pełny obraz, siatka 2x2, środek o danym polu. Rect = (x, y, w, h) w px."""
from __future__ import annotations

from typing import Any, Mapping

Rect = tuple[int, int, int, int]


def crop_rects(width: int, height: int, cfg: Mapping[str, Any]) -> list[tuple[str, Rect]]:
    rules = list(cfg["crop_rules"])
    out: list[tuple[str, Rect]] = []
    if "whole" in rules:
        out.append(("whole", (0, 0, width, height)))
    if "grid2x2" in rules:
        hw, hh = width // 2, height // 2
        for name, (x, y) in {"tl": (0, 0), "tr": (width - hw, 0), "bl": (0, height - hh),
                             "br": (width - hw, height - hh)}.items():
            out.append((f"g2-{name}", (x, y, hw, hh)))
    if "center50" in rules:
        s = float(cfg["center_area"]) ** 0.5
        cw, ch = max(1, round(width * s)), max(1, round(height * s))
        out.append(("center", ((width - cw) // 2, (height - ch) // 2, cw, ch)))
    ms = int(cfg["min_crop_side"])
    return [(n, r) for n, r in out if n == "whole" or min(r[2], r[3]) >= ms]
