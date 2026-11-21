"""pHash 64-bitowy (DCT 32x32 -> blok niskich częstotliwości 8x8) i odległość Hamminga."""
from __future__ import annotations

import io
from functools import lru_cache
from typing import Optional

SIZE, LOW = 32, 8


@lru_cache(maxsize=1)
def _dct_matrix():
    import numpy as np
    n = SIZE
    k = np.arange(n)[:, None]
    m = np.cos(np.pi * (2 * np.arange(n)[None, :] + 1) * k / (2 * n)) * np.sqrt(2.0 / n)
    m[0, :] = np.sqrt(1.0 / n)
    return m


def load_gray(data: bytes):
    """PIL 'L' z bajtów; None dla uszkodzonego lub nieobsługiwanego obrazu. Obraz nie jest wykonywany, tylko dekodowany."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            if im.mode in ("RGBA", "LA", "P"):
                rgba = im.convert("RGBA")
                bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                bg.alpha_composite(rgba)
                return bg.convert("L")
            return im.convert("L")
    except Exception:  # noqa: BLE001 - uszkodzony obraz to brak cechy
        return None


def phash_gray(gray, flat_std: float = 2.0) -> Optional[int]:
    """64-bitowy pHash; None dla obrazu płaskiego (brak informacji - hash byłby kolizją wszystkich płaskich)."""
    import numpy as np
    from PIL import Image
    px = np.asarray(gray.resize((SIZE, SIZE), Image.Resampling.LANCZOS), dtype=np.float64)
    if float(px.std()) < flat_std:
        return None
    d = _dct_matrix()
    low = (d @ px @ d.T)[:LOW, :LOW].ravel()
    med = float(np.median(low[1:]))  # bez składowej stałej
    bits = 0
    for v in low:
        bits = (bits << 1) | int(v > med)
    return bits


def phash_bytes(data: bytes, flat_std: float = 2.0) -> Optional[int]:
    g = load_gray(data)
    return None if g is None else phash_gray(g, flat_std)


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def to_hex(h: int) -> str:
    return f"{h:016x}"


def from_hex(s: str) -> int:
    return int(s, 16)
