"""Porównanie układu ról (podobieństwo ról po zamaskowaniu nazw) oddzielnie od zgodności konkretnych nazw."""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Sequence

from .slots import SLOTS

WEIGHTS = {"who": 1.0, "event": 1.0, "promise": 1.0, "product": 1.0, "call": 1.0}
CLASS_SHARE = 0.5  # udział zgodności klasy ramy w wyniku pola (reszta: Jaccard zamaskowanych tokenów)
MIN_BOTH = 2       # minimum pól wypełnionych po obu stronach, by wynik nie był None


def _jac(a: Sequence[str], b: Sequence[str]) -> Optional[float]:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return None
    return len(sa & sb) / len(sa | sb)


def layout(slots: Mapping[str, Optional[Mapping[str, Any]]]) -> dict[str, Optional[tuple]]:
    """Układ ról niezależny od nazw własnych: {pole: (klasa, zamaskowane tokeny)} lub None dla pustego pola.
    Dla pola who liczy się rodzaj bohatera i zawód, nie nazwisko."""
    out: dict[str, Optional[tuple]] = {}
    for s in SLOTS:
        v = slots.get(s)
        if not v:
            out[s] = None
        elif s == "who":
            out[s] = (v.get("cls"), (v.get("extra") or {}).get("profession"))
        else:
            out[s] = (v.get("cls"), tuple(v.get("masked") or ()))
    return out


def slot_similarity(slot: str, a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
    if slot == "who":
        pa, pb = (a.get("extra") or {}).get("profession"), (b.get("extra") or {}).get("profession")
        return 0.5 * (a.get("cls") == b.get("cls")) + 0.5 * (pa == pb and pa is not None)
    j = _jac(a.get("masked") or (), b.get("masked") or ()) or 0.0
    ca, cb = a.get("cls"), b.get("cls")
    if ca is None or cb is None:
        return j
    return CLASS_SHARE * (ca == cb) + (1 - CLASS_SHARE) * j


def compare_roles(sa: Mapping[str, Optional[Mapping[str, Any]]], sb: Mapping[str, Optional[Mapping[str, Any]]],
                  weights: Mapping[str, float] = WEIGHTS) -> dict:
    """Wynik pochodny pary: role_score (po maskowaniu nazw), name_score (zgodność nazw własnych w cytatach),
    per_slot oraz frame_match (zgodność klas ramy w polach wypełnionych po obu stronach).
    Pole puste po jednej stronie liczy się jako 0, puste po obu stronach jest pomijane."""
    per, num, den, both = {}, 0.0, 0.0, 0
    for s in SLOTS:
        a, b = sa.get(s), sb.get(s)
        if not a and not b:
            per[s] = {"state": "empty_both"}
            continue
        w = weights.get(s, 1.0)
        den += w
        if not a or not b:
            per[s] = {"state": "missing_a" if not a else "missing_b", "role_sim": 0.0}
            continue
        both += 1
        sim = slot_similarity(s, a, b)
        na, nb = set(a.get("name_stems") or ()), set(b.get("name_stems") or ())
        per[s] = {"state": "both", "role_sim": sim, "cls": [a.get("cls"), b.get("cls")],
                  "name_sim": (len(na & nb) / len(na | nb)) if (na or nb) else None,
                  "names": [a.get("names"), b.get("names")]}
        num += w * sim
    role = num / den if den and both >= MIN_BOTH else None
    stems = [set(), set()]
    for k, sl in enumerate((sa, sb)):
        for v in sl.values():
            if v:
                stems[k] |= set(v.get("name_stems") or ())
    name = (len(stems[0] & stems[1]) / len(stems[0] | stems[1])) if (stems[0] or stems[1]) else None
    classes = [(s, per[s]["cls"]) for s in SLOTS if per[s].get("state") == "both" and None not in per[s]["cls"]]
    return {"role_score": role, "name_score": name, "slots_both": both, "per_slot": per,
            "frame_match": bool(classes) and all(c[0] == c[1] for _, c in classes) and both >= MIN_BOTH,
            "shared_names": sorted(stems[0] & stems[1])}


def swap_names(text: str, mapping: Mapping[str, str]) -> str:
    """Zamiana nazw własnych (dokładne formy wyrazów) bez zmiany reszty tekstu; służy do testu niezmienności układu."""
    if not mapping:
        return text
    rx = re.compile(r"(?<!\w)(" + "|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True)) + r")(?!\w)")
    return rx.sub(lambda m: mapping[m.group(1)], text)
