"""Podział na część rozwojową i odłożone rodziny oraz test nowego członka grupy (§6.3)."""
from __future__ import annotations

import hashlib
import math
from typing import Callable, Iterable, Mapping, Optional

from .labels import Pair


def split_families(families: Mapping[str, str], *, seed: int = 0, heldout_fraction: float = 0.3) -> dict[str, str]:
    """families: material_id -> rodzina (kopie i warianty jednego źródła mają wspólną rodzinę).
    Zwraca material_id -> 'dev' | 'heldout'. Całe rodziny trafiają do jednej części; wynik nie zależy od kolejności."""
    fams = sorted(set(families.values()), key=lambda f: hashlib.sha256(f"{seed}\x1f{f}".encode()).hexdigest())
    k = min(len(fams), math.ceil(heldout_fraction * len(fams))) if len(fams) > 1 else 0
    held = set(fams[:k])
    return {m: ("heldout" if f in held else "dev") for m, f in sorted(families.items())}


def pairs_in_split(pairs: Iterable[Pair], split: Mapping[str, str], part: str) -> set[Pair]:
    """Pary z obu materiałami w danej części; pary przekraczające granicę nie należą do żadnej."""
    return {p for p in pairs if split.get(p[0]) == part and split.get(p[1]) == part}


def newcomer_top_k(query: str, candidates: Iterable[str], families: Mapping[str, str],
                   score: Callable[[str, str], Optional[float]], k: int = 3) -> dict:
    """Test nowego członka: `query` jest zapytaniem, reszta to indeks (także jego znani krewni).
    Trafienie, gdy wśród k najwyższych wyników jest materiał tej samej rodziny.
    Wynik None (brak oceny) nie trafia do rankingu. hit=None, gdy w indeksie nie ma krewnych."""
    index = sorted(c for c in candidates if c != query)
    relatives = [c for c in index if families.get(c) == families.get(query)]
    ranked = sorted(((s, c) for c in index if (s := score(query, c)) is not None), key=lambda t: (-t[0], t[1]))
    top = [c for _, c in ranked[:k]]
    return {"query": query, "k": k, "top": top, "relatives_in_index": len(relatives),
            "hit": (any(c in relatives for c in top) if relatives else None)}
