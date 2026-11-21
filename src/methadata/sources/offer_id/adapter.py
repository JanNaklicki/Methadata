"""S10: GTIN, lokalny SKU (z wystawcą), ID oferty i ID sprzedawcy jako odrębne rodzaje danych."""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import urlparse

from methadata.contracts import ids
from methadata.contracts.interfaces import AdapterKind, AdapterSpec
from methadata.contracts.models import (ArtifactRef, ArtifactRole, ExtractionState, ExtractionStatus, Feature,
                                        Observation, PairEvidence, Relation, ValueType)

from .gtin import normalize_gtin
from .scan import Hit, load_config, market_of, scan_html, scan_text

ADAPTERS_VERSION = "1.0.0"
EXTRACTOR_VERSION = f"s10-offer-id/{ADAPTERS_VERSION}"
FAMILY = "offer_id"
KINDS = ("gtin", "sku", "offer_id", "seller_id")


def _norm(s: Optional[str]) -> str:
    return re.sub(r"[^\w]+", " ", (s or "").casefold()).strip()


def _is_html(a) -> bool:
    mt = (a.media_type or "").lower()
    ref = (a.origin or a.local_ref or "").lower()
    return a.role in (ArtifactRole.landing_html, ArtifactRole.landing_dom) or "html" in mt or ref.endswith((".html", ".htm", ".mhtml"))


def _is_text(a) -> bool:
    mt = (a.media_type or "").lower()
    ref = (a.origin or a.local_ref or "").lower()
    return a.role in (ArtifactRole.document, ArtifactRole.other) and (mt.startswith("text/plain") or ref.endswith(".txt"))


class OfferIdExtractor:
    """Wejścia: HTML (JSON-LD, microdata, meta, URL, etykiety) oraz tekst OCR (text/plain lub optional['ocr_texts'])."""
    spec = AdapterSpec(id="source-offer-id-extractor", kind=AdapterKind.extractor, version=ADAPTERS_VERSION,
                       description="GTIN (z walidacją), lokalny SKU z wystawcą, ID oferty i ID sprzedawcy z HTML/OCR")

    def __init__(self, config: Optional[Mapping[str, Any]] = None):
        self.cfg = dict(config) if config else load_config()

    def extract(self, observation: Observation, *, reader, optional: Mapping[str, Any] | None = None):
        oid = observation.observation_id
        st = lambda state, detail=None, art=None: ExtractionStatus(  # noqa: E731
            observation_id=oid, artifact_id=art, extractor_version=EXTRACTOR_VERSION, state=state, detail=detail)
        ocr = dict((optional or {}).get("ocr_texts") or {})
        jobs = [(a, "html") for a in observation.artifacts if _is_html(a)]
        jobs += [(a, "ocr") for a in observation.artifacts if not _is_html(a) and (_is_text(a) or a.artifact_id in ocr)]
        if not jobs:
            return [], [st(ExtractionState.missing_input, "brak artefaktu HTML ani tekstu OCR")]
        src_url = observation.source_ref if "://" in observation.source_ref else ""
        feats: list[Feature] = []
        sts: list[ExtractionStatus] = []
        seen: set[tuple] = set()
        for art, source in jobs:
            try:
                raw = ocr[art.artifact_id] if art.artifact_id in ocr else \
                    reader.open_bytes(ArtifactRef(artifact_id=art.artifact_id)).decode("utf-8", errors="replace")
            except Exception as e:  # noqa: BLE001
                sts.append(st(ExtractionState.missing_input, f"nie można odczytać bajtów: {type(e).__name__}", art.artifact_id))
                continue
            if not raw.strip():
                sts.append(st(ExtractionState.ok, "pusty tekst; brak podstaw", art.artifact_id))
                continue
            hits = scan_html(raw, src_url, self.cfg) if source == "html" else scan_text(raw, "ocr")
            n0 = len(feats)
            mk = market_of(src_url, self.cfg) if src_url else None
            seller = next((h.value for h in hits if h.kind == "seller_id"), None)
            for h in hits:
                f = self._feature(observation, art, h, mk, seller, seen)
                if f:
                    feats.append(f)
            sts.append(st(ExtractionState.ok, None if len(feats) > n0 else "nie znaleziono GTIN/SKU/ID oferty", art.artifact_id))
        return feats, sts

    def _feature(self, obs: Observation, art, h: Hit, page_market, seller, seen) -> Optional[Feature]:
        status, value = "ok", {}
        if h.kind == "gtin":
            g, valid = normalize_gtin(h.value)
            if g is None:
                return None
            value = {"gtin14": g, "valid": valid}
            status = "ok" if valid else "invalid_checksum"
            ident = g
        elif h.kind == "sku":
            sk = h.value.strip()
            sel = h.seller or seller
            value = {"sku": sk, "issuer": sel, "issuer_key": _norm(sel) or None}
            if not sel:
                status = "issuer_unknown"
            ident = (_norm(sk), _norm(sel))
        else:
            mk = h.marketplace or page_market
            value = {"id": h.value.strip(), "marketplace": mk}
            if h.kind == "offer_id" and not mk:
                status = "marketplace_unknown"
            ident = (mk, _norm(h.value) if h.kind == "seller_id" else h.value.strip())
        if (h.kind, ident) in seen:
            return None
        seen.add((h.kind, ident))
        value.update({"raw": h.raw, "source": h.source, "captured_at": obs.capture_time, "status": status,
                      "provenance": {"extractor": EXTRACTOR_VERSION, "artifact_id": art.artifact_id}})
        return Feature(feature_id=ids.feature_id(obs.observation_id, FAMILY, h.kind, EXTRACTOR_VERSION, str(ident)),
                       observation_id=obs.observation_id, family=FAMILY, kind=h.kind, value_type=ValueType.json,
                       value=value, extractor_version=EXTRACTOR_VERSION,
                       evidence_refs=[ArtifactRef(artifact_id=art.artifact_id)])


# ---------------------------------------------------------------------------- porównanie

def _by_kind(feats: Sequence[Feature], kind: str) -> list[Feature]:
    return [f for f in feats if f.family == FAMILY and f.kind == kind and isinstance(f.value, dict)]


class OfferIdComparator:
    method_id = "source-offer-id-compare"
    spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version=ADAPTERS_VERSION,
                       description="Ten sam produkt (GTIN), ta sama oferta (ID), SKU u różnych sprzedawców jako mylące dopasowanie")

    def __init__(self, config: Optional[Mapping[str, Any]] = None):
        self.cfg = dict(config) if config else load_config()

    def compare(self, a: Observation, b: Observation, features: Mapping[str, Sequence[Feature]], *, run_id: str,
                optional: Mapping[str, Any] | None = None) -> Sequence[PairEvidence]:
        (ca, oa), (cb, ob) = sorted([(a.canonical_id, a), (b.canonical_id, b)], key=lambda t: t[0])
        fa, fb = features.get(oa.observation_id, []), features.get(ob.observation_id, [])
        sc = self.cfg["scores"]
        pair = (ca, cb)

        def refs(*fs):
            return {ca: [r for f in fs[0] for r in f.evidence_refs], cb: [r for f in fs[1] for r in f.evidence_refs]}

        def make(score, why, r=None):
            return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=Relation.campaign_candidate,
                                 score=score, evidence_refs=r or {}, explanation=why)]

        if not any(_by_kind(f, k) for f in (fa, fb) for k in KINDS) or \
                not any(_by_kind(fa, k) for k in KINDS) or not any(_by_kind(fb, k) for k in KINDS):
            return make(None, "brak podstaw: co najmniej jedna strona nie ujawnia GTIN, SKU ani ID oferty "
                              "(brak identyfikatora to nie dowód różnych produktów)")
        cands: list[tuple[float, str, tuple]] = []
        # ta sama oferta: (marketplace, id)
        oa_ = {(f.value["marketplace"], f.value["id"]): f for f in _by_kind(fa, "offer_id") if f.value.get("marketplace")}
        ob_ = {(f.value["marketplace"], f.value["id"]): f for f in _by_kind(fb, "offer_id") if f.value.get("marketplace")}
        for k in sorted(set(oa_) & set(ob_)):
            cands.append((sc["same_offer"], f"to samo ID oferty {k[1]} ({k[0]}): kilka reklam jednej konkretnej oferty", (oa_[k], ob_[k])))
        # GTIN poprawne
        ga = {f.value["gtin14"]: f for f in _by_kind(fa, "gtin") if f.value["valid"]}
        gb = {f.value["gtin14"]: f for f in _by_kind(fb, "gtin") if f.value["valid"]}
        for g in sorted(set(ga) & set(gb)):
            cands.append((sc["same_gtin"], f"ten sam GTIN {g} (cyfra kontrolna poprawna): ten sam produkt, niekoniecznie ta sama oferta",
                          (ga[g], gb[g])))
        # SKU
        for f1 in _by_kind(fa, "sku"):
            s1 = f1.value["sku"].casefold()
            for f2 in _by_kind(fb, "sku"):
                if s1 != f2.value["sku"].casefold():
                    continue
                i1, i2 = f1.value.get("issuer_key"), f2.value.get("issuer_key")
                if i1 and i2 and i1 == i2:
                    cands.append((sc["same_sku_same_seller"], f"ten sam SKU {f1.value['sku']} u tego samego wystawcy ({f1.value['issuer']})", (f1, f2)))
                elif i1 and i2:
                    cands.append((sc["same_sku_other_sellers"], f"mylące dopasowanie: ten sam lokalny SKU {f1.value['sku']} u różnych "
                                  f"sprzedawców ({f1.value['issuer']} / {f2.value['issuer']}); SKU nie jest identyfikatorem globalnym", (f1, f2)))
                else:
                    cands.append((sc["same_sku_unknown_seller"], f"ten sam SKU {f1.value['sku']}, ale wystawca nieznany: "
                                  "nie wiadomo, czy to ten sam produkt", (f1, f2)))
        if cands:
            best = max(cands, key=lambda c: c[0])
            extra = f"; łącznie zbieżności: {len(cands)}" if len(cands) > 1 else ""
            return make(best[0], best[1] + extra, refs([best[2][0]], [best[2][1]]))
        if ga and gb:
            return make(sc["different_gtin"], "różne poprawne GTIN: różne produkty", refs(list(ga.values()), list(gb.values())))
        return make(sc["no_shared_id"], "brak wspólnego GTIN, ID oferty ani SKU",
                    refs([f for k in KINDS for f in _by_kind(fa, k)], [f for k in KINDS for f in _by_kind(fb, k)]))
