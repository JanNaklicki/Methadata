"""Generator jawnie syntetycznego zestawu demo (bez żadnych danych rzeczywistych).

  python backend/methadata_api/demo_data/generate_demo.py

Tworzy deterministyczne, maleńkie obrazy PNG (wzory z hasha nazwy) i proste strony HTML w katalogach ad-a, ad-b, ad-c.
Układ: A = {hero, logo, badge}, B = {promo-c, promo-d, badge-b}, C = {hero, logo, badge, promo-c}.
Oczekiwany wynik: grupa {A, C} (grafiki Jaccard 3/4, to samo hasło i układ), B osobno (z C wspólna tylko promo-c: 1/6; inny temat tekstu i inny szablon HTML).
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


# Treść i szablon dobrane tak, by wszystkie rodziny cech (grafiki, tekst, struktura) zgadzały się z oczekiwanym wynikiem:
# A i C to warianty jednej kampanii (to samo hasło i układ, inna końcówka), B to inna kampania (inny temat i inny szablon).
COPY = {
    "A": "Syntetyczna oferta Alfa: zamów teraz zestaw kuracyjny w promocyjnej cenie. Liczba sztuk ograniczona, rabat obowiązuje do końca tygodnia. Tekst wymyślony na potrzeby demonstracji.",
    "C": "Syntetyczna oferta Alfa: zamów teraz zestaw kuracyjny w promocyjnej cenie. Liczba sztuk ograniczona, rabat obowiązuje do końca miesiąca. Tekst wymyślony na potrzeby demonstracji.",
    "B": "Fikcyjna pożyczka Beta: wypełnij wniosek przez internet i poznaj decyzję w kilka minut. Bez zaświadczeń i wizyty w oddziale. Dane wymyślone na potrzeby demonstracji.",
}


def page(letter: str, assets: list[str]) -> str:
    imgs = "".join(f'<img src="assets/{a}.png" alt="{a}">' for a in assets)
    head = ("<!doctype html>\n<html lang=\"pl\"><head><meta charset=\"utf-8\">"
            f"<title>Materiał demonstracyjny {letter} (syntetyczny)</title></head>\n")
    if letter == "B":
        return (head + "<body><main><section class=\"wniosek\"><h2>Wniosek online</h2>"
                f"<ol><li>{COPY[letter]}</li><li>Dane wymyślone, bez znaczenia rzeczywistego.</li></ol>"
                f"<aside>{imgs}</aside></section><footer><small>Zestaw jawnie syntetyczny</small></footer></main></body></html>\n")
    return (head + f"<body><div class=\"oferta\"><h1>Materiał demonstracyjny {letter}</h1><p>{COPY[letter]}</p>"
            f"<div class=\"galeria\">{imgs}</div></div></body></html>\n")


def main() -> None:
    for folder, (letter, assets) in ADS.items():
        d = HERE / folder
        (d / "assets").mkdir(parents=True, exist_ok=True)
        for a in assets:
            (d / "assets" / f"{a}.png").write_bytes(png(a))
        (d / "screenshot.png").write_bytes(png("screenshot-" + folder, 160, 90))
        (d / "index.html").write_text(page(letter, assets), encoding="utf-8")


if __name__ == "__main__":
    main()
