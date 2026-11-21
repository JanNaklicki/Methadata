"""Wynik OCR z prostokątami: piksele + koordynaty względne (0..1), niezależne od rozdzielczości."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional

Rect = tuple[float, float, float, float]  # x, y, w, h

_PL = str.maketrans({"ł": "l", "Ł": "l", "ø": "o", "ß": "ss", "đ": "d"})


def norm(text: str) -> str:
    """Normalizacja do porównań: bez diakrytyków (OCR często je gubi), małe litery, tylko alfanumeryczne."""
    t = unicodedata.normalize("NFKD", text.translate(_PL))
    t = "".join(c for c in t if not unicodedata.combining(c)).casefold()
    return re.sub(r"[^0-9a-z]+", " ", t).strip()


def union(rects) -> Rect:
    rects = list(rects)
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[0] + r[2] for r in rects)
    y1 = max(r[1] + r[3] for r in rects)
    return (x0, y0, x1 - x0, y1 - y0)


@dataclass
class OcrLine:
    text: str
    rect: Rect          # piksele (x, y, w, h)
    conf: float = 1.0


@dataclass
class OcrResult:
    width: int
    height: int
    lines: list[OcrLine] = field(default_factory=list)
    engine: str = "unknown"
    engine_version: str = ""

    def rel(self, rect: Rect) -> Rect:
        return (rect[0] / self.width, rect[1] / self.height, rect[2] / self.width, rect[3] / self.height)

    def px(self, rel: Rect) -> Rect:
        return (rel[0] * self.width, rel[1] * self.height, rel[2] * self.width, rel[3] * self.height)

    def filtered(self, min_conf: float) -> "OcrResult":
        return OcrResult(self.width, self.height, [l for l in self.lines if l.conf >= min_conf and norm(l.text)],
                         self.engine, self.engine_version)

    @property
    def mean_conf(self) -> Optional[float]:
        return sum(l.conf for l in self.lines) / len(self.lines) if self.lines else None

    def to_dict(self) -> dict[str, Any]:
        return {"width": self.width, "height": self.height, "engine": self.engine,
                "engine_version": self.engine_version,
                "lines": [{"text": l.text, "conf": round(l.conf, 4), "rect": [round(v, 1) for v in l.rect],
                           "rel": [round(v, 5) for v in self.rel(l.rect)]} for l in self.lines]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "OcrResult":
        return cls(int(d["width"]), int(d["height"]),
                   [OcrLine(l["text"], tuple(l["rect"]), float(l.get("conf", 1.0))) for l in d.get("lines", [])],
                   d.get("engine", "unknown"), d.get("engine_version", ""))
