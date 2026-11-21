"""Warianty obrazu do testów odporności: osobno kompresja, skalowanie i kadrowanie."""
from __future__ import annotations

import io


def jpeg(img, quality: int):
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality)
    buf.seek(0)
    from PIL import Image
    return Image.open(buf).convert("RGB")


def scale(img, factor: float):
    from PIL import Image
    w, h = img.size
    return img.resize((max(1, round(w * factor)), max(1, round(h * factor))), Image.Resampling.LANCZOS)


def crop(img, keep: float, anchor: tuple[float, float] = (0.5, 0.5)):
    """Wycinek o polu `keep` (0-1) całości, ten sam stosunek boków, zakotwiczony ułamkiem (ax, ay)."""
    w, h = img.size
    s = keep ** 0.5
    cw, ch = max(1, round(w * s)), max(1, round(h * s))
    x, y = round((w - cw) * anchor[0]), round((h - ch) * anchor[1])
    return img.crop((x, y, x + cw, y + ch)), (x, y, cw, ch)


def to_bytes(img, fmt: str = "PNG", **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    return buf.getvalue()
