"""Różnice oferty, kontaktu i celu CTA między dwiema wersjami strony."""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional

from .analyze import normalize_url  # noqa: F401  (re-export wygody)


def _tok(s: Optional[str]) -> set[str]:
    return set(re.findall(r"\w{3,}", (s or "").casefold()))


def _sets(a: set, b: set) -> dict:
    return {"added": sorted(b - a), "removed": sorted(a - b), "kept": sorted(a & b)}


def _offer_ids(snap: Mapping) -> set[str]:
    return {f"{k}:{v}" for k, vs in snap.get("offer", {}).get("ids", {}).items() for v in vs if k != "seller_id"}


def _contact_keys(snap: Mapping) -> set[str]:
    return {f"{c['kind']}:{c['key']}" for c in snap.get("contacts", [])}


def diff_snapshots(a: Mapping[str, Any], b: Mapping[str, Any], cfg: Mapping[str, Any]) -> dict:
    """a - wersja wcześniejsza, b - późniejsza. Wynik:
    status: ok | no_data (któraś strona bez sygnałów) ; labels: podzbiór {recipient_change, product_swap, cta_change};
    unchanged: True tylko, gdy wszystkie obserwowane sygnały są identyczne i istnieją."""
    if a.get("status") == "no_data" or b.get("status") == "no_data":
        return {"status": "no_data", "labels": [], "unchanged": None,
                "reason": "co najmniej jedna wersja bez rozpoznanych sygnałów (oferta/kontakt/CTA)"}
    oa, ob = _offer_ids(a), _offer_ids(b)
    ca, cb = _contact_keys(a), _contact_keys(b)
    offer = _sets(oa, ob)
    contact = _sets(ca, cb)
    ta, tb = a.get("offer", {}).get("title"), b.get("offer", {}).get("title")
    ja = len(_tok(ta) & _tok(tb)) / max(1, len(_tok(ta) | _tok(tb))) if ta and tb else None
    offer.update(title={"from": ta, "to": tb, "jaccard": None if ja is None else round(ja, 3)},
                 price={"from": a.get("offer", {}).get("price"), "to": b.get("offer", {}).get("price")})
    xa, xb = a.get("cta") or {}, b.get("cta") or {}
    cta = {"from": xa or None, "to": xb or None,
           "target_changed": bool(xa and xb and (xa.get("href") != xb.get("href"))),
           "host_changed": bool(xa and xb and xa.get("host") != xb.get("host")),
           "text_changed": bool(xa and xb and xa.get("text") != xb.get("text"))}
    labels = []
    # zmiana odbiorcy: kontakt (cały zbiór rozłączny, oba niepuste) lub zmiana hosta celu CTA
    if (ca and cb and not (ca & cb)) or cta["host_changed"]:
        labels.append("recipient_change")
    # podmiana produktu: identyfikatory oferty rozłączne (oba niepuste) albo, bez identyfikatorów, tytuł wyraźnie inny
    if (oa and ob and not (oa & ob)) or (not oa and not ob and ja is not None and ja < cfg["title_swap_jaccard"]):
        labels.append("product_swap")
    if cta["target_changed"] and "recipient_change" not in labels:
        labels.append("cta_change")
    same = not (offer["added"] or offer["removed"] or contact["added"] or contact["removed"] or cta["target_changed"]
                or cta["text_changed"] or (ja is not None and ja < 1.0)
                or offer["price"]["from"] != offer["price"]["to"])
    return {"status": "ok", "labels": labels, "unchanged": same, "offer": offer, "contact": contact, "cta": cta}
