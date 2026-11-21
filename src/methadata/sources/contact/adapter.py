"""S03: publiczny kontakt handlowy (telefon, e-mail, konto komunikatora) z ogłoszenia/stopki/CTA (HTML i OCR)."""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import urlparse

from methadata.contracts import ids
from methadata.contracts.interfaces import AdapterKind, AdapterSpec
from methadata.contracts.models import (ArtifactRef, ArtifactRole, ExtractionState, ExtractionStatus, Feature,
                                        Observation, PairEvidence, Relation, TextSpan, ValueType)

from . import normalize as N
from .scan import Hit, load_config, parse_html, scan

ADAPTERS_VERSION = "1.0.0"
EXTRACTOR_VERSION = f"s03-contact/{ADAPTERS_VERSION}"
FAMILY = "contact"
KINDS = {"phone": "contact_phone", "email": "contact_email", "messenger": "contact_messenger"}
_KIND_OF = {v: k for k, v in KINDS.items()}


def norm_brand(name: Optional[str]) -> str:
    return re.sub(r"[^\w]+", " ", (name or "").casefold()).strip()


def _is_html(a) -> bool:
    mt = (a.media_type or "").lower()
    ref = (a.origin or a.local_ref or "").lower()
    return a.role in (ArtifactRole.landing_html, ArtifactRole.landing_dom) or "html" in mt or ref.endswith((".html", ".htm", ".mhtml"))


def _is_text(a) -> bool:
    mt = (a.media_type or "").lower()
    ref = (a.origin or a.local_ref or "").lower()
    return a.role in (ArtifactRole.document, ArtifactRole.other) and (mt.startswith("text/plain") or ref.endswith(".txt"))


class ContactExtractor:
    """Wejścia: artefakty HTML, tekst OCR (artefakt text/plain lub optional['ocr_texts'] {artifact_id: tekst})."""
    spec = AdapterSpec(id="source-contact-extractor", kind=AdapterKind.extractor, version=ADAPTERS_VERSION,
                       description="Kontakt handlowy: telefon (E.164), e-mail, konto komunikatora, rola; wartości maskowane")

    def __init__(self, config: Optional[Mapping[str, Any]] = None, key: "bytes | str | None" = None, key_dir=None):
        self.cfg = dict(config) if config else load_config()
        self._key_arg, self._key_dir, self._k = key, key_dir, None

    @property
    def pii_key(self) -> bytes:
        if self._k is None:
            self._k = N.resolve_key(self._key_arg, self._key_dir)
        return self._k

    def extract(self, observation: Observation, *, reader, optional: Mapping[str, Any] | None = None):
        oid = observation.observation_id
        st = lambda state, detail=None, art=None: ExtractionStatus(  # noqa: E731
            observation_id=oid, artifact_id=art, extractor_version=EXTRACTOR_VERSION, state=state, detail=detail)
        ocr_texts = dict((optional or {}).get("ocr_texts") or {})
        jobs = [(a, "html") for a in observation.artifacts if _is_html(a)]
        jobs += [(a, "ocr") for a in observation.artifacts if not _is_html(a) and (_is_text(a) or a.artifact_id in ocr_texts)]
        if not jobs:
            return [], [st(ExtractionState.missing_input, "brak artefaktu HTML ani tekstu OCR")]
        feats: list[Feature] = []
        sts: list[ExtractionStatus] = []
        brand = None
        for art, source in jobs:
            try:
                raw = ocr_texts[art.artifact_id] if art.artifact_id in ocr_texts else \
                    reader.open_bytes(ArtifactRef(artifact_id=art.artifact_id)).decode("utf-8", errors="replace")
            except Exception as e:  # noqa: BLE001
                sts.append(st(ExtractionState.missing_input, f"nie można odczytać bajtów: {type(e).__name__}", art.artifact_id))
                continue
            if not raw.strip():
                sts.append(st(ExtractionState.ok, "pusty tekst; brak podstaw", art.artifact_id))
                continue
            if source == "html":
                page = parse_html(raw)
                brand = brand or page.brand
                hits = scan(page, self.cfg, "html")
            else:
                hits = scan(raw, self.cfg, "ocr")
            seen = {(f.kind, f.value["key"]) for f in feats if isinstance(f.value, dict) and "key" in f.value}
            for h in hits:
                if (KINDS[h.kind], self._key(h)) in seen:
                    continue
                feats.append(self._feature(observation, art, raw, h))
            sts.append(st(ExtractionState.ok, None if hits else "nie znaleziono publicznego kontaktu", art.artifact_id))
        b = brand or (urlparse(observation.source_ref).hostname if "://" in observation.source_ref else None)
        if b and feats:
            feats.append(Feature(feature_id=ids.feature_id(oid, FAMILY, "contact_brand", EXTRACTOR_VERSION, b),
                                 observation_id=oid, family=FAMILY, kind="contact_brand", value_type=ValueType.string,
                                 value=b.strip(), extractor_version=EXTRACTOR_VERSION,
                                 evidence_refs=[ArtifactRef(artifact_id=jobs[0][0].artifact_id)]))
        return feats, sts

    def _key(self, h: Hit) -> str:
        return N.digest(h.kind, h.key, self.pii_key)

    def _feature(self, obs: Observation, art, raw: str, h: Hit) -> Feature:
        i = raw.find(h.raw)
        loc = TextSpan(start=i, end=i + len(h.raw)) if i >= 0 and h.raw else None
        value = {"key": self._key(h), "key_id": N.key_id(self.pii_key), "masked": h.masked, "role": h.role, "country": h.country, "platform": h.platform,
                 "source": h.source, "captured_at": obs.capture_time, "free_mail": h.free_mail,
                 "status": "low_confidence" if h.low_confidence else "ok",
                 "provenance": {"extractor": EXTRACTOR_VERSION, "artifact_id": art.artifact_id}}
        kind = KINDS[h.kind]
        return Feature(feature_id=ids.feature_id(obs.observation_id, FAMILY, kind, EXTRACTOR_VERSION, value["key"]),
                       observation_id=obs.observation_id, family=FAMILY, kind=kind, value_type=ValueType.json,
                       value=value, extractor_version=EXTRACTOR_VERSION,
                       evidence_refs=[ArtifactRef(artifact_id=art.artifact_id, locator=loc)])


# ---------------------------------------------------------------------------- porównanie

def _contacts(feats: Sequence[Feature]) -> dict[tuple[str, str], Feature]:
    out: dict[tuple[str, str], Feature] = {}
    for f in feats:
        if f.family == FAMILY and f.kind in _KIND_OF and isinstance(f.value, dict):
            out[(f.kind, f.value["key"])] = f
    return out


def _brand(feats: Sequence[Feature], fallback: str) -> str:
    for f in feats:
        if f.family == FAMILY and f.kind == "contact_brand" and isinstance(f.value, str):
            return norm_brand(f.value)
    return norm_brand(fallback)


class ContactComparator:
    method_id = "source-contact-compare"
    spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version=ADAPTERS_VERSION,
                       description="Wspólny kontakt handlowy pod różnymi markami; wspólna infolinia pośrednika obniżona")

    def __init__(self, config: Optional[Mapping[str, Any]] = None):
        self.cfg = dict(config) if config else load_config()

    def _score(self, fa: Feature, fb: Feature, same_brand: bool, intermediary: bool) -> tuple[float, str]:
        sc = self.cfg["scores"]
        roles = {fa.value["role"], fb.value["role"]}
        what = {"contact_phone": "telefon", "contact_email": "e-mail", "contact_messenger": "konto komunikatora"}[fa.kind]
        masked = fa.value["masked"]
        if intermediary or ("hotline" in roles and "order" not in roles):
            return sc["shared_hotline"], (f"wspólna infolinia/pośrednik ({what} {masked}): mylące dopasowanie, "
                                          "linia obsługuje wielu reklamodawców")
        if fa.kind == "contact_messenger":
            s = sc["messenger"]
        else:
            s = sc["same_brand"] if same_brand else sc["order_distinct_brands"]
            if "order" not in roles:
                s = round(s * 0.7, 3)
        if fa.value.get("free_mail") or fb.value.get("free_mail"):
            s = round(s * sc["free_mail_factor"], 3)
        why = f"ten sam {what} ({masked}, rola {fa.value['role']}) " + \
              ("pod tą samą marką" if same_brand else "pod różnymi markami")
        return s, why

    def compare(self, a: Observation, b: Observation, features: Mapping[str, Sequence[Feature]], *, run_id: str,
                optional: Mapping[str, Any] | None = None) -> Sequence[PairEvidence]:
        (ca, oa), (cb, ob) = sorted([(a.canonical_id, a), (b.canonical_id, b)], key=lambda t: t[0])
        fa, fb = features.get(oa.observation_id, []), features.get(ob.observation_id, [])
        pair = (ca, cb)

        def make(score, why, refs=None):
            return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=Relation.campaign_candidate,
                                 score=score, evidence_refs=refs or {}, explanation=why)]

        A, B = _contacts(fa), _contacts(fb)
        if not A or not B:
            return make(None, "brak podstaw: co najmniej jedna strona nie ujawnia publicznego kontaktu "
                              "(brak kontaktu to nie dowód różnych podmiotów)")
        ka = {f.value.get("key_id") for f in A.values()}
        kb = {f.value.get("key_id") for f in B.values()}
        if len(ka | kb) > 1:
            return make(None, "brak podstaw: cechy zbudowane różnymi kluczami PII (key_id) nie są porównywalne")
        shared = sorted(set(A) & set(B))
        if not shared:
            refs = {ca: [r for f in A.values() for r in f.evidence_refs], cb: [r for f in B.values() for r in f.evidence_refs]}
            return make(0.0, "brak wspólnego kontaktu (telefon, e-mail, komunikator)", refs)
        same_brand = _brand(fa, oa.source_ref) == _brand(fb, ob.source_ref)
        known = set(self.cfg.get("known_intermediary_keys", []))
        best: Optional[tuple[float, str, tuple]] = None
        for k in shared:
            s, why = self._score(A[k], B[k], same_brand, k[1] in known)
            if best is None or s > best[0]:
                best = (s, why, k)
        k = best[2]
        refs = {ca: A[k].evidence_refs, cb: B[k].evidence_refs}
        extra = f"; wspólnych kontaktów: {len(shared)}" if len(shared) > 1 else ""
        return make(best[0], best[1] + extra, refs)


def group_by_contact(features: Mapping[str, Sequence[Feature]], roles: tuple[str, ...] = ("order",)) -> dict[str, list[str]]:
    """observation_id -> cechy => {skrót kontaktu: [observation_id,...]} dla ról (domyślnie zamówieniowych)."""
    groups: dict[str, list[str]] = {}
    for oid, feats in features.items():
        for (kind, key), f in _contacts(feats).items():
            if f.value["role"] in roles:
                groups.setdefault(key, []).append(oid)
    return {k: sorted(set(v)) for k, v in groups.items()}
