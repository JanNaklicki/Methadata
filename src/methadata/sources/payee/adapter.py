"""S04: deklarowany odbiorca płatności (rachunek IBAN, adres portfela z siecią, ID sprzedawcy u bramki).

Wartości wrażliwe nie są zapisywane jawnie: cecha niesie HMAC-SHA256 (klucz lokalny, z identyfikatorem klucza)
oraz maskę do UI. Porównanie następuje tylko w obrębie tej samej przestrzeni (np. iban, crypto:evm, gateway:stripe)
i tego samego klucza. Nazwa bramki sama w sobie nie jest dowodem. Nic nie jest wysyłane ani inicjowane.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Optional, Sequence

from methadata.contracts import ids
from methadata.privacy.pii import mask, pii_token
from methadata.contracts.interfaces import AdapterKind, AdapterSpec
from methadata.contracts.models import (ArtifactRef, ExtractionState, ExtractionStatus, Feature, Observation,
                                        PairEvidence, Relation, TextSpan, ValueType)

ADAPTERS_VERSION = "1.0.0"
EXTRACTOR_VERSION = f"s04-payee/{ADAPTERS_VERSION}"
FAMILY = "payee"
_TEXT_EXT = (".html", ".htm", ".json", ".har", ".txt", ".js", ".md")

# ---------------------------------------------------------------- walidacja
_IBAN = re.compile(r"\b([A-Z]{2}\d{2}(?:[  -]?[A-Z0-9]{2,4}){3,8})\b")
_PL_NRB = re.compile(r"(?<![\w])(\d{2}(?: ?\d{4}){6})(?![\w])")


def iban_valid(s: str) -> bool:
    s = re.sub(r"[  -]", "", s).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", s):
        return False
    return int("".join(str(int(c, 36)) for c in s[4:] + s[:4])) % 97 == 1


_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58check(s: str) -> Optional[bytes]:
    n = 0
    for c in s:
        i = _B58.find(c)
        if i < 0:
            return None
        n = n * 58 + i
    raw = n.to_bytes(25, "big") if n.bit_length() <= 200 else b""
    if len(raw) != 25:
        return None
    return raw if hashlib.sha256(hashlib.sha256(raw[:21]).digest()).digest()[:4] == raw[21:] else None


_BECH = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _bech32_ok(s: str) -> bool:
    s = s.lower()
    if "1" not in s:
        return False
    hrp, data = s.rsplit("1", 1)
    if hrp != "bc" or len(data) < 7 or any(c not in _BECH for c in data):
        return False
    gen = (0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3)
    chk = 1
    for v in [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp] + [_BECH.find(c) for c in data]:
        top = chk >> 25
        chk = (chk & 0x1ffffff) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if (top >> i) & 1 else 0
    return chk in (1, 0x2bc830a3)


def _crypto(text: str):
    """-> [(network, canonical, start, end)] z walidacją sumy kontrolnej (bez fałszywych trafień na losowych ciągach)."""
    for m in re.finditer(r"(?<![0-9A-Za-z])0x[0-9a-fA-F]{40}(?![0-9A-Za-z])", text):
        yield "evm", m.group(0).lower(), m.start(), m.end()
    for m in re.finditer(r"(?<![0-9A-Za-z])T[1-9A-HJ-NP-Za-km-z]{33}(?![0-9A-Za-z])", text):
        raw = _b58check(m.group(0))
        if raw and raw[0] == 0x41:
            yield "tron", m.group(0), m.start(), m.end()
    for m in re.finditer(r"(?<![0-9A-Za-z])[13][1-9A-HJ-NP-Za-km-z]{25,34}(?![0-9A-Za-z])", text):
        raw = _b58check(m.group(0))
        if raw and raw[0] in (0, 5):
            yield "btc", m.group(0), m.start(), m.end()
    for m in re.finditer(r"(?<![0-9A-Za-z])(?:bc1|BC1)[0-9a-zA-Z]{8,87}(?![0-9A-Za-z])", text):
        if _bech32_ok(m.group(0)):
            yield "btc", m.group(0).lower(), m.start(), m.end()


# bramka -> [(regex z grupą 1 = publiczny ID sprzedawcy)]
_GATEWAYS = {
    "stripe": [re.compile(r"(?<![\w])(acct_[0-9A-Za-z]{8,32})\b"), re.compile(r"\b(pk_(?:live|test)_[0-9A-Za-z]{16,})")],
    "paypal": [re.compile(r"paypal\.com/(?:paypalme|ncp/payment)/([\w.\-]{3,40})", re.I),
               re.compile(r"[?&](?:business|merchant_?id|merchantId|payee)=([A-Za-z0-9._%@\-]{5,64})", re.I)],
    "przelewy24": [re.compile(r"\bp24_merchant_id=(\d{3,10})", re.I)],
}
_GATEWAY_HOST = re.compile(r"(stripe\.com|paypal\.com|przelewy24\.pl|payu\.com|adyen\.com|tpay\.com)", re.I)


def find_payees(text: str):
    """-> [(space, canonical, display, start, end, gateway_or_None)] — deterministycznie posortowane, bez duplikatów."""
    out: dict[tuple, tuple] = {}
    for m in _IBAN.finditer(text):
        if iban_valid(m.group(1)):
            c = re.sub(r"[  -]", "", m.group(1)).upper()
            out.setdefault(("iban", c), ("iban", c, c, m.start(1), m.end(1), None))
    for m in _PL_NRB.finditer(text):
        c = "PL" + m.group(1).replace(" ", "")
        if iban_valid(c):
            out.setdefault(("iban", c), ("iban", c, c, m.start(1), m.end(1), None))
    for net, c, s, e in _crypto(text):
        out.setdefault((f"crypto:{net}", c), (f"crypto:{net}", c, c, s, e, None))
    for gw, pats in _GATEWAYS.items():
        for p in pats:
            for m in p.finditer(text):
                c = m.group(1).lower() if gw == "paypal" else m.group(1)
                out.setdefault((f"gateway:{gw}", c), (f"gateway:{gw}", c, c, m.start(1), m.end(1), gw))
    return sorted(out.values(), key=lambda t: (t[3], t[0]))


class PayeeExtractor:
    spec = AdapterSpec(id="source-payee-extractor", kind=AdapterKind.extractor, version=ADAPTERS_VERSION,
                       description="Deklarowany odbiorca płatności: IBAN, portfel z siecią, ID sprzedawcy u bramki (HMAC + maska)")

    def extract(self, observation: Observation, *, reader, optional: Mapping[str, Any] | None = None):
        oid = observation.observation_id
        st = lambda state, detail=None, art=None: ExtractionStatus(  # noqa: E731
            observation_id=oid, artifact_id=art, extractor_version=EXTRACTOR_VERSION, state=state, detail=detail)
        targets = [a for a in observation.artifacts
                   if (a.media_type or "").startswith("text/") or "json" in (a.media_type or "") or "html" in (a.media_type or "")
                   or (a.origin or a.local_ref or "").lower().endswith(_TEXT_EXT)]
        if not targets:
            return [], [st(ExtractionState.missing_input, "brak artefaktu tekstowego (instrukcja płatnicza/oferta)")]
        feats: list[Feature] = []
        sts: list[ExtractionStatus] = []
        seen: set[tuple] = set()
        for art in targets:
            try:
                text = reader.open_bytes(ArtifactRef(artifact_id=art.artifact_id)).decode("utf-8-sig", errors="replace")
            except Exception as e:
                sts.append(st(ExtractionState.missing_input, f"nie można odczytać bajtów: {e}", art.artifact_id))
                continue
            found = find_payees(text)
            gateways = sorted({m.group(1).lower() for m in _GATEWAY_HOST.finditer(text)})
            for space, canon, _disp, s, e, gw in found:
                if (space, canon) in seen:
                    continue
                seen.add((space, canon))
                tok, kid = pii_token(canon, space)
                val = {"space": space, "key_id": kid, "token": tok, "masked": mask(canon), "declared": True,
                       "retrieved_at": observation.capture_time, "status": "extracted"}
                if gw:
                    val["gateway"] = gw
                feats.append(Feature(feature_id=ids.feature_id(oid, FAMILY, "payee_account", EXTRACTOR_VERSION, val),
                                     observation_id=oid, family=FAMILY, kind="payee_account", value_type=ValueType.json,
                                     value=val, extractor_version=EXTRACTOR_VERSION,
                                     evidence_refs=[ArtifactRef(artifact_id=art.artifact_id, locator=TextSpan(start=s, end=e))]))
            for g in gateways:
                v = {"gateway": g}
                feats.append(Feature(feature_id=ids.feature_id(oid, FAMILY, "payment_gateway", EXTRACTOR_VERSION, v),
                                     observation_id=oid, family=FAMILY, kind="payment_gateway", value_type=ValueType.json,
                                     value=v, extractor_version=EXTRACTOR_VERSION,
                                     evidence_refs=[ArtifactRef(artifact_id=art.artifact_id)]))
            sts.append(st(ExtractionState.ok, None if found else "nie znaleziono deklarowanego odbiorcy płatności", art.artifact_id))
        return feats, sts


def _payees(feats: Sequence[Feature]) -> dict[tuple, list[ArtifactRef]]:
    out: dict[tuple, list[ArtifactRef]] = {}
    for f in feats:
        if f.family == FAMILY and f.kind == "payee_account" and isinstance(f.value, dict):
            out.setdefault((f.value["space"], f.value["key_id"], f.value["token"]), []).extend(f.evidence_refs)
    return out


def _masked(feats: Sequence[Feature]) -> dict[tuple, str]:
    return {(f.value["space"], f.value["key_id"], f.value["token"]): f.value["masked"] for f in feats
            if f.family == FAMILY and f.kind == "payee_account" and isinstance(f.value, dict)}


class PayeeComparator:
    method_id = "source-payee-compare"
    spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version=ADAPTERS_VERSION,
                       description="Zgodność odbiorcy płatności w obrębie przestrzeni; sama bramka nie liczy się jako dowód")

    def compare(self, a: Observation, b: Observation, features: Mapping[str, Sequence[Feature]], *, run_id: str,
                optional: Mapping[str, Any] | None = None) -> Sequence[PairEvidence]:
        (ca, oa), (cb, ob) = sorted([(a.canonical_id, a), (b.canonical_id, b)], key=lambda t: t[0])
        fa, fb = features.get(oa.observation_id, []), features.get(ob.observation_id, [])
        pair = (ca, cb)

        def make(score, why, refs=None):
            return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=Relation.campaign_candidate,
                                 score=score, evidence_refs=refs or {}, explanation=why)]

        pa, pb = _payees(fa), _payees(fb)
        if not pa or not pb:
            return make(None, "brak podstaw: po co najmniej jednej stronie nie wykryto deklarowanego odbiorcy płatności")
        shared = set(pa) & set(pb)
        if shared:
            k = sorted(shared)[0]
            m = _masked(fa).get(k, "?")
            refs = {ca: [r for x in shared for r in pa[x]], cb: [r for x in shared for r in pb[x]]}
            return make(1.0, f"ten sam odbiorca płatności w przestrzeni {k[0]} ({m})", refs)
        # porównywalne: ta sama przestrzeń i ten sam klucz HMAC
        comparable = {(s, kid) for s, kid, _ in pa} & {(s, kid) for s, kid, _ in pb}
        if comparable:
            s = sorted(comparable)[0][0]
            gw = s.startswith("gateway:")
            return make(0.0, f"różni odbiorcy w przestrzeni {s}" + (" (ta sama bramka, różni sprzedawcy: sama bramka nie jest dowodem)" if gw else ""),
                        {ca: [r for v in pa.values() for r in v], cb: [r for v in pb.values() for r in v]})
        return make(None, "brak podstaw: odbiorcy w różnych przestrzeniach lub zapisani innym kluczem HMAC (nieporównywalne)")


def group_by_payee(features: Mapping[str, Sequence[Feature]]) -> dict[tuple, list[str]]:
    groups: dict[tuple, list[str]] = {}
    for oid, feats in features.items():
        for k in _payees(feats):
            groups.setdefault(k, []).append(oid)
    return {k: sorted(set(v)) for k, v in groups.items()}
