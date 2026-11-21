"""S01: płatnik, beneficjent, reklamodawca, ID emisji i okres wyświetlania z rekordu Meta Ad Library."""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Mapping, Optional, Sequence

from methadata.contracts import ids
from methadata.contracts.interfaces import AdapterKind, AdapterSpec
from methadata.contracts.models import (ArtifactRef, ArtifactRole, ExtractionState, ExtractionStatus, Feature,
                                        Observation, PairEvidence, Relation, TextSpan, ValueType)

ADAPTERS_VERSION = "1.0.0"
EXTRACTOR_VERSION = f"s01-payer/{ADAPTERS_VERSION}"
REPOSITORY = "meta-ad-library"
FAMILY = "payer"
_LEGAL = re.compile(r"\b(sp\.? ?z ?o\.? ?o\.?|s\.?a\.?|sp\.? ?k\.?|ltd|llc|inc|gmbh|ag|bv|srl|limited)\b\.?")
_BYLINE = re.compile(r"^\s*(?:paid for by|zapłacone przez|opłacone przez)\s*:?\s*(.+?)\s*$", re.I)


def normalize_entity(name: Optional[str]) -> str:
    """Klucz porównania nazwy podmiotu: NFKC, casefold, bez znaków diakrytycznych, form prawnych i interpunkcji."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", name).casefold())
    s = "".join(c for c in s if not unicodedata.combining(c)).replace("ł", "l")
    s = _LEGAL.sub(" ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _s(v: Any) -> Optional[str]:
    return v.strip() if isinstance(v, str) and v.strip() else None


def _records(doc: Any) -> list[dict]:
    if isinstance(doc, dict) and isinstance(doc.get("data"), list):
        return [r for r in doc["data"] if isinstance(r, dict)]
    return [doc] if isinstance(doc, dict) else []


def _declared(rec: Mapping[str, Any]):
    """-> (payers, beneficiaries, payer_fields): płatnik wg pierwszego dostępnego pola."""
    payers: list[str] = []
    bens: list[str] = []
    fields: list[str] = []
    bp = rec.get("beneficiary_payers")
    if isinstance(bp, list):
        for e in bp:
            if isinstance(e, dict):
                if _s(e.get("payer")):
                    payers.append(_s(e["payer"]))
                if _s(e.get("beneficiary")):
                    bens.append(_s(e["beneficiary"]))
        if payers:
            fields.append("beneficiary_payers.payer")
        if bens:
            fields.append("beneficiary_payers.beneficiary")
    if not payers and _s(rec.get("funding_entity")):
        payers.append(_s(rec["funding_entity"]))
        fields.append("funding_entity")
    if not payers and _s(rec.get("bylines")):
        m = _BYLINE.match(rec["bylines"])
        payers.append(m.group(1) if m else rec["bylines"].strip())
        fields.append("bylines")
    return payers, bens, fields


class PayerExtractor:
    spec = AdapterSpec(id="source-payer-extractor", kind=AdapterKind.extractor, version=ADAPTERS_VERSION,
                       description="Meta Ad Library: płatnik, beneficjent, reklamodawca, ID emisji, okres emisji")

    def extract(self, observation: Observation, *, reader, optional: Mapping[str, Any] | None = None):
        oid = observation.observation_id
        st = lambda state, detail=None, art=None: ExtractionStatus(  # noqa: E731
            observation_id=oid, artifact_id=art, extractor_version=EXTRACTOR_VERSION, state=state, detail=detail)
        targets = [a for a in observation.artifacts if a.role is ArtifactRole.document
                   and ((a.media_type or "").endswith("json") or (a.origin or a.local_ref or "").endswith(".json"))]
        if not targets:
            return [], [st(ExtractionState.missing_input, "brak artefaktu document (JSON rekordu Ad Library)")]
        feats: list[Feature] = []
        sts: list[ExtractionStatus] = []
        for art in targets:
            try:
                text = reader.open_bytes(ArtifactRef(artifact_id=art.artifact_id)).decode("utf-8-sig")
            except Exception as e:
                sts.append(st(ExtractionState.missing_input, f"nie można odczytać bajtów: {e}", art.artifact_id))
                continue
            try:
                recs = _records(json.loads(text))
            except ValueError as e:
                sts.append(st(ExtractionState.failed, f"niepoprawny JSON: {e}", art.artifact_id))
                continue
            if len(recs) != 1:
                sts.append(st(ExtractionState.unsupported,
                              f"oczekiwano 1 rekordu reklamy, jest {len(recs)}; importuj jeden rekord na obserwację",
                              art.artifact_id))
                continue
            f, s = self._record(observation, art, text, recs[0])
            feats += f
            sts.append(s)
        return feats, sts

    def _record(self, obs: Observation, art, text: str, rec: Mapping[str, Any]):
        oid = obs.observation_id

        def ref(value: str) -> list[ArtifactRef]:
            i = text.find(json.dumps(value, ensure_ascii=False)[1:-1])
            loc = TextSpan(start=i, end=i + len(json.dumps(value, ensure_ascii=False)[1:-1])) if i >= 0 and value else None
            return [ArtifactRef(artifact_id=art.artifact_id, locator=loc)]

        out: list[Feature] = []
        seen: set[tuple] = set()

        def add(kind: str, value: str, vt=ValueType.string, refs=None):
            key = (kind, json.dumps(value, sort_keys=True))
            if key in seen:
                return
            seen.add(key)
            out.append(Feature(feature_id=ids.feature_id(oid, FAMILY, kind, EXTRACTOR_VERSION, value),
                               observation_id=oid, family=FAMILY, kind=kind, value_type=vt, value=value,
                               extractor_version=EXTRACTOR_VERSION,
                               evidence_refs=refs if refs is not None else ref(value if isinstance(value, str) else "")))

        payers, bens, pfields = _declared(rec)
        for p in payers:
            add("payer_name", p)
        for b in bens:
            add("beneficiary_name", b)
        used = list(pfields)
        if _s(rec.get("page_name")):
            add("advertiser_name", rec["page_name"].strip())
            used.append("page_name")
        if rec.get("page_id") not in (None, ""):
            add("advertiser_id", str(rec["page_id"]))
            used.append("page_id")
        ad_id = rec.get("id", rec.get("ad_archive_id"))
        if ad_id not in (None, ""):
            add("ad_id", str(ad_id))
            used.append("id")
        for kind, key in (("delivery_start", "ad_delivery_start_time"), ("delivery_stop", "ad_delivery_stop_time")):
            if _s(rec.get(key)):
                add(kind, rec[key].strip())
                used.append(key)
        declared = bool(payers)
        add("record_provenance", {"repository": REPOSITORY, "fields": used, "retrieved_at": obs.capture_time,
                                  "payer_declared": declared, "declaration": "self-declared by advertiser"},
            ValueType.json, [ArtifactRef(artifact_id=art.artifact_id)])
        detail = None if declared else "źródło nie ujawnia płatnika (brak beneficiary_payers/funding_entity/bylines)"
        return out, ExtractionStatus(observation_id=oid, artifact_id=art.artifact_id,
                                     extractor_version=EXTRACTOR_VERSION, state=ExtractionState.ok, detail=detail)


# ---------------------------------------------------------------------------- porównanie

def _entities(feats: Sequence[Feature], kind: str) -> dict[str, list[ArtifactRef]]:
    out: dict[str, list[ArtifactRef]] = {}
    for f in feats:
        if f.family == FAMILY and f.kind == kind and isinstance(f.value, str):
            k = normalize_entity(f.value)
            if k:
                out.setdefault(k, []).extend(f.evidence_refs)
    return out


def _refs(feats: Sequence[Feature], kinds: tuple[str, ...]) -> list[ArtifactRef]:
    return [r for f in feats if f.family == FAMILY and f.kind in kinds for r in f.evidence_refs]


class PayerComparator:
    method_id = "source-payer-compare"
    spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version=ADAPTERS_VERSION,
                       description="Zgodność deklarowanego płatnika; agencja (ten sam płatnik, różni beneficjenci) obniżona")

    def compare(self, a: Observation, b: Observation, features: Mapping[str, Sequence[Feature]], *, run_id: str,
                optional: Mapping[str, Any] | None = None) -> Sequence[PairEvidence]:
        (ca, oa), (cb, ob) = sorted([(a.canonical_id, a), (b.canonical_id, b)], key=lambda t: t[0])
        fa, fb = features.get(oa.observation_id, []), features.get(ob.observation_id, [])
        pair = (ca, cb)

        def make(score, why, refs=None):
            return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id,
                                 relation=Relation.campaign_candidate, score=score, evidence_refs=refs or {},
                                 explanation=why)]

        pa, pb = _entities(fa, "payer_name"), _entities(fb, "payer_name")
        if not pa or not pb:
            return make(None, "brak podstaw: źródło nie ujawnia płatnika po co najmniej jednej stronie "
                              "(brak deklaracji to nie dowód różnych płatników)")
        ba, bb = _entities(fa, "beneficiary_name"), _entities(fb, "beneficiary_name")
        shared_p, shared_b = set(pa) & set(pb), set(ba) & set(bb)
        refs = {ca: _refs(fa, ("payer_name", "beneficiary_name")), cb: _refs(fb, ("payer_name", "beneficiary_name"))}
        if not refs[ca] or not refs[cb]:
            return make(None, "brak podstaw: cechy bez odwołań do artefaktów")
        if shared_p:
            if ba and bb and not shared_b:
                return make(0.4, f"ten sam deklarowany płatnik ({sorted(shared_p)[0]}), ale różni beneficjenci "
                                 f"({sorted(ba)[0]} vs {sorted(bb)[0]}): wzorzec agencji, mylące dopasowanie "
                                 "do jednej kampanii", refs)
            return make(1.0, f"ten sam deklarowany płatnik ({sorted(shared_p)[0]})"
                             + (f" i beneficjent ({sorted(shared_b)[0]})" if shared_b else ""), refs)
        if shared_b:
            return make(0.7, f"ten sam beneficjent ({sorted(shared_b)[0]}), różni płatnicy "
                             f"({sorted(pa)[0]} vs {sorted(pb)[0]})", refs)
        return make(0.0, f"różni deklarowani płatnicy ({sorted(pa)[0]} vs {sorted(pb)[0]})", refs)


def group_by_payer(features: Mapping[str, Sequence[Feature]]) -> dict[str, list[str]]:
    """observation_id -> cechy => {znormalizowany płatnik: [observation_id,...]} (tylko ujawnieni płatnicy)."""
    groups: dict[str, list[str]] = {}
    for oid, feats in features.items():
        for k in _entities(feats, "payer_name"):
            groups.setdefault(k, []).append(oid)
    return {k: sorted(set(v)) for k, v in groups.items()}
