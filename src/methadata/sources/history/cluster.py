"""Klaster historyczny: odtwarzany wyłącznie z wersji widocznych i znanych na dany dzień."""
from __future__ import annotations

from typing import Optional

from .timeline import History

_SIGNALS = ("contact", "gtin", "offer_id", "sku", "cta")


def signals_of(snap: dict, generic_hosts=()) -> set[tuple[str, str]]:
    out = {("contact", f"{c['kind']}:{c['key']}") for c in snap.get("contacts", [])}
    for k, vs in snap.get("offer", {}).get("ids", {}).items():
        if k in ("gtin", "offer_id"):  # sku bez wystawcy i seller_id nie są dowodem wspólnego produktu
            out |= {(k, v) for v in vs}
    cta = snap.get("cta") or {}
    if cta.get("href") and cta.get("host") not in generic_hosts:
        out.add(("cta", cta["href"]))
    return out


def cluster_as_of(history: History, day: str, *, generic_hosts=()) -> list[list[str]]:
    """Klastry URL-i łączonych wspólnym sygnałem (kontakt, GTIN, ID oferty, cel CTA) w stanie z `day`.
    Zwraca posortowane listy (singletony pomijane). Powtarzalne: nowsze lub spóźnione wersje nie wpływają na wynik."""
    snaps = history.snapshot_as_of(day)
    parent = {u: u for u in snaps}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    owner: dict[tuple, str] = {}
    for u in sorted(snaps):
        for s in sorted(signals_of(snaps[u].snapshot, generic_hosts)):
            if s in owner:
                parent[find(u)] = find(owner[s])
            else:
                owner[s] = u
    groups: dict[str, list[str]] = {}
    for u in snaps:
        groups.setdefault(find(u), []).append(u)
    return sorted(sorted(g) for g in groups.values() if len(g) > 1)
