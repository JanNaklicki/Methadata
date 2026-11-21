"""Generator jawnie syntetycznego zestawu demo (bez żadnych danych rzeczywistych).

  python backend/methadata_api/demo_data/generate_demo.py

Tworzy deterministyczne, maleńkie obrazy PNG (wzory z hasha nazwy) i proste strony HTML w katalogach ad-a, ad-b, ad-c.
Układ: A = {hero, logo, badge}, B = {promo-c, promo-d, badge-b}, C = {hero, logo, badge, promo-c}.
Oczekiwany wynik: grupa {A, C} (Jaccard 3/4), B osobno (z C wspólna tylko promo-c: 1/6, poniżej progu).
"""
import hashlib
import struct
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADS = {
    "ad-a": ("A", ["hero", "logo", "badge"]),
    "ad-b": ("B", ["promo-c", "promo-d", "badge-b"]),
    "ad-c": ("C", ["hero", "logo", "badge", "promo-c"]),
}


def png(name: str, w: int = 96, h: int = 54) -> bytes:
    d = hashlib.sha256(("synthetic:" + name).encode()).digest()
    bg, fg = tuple(d[0:3]), tuple(d[3:6])
    x0, y0, x1, y1 = d[6] % (w // 2), d[7] % (h // 2), w // 2 + d[8] % (w // 2), h // 2 + d[9] % (h // 2)
    rows = []
    for y in range(h):
        row = bytearray([0])
        for x in range(w):
            inside = x0 <= x < x1 and y0 <= y < y1
            stripe = ((x + y) // (3 + d[10] % 4)) % 2 == 0
            row += bytes(fg if inside and stripe else bg)
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(t: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + t + data
        return c + struct.pack(">I", zlib.crc32(t + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main() -> None:
    for folder, (letter, assets) in ADS.items():
        d = HERE / folder
        (d / "assets").mkdir(parents=True, exist_ok=True)
        for a in assets:
            (d / "assets" / f"{a}.png").write_bytes(png(a))
        (d / "screenshot.png").write_bytes(png("screenshot-" + folder, 160, 90))
        imgs = "".join(f'<img src="assets/{a}.png" alt="{a}">' for a in assets)
        (d / "index.html").write_text(
            "<!doctype html>\n<html lang=\"pl\"><head><meta charset=\"utf-8\">"
            f"<title>Materiał demonstracyjny {letter} (syntetyczny)</title></head>\n"
            f"<body><h1>Materiał demonstracyjny {letter}</h1>"
            "<p>Treść wymyślona na potrzeby demonstracji. Zestaw jest jawnie syntetyczny i nie zawiera danych rzeczywistych.</p>"
            f"{imgs}</body></html>\n", encoding="utf-8")


if __name__ == "__main__":
    main()
