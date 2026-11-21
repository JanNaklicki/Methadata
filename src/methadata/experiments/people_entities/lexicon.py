"""Słownik aliasów (osoby, marki, produkty) i dopasowanie nazw do tekstu (HTML, OCR).

Dopasowanie działa na tekście „złożonym” (bez diakrytyków, ł->l), o tej samej długości co oryginał, więc
przesunięcia znaków dotyczą tekstu źródłowego. Odmianę polską obsługuje reguła `inflect`: do 3 liter po rdzeniu
(ostatnia samogłoska a/o/e/y słowa jest odrzucana)."""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional, Union

DATA = Path(__file__).parent / "data"
ENTITY_TYPES = ("person", "brand", "product")
_PL = {"ł": "l", "Ł": "L", "ø": "o", "đ": "d"}


def fold(text: str) -> str:
    """Bez diakrytyków; ZAWSZE ta sama długość co wejście (znak bez odpowiednika ASCII zostaje)."""
    out = []
    for ch in text:
        if ch in _PL:
            out.append(_PL[ch])
            continue
        d = unicodedata.normalize("NFD", ch)
        base = "".join(c for c in d if not unicodedata.combining(c))
        out.append(base if len(base) == 1 else ch)
    return "".join(out)


@dataclass(frozen=True)
class Hit:
    entity_id: str
    entity_type: str
    canonical: str
    surface: str
    start: int
    end: int


@dataclass(frozen=True)
class Entity:
    entity_id: str
    type: str
    canonical: str
    aliases: tuple


def _word_pattern(word: str, inflect: bool) -> str:
    w = fold(word)
    if inflect and len(w) >= 4:
        if w[-1].lower() in "aoey":
            w = w[:-1]
        return re.escape(w) + r"\w{0,3}"
    return re.escape(w)


class Lexicon:
    def __init__(self, entities: Iterable[dict], version: str = "custom"):
        self.version = version
        self.entities: dict[str, Entity] = {}
        self._pats: list[tuple[re.Pattern, Entity]] = []
        for e in entities:
            if e["type"] not in ENTITY_TYPES:
                raise ValueError(f"nieznany typ encji: {e['type']}")
            aliases = [a if isinstance(a, dict) else {"text": a} for a in e["aliases"]]
            ent = Entity(e["entity_id"], e["type"], e["canonical"], tuple(a["text"] for a in aliases))
            if ent.entity_id in self.entities:
                raise ValueError(f"zduplikowane entity_id: {ent.entity_id}")
            self.entities[ent.entity_id] = ent
            seen = set()
            for a in aliases:
                if a["text"] in seen:
                    continue
                seen.add(a["text"])
                infl = a.get("inflect", e.get("inflect", False))
                words = a["text"].split()
                body = r"\s+".join(_word_pattern(w, infl) for w in words)
                flags = 0 if a.get("case_sensitive") else re.I
                self._pats.append((re.compile(rf"(?<!\w){body}(?!\w)", flags), ent))

    @classmethod
    def from_file(cls, path: Union[str, Path, None] = None) -> "Lexicon":
        p = Path(path) if path else DATA / "lexicon.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        return cls(d["entities"], d.get("version", p.name))

    def find(self, text: str) -> list[Hit]:
        """Trafienia w kolejności pozycji; przy nakładaniu wygrywa dłuższe (np. „Rafał Brzoska” nad „Brzoska”)."""
        folded = fold(text)
        cands = []
        for pat, ent in self._pats:
            for m in pat.finditer(folded):
                cands.append((m.start(), m.end(), ent))
        cands.sort(key=lambda c: (-(c[1] - c[0]), c[0], c[2].entity_id))
        taken: list[tuple[int, int, Entity]] = []
        for s, e, ent in cands:
            if all(e <= ts or s >= te for ts, te, _ in taken):
                taken.append((s, e, ent))
        taken.sort(key=lambda c: (c[0], c[1]))
        return [Hit(ent.entity_id, ent.type, ent.canonical, text[s:e], s, e) for s, e, ent in taken]


@lru_cache(maxsize=1)
def default_lexicon() -> Lexicon:
    return Lexicon.from_file()


@dataclass(frozen=True)
class ReferenceImage:
    sha256: str
    entity_id: str
    label_source: str
    verified: bool = False
    described_by: str = ""


def load_references(path: Union[str, Path, None] = None) -> dict[str, ReferenceImage]:
    p = Path(path) if path else DATA / "reference_images.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    out: dict[str, ReferenceImage] = {}
    for i in d["images"]:
        if not i.get("label_source"):
            raise ValueError("obraz referencyjny bez źródła etykiety (label_source)")
        out[i["sha256"]] = ReferenceImage(i["sha256"], i["entity_id"], i["label_source"],
                                          bool(i.get("verified", False)), i.get("described_by", ""))
    return out
