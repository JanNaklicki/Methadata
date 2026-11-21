"""S08: adapter F01 - cecha 'page_version' (pochodzenie, czas, status) i porównanie wersji/stron."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from methadata.contracts import ids
from methadata.contracts.interfaces import AdapterKind, AdapterSpec
from methadata.contracts.models import (ArtifactRef, ArtifactRole, ExtractionState, ExtractionStatus, Feature,
                                        Observation, PairEvidence, ParentRelation, Relation, ValueType)

from .analyze import analyze_html, normalize_url
from .cluster import signals_of
from .diff import diff_snapshots
from .model import iso, parse_time

ADAPTERS_VERSION = "1.0.0"
EXTRACTOR_VERSION = f"s08-history/{ADAPTERS_VERSION}"
FAMILY = "history"
_CFG = Path(__file__).with_name("config.json")


def load_config() -> dict:
    return json.loads(_CFG.read_text(encoding="utf-8"))


def _is_html(a) -> bool:
    mt = (a.media_type or "").lower()
    ref = (a.origin or a.local_ref or "").lower()
    return a.role in (ArtifactRole.landing_html, ArtifactRole.landing_dom) or "html" in mt or ref.endswith((".html", ".htm", ".mhtml"))


class HistoryExtractor:
    """Cecha 'page_version': URL, captured_at, known_at, published_at, source, migawka oferty/kontaktu/CTA, status.
    optional: known_at, source, published_at (np. z kopii archiwalnej)."""
    spec = AdapterSpec(id="source-history-extractor", kind=AdapterKind.extractor, version=ADAPTERS_VERSION,
                       description="Wersja strony w czasie: oferta, kontakt, cel CTA, czas pobrania/publikacji i źródło")

    def __init__(self, contact_extractor=None):
        self._ce = contact_extractor

    def extract(self, observation: Observation, *, reader, optional: Mapping[str, Any] | None = None):
        oid, opt = observation.observation_id, dict(optional or {})
        st = lambda state, detail=None, art=None: ExtractionStatus(  # noqa: E731
            observation_id=oid, artifact_id=art, extractor_version=EXTRACTOR_VERSION, state=state, detail=detail)
        art = next((a for a in observation.artifacts if _is_html(a)), None)
        if art is None:
            return [], [st(ExtractionState.missing_input, "brak artefaktu HTML")]
        try:
            raw = reader.open_bytes(ArtifactRef(artifact_id=art.artifact_id)).decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            return [], [st(ExtractionState.missing_input, f"nie można odczytać bajtów: {type(e).__name__}", art.artifact_id)]
        url = normalize_url(observation.source_ref) if "://" in observation.source_ref else ""
        snap = analyze_html(raw, url, contact_extractor=self._ce)
        cap = parse_time(observation.capture_time)
        status = "ok" if cap and snap["status"] == "ok" else ("no_capture_time" if snap["status"] == "ok" else "no_data")
        value = {"url": url or None, "captured_at": iso(cap), "known_at": iso(parse_time(opt.get("known_at"))) or iso(cap),
                 "published_at": iso(parse_time(opt.get("published_at") or snap.get("published_at"))),
                 "source": opt.get("source") or ("intake" if url else "file"), "status": status, "snapshot": snap,
                 "provenance": {"extractor": EXTRACTOR_VERSION, "artifact_id": art.artifact_id, "sha256": art.sha256}}
        f = Feature(feature_id=ids.feature_id(oid, FAMILY, "page_version", EXTRACTOR_VERSION, art.sha256),
                    observation_id=oid, family=FAMILY, kind="page_version", value_type=ValueType.json, value=value,
                    extractor_version=EXTRACTOR_VERSION, evidence_refs=[ArtifactRef(artifact_id=art.artifact_id)])
        return [f], [st(ExtractionState.ok, None if status == "ok" else f"status cechy: {status}", art.artifact_id)]


def _pv(feats: Sequence[Feature]) -> Optional[Feature]:
    return next((f for f in feats if f.family == FAMILY and f.kind == "page_version" and isinstance(f.value, dict)), None)


def same_url_verdict(va: Mapping[str, Any], vb: Mapping[str, Any], cfg: Mapping[str, Any]) -> tuple[Optional[float], str]:
    """(score, wyjaśnienie) dla dwóch wersji tego samego URL (słowniki z captured_at, source, snapshot)."""
    sc = cfg["scores"]
    ta, tb = parse_time(va["captured_at"]), parse_time(vb["captured_at"])
    if ta is None or tb is None:
        return None, "ten sam URL, ale brak czasu pobrania: nie da się uporządkować wersji"
    older, newer = (va, vb) if ta <= tb else (vb, va)
    d = diff_snapshots(older["snapshot"], newer["snapshot"], cfg)
    when = f"{older['captured_at']} ({older['source']}) -> {newer['captured_at']} ({newer['source']})"
    if d["status"] == "no_data":
        return None, "ten sam URL, " + d["reason"]
    if "recipient_change" in d["labels"]:
        return sc["recipient_change"], f"mylące dopasowanie: ten sam URL, ale zmiana odbiorcy (kontakt/cel CTA) {when}"
    if "product_swap" in d["labels"]:
        return sc["product_swap"], f"mylące dopasowanie: ten sam URL, ale podmiana produktu/oferty {when}"
    if "cta_change" in d["labels"]:
        return sc["cta_change"], f"ten sam URL, zmieniony cel CTA {when}"
    return (sc["unchanged_same_url"] if d["unchanged"] else sc["cta_change"],
            f"ten sam URL, {'bez zmian' if d['unchanged'] else 'drobne zmiany treści'} {when}")


def compare_versions(older, newer, cfg: Optional[Mapping[str, Any]] = None) -> tuple[Optional[float], str]:
    """To samo dla dwóch PageVersion z jednej obserwacji (kolejne zgłoszenia pod stałym URL-em w magazynie F02)."""
    return same_url_verdict(older.to_json(), newer.to_json(), dict(cfg) if cfg else load_config())


class HistoryComparator:
    """Ten sam URL: różnica wersji (zmiana odbiorcy/produktu = mylące dopasowanie, niski wynik).
    Różne URL-e: wynik wg wspólnych sygnałów w migawkach. Brak cechy/danych -> score None."""
    method_id = "source-history-compare"
    spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version=ADAPTERS_VERSION,
                       description="Różnice oferty, kontaktu i celu CTA między wersjami strony; podmiana pod stałym URL-em")

    def __init__(self, config: Optional[Mapping[str, Any]] = None):
        self.cfg = dict(config) if config else load_config()

    def compare(self, a: Observation, b: Observation, features: Mapping[str, Sequence[Feature]], *, run_id: str,
                optional: Mapping[str, Any] | None = None) -> Sequence[PairEvidence]:
        (ca, oa), (cb, ob) = sorted([(a.canonical_id, a), (b.canonical_id, b)], key=lambda t: t[0])
        fa, fb = _pv(features.get(oa.observation_id, [])), _pv(features.get(ob.observation_id, []))
        pair = (ca, cb)
        if ca == cb:  # ta sama treść = ten sam materiał; kontrakt nie dopuszcza pary z samym sobą
            return []

        def make(score, why, refs=None):
            return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=Relation.campaign_candidate,
                                 score=score, evidence_refs=refs or {}, explanation=why)]
        if fa is None or fb is None or fa.value["status"] == "no_data" or fb.value["status"] == "no_data":
            return make(None, "brak podstaw: co najmniej jedna strona bez cechy wersji lub bez rozpoznanych sygnałów "
                              "(oferta/kontakt/CTA); brak danych to nie dowód różnicy")
        refs = {ca: list(fa.evidence_refs), cb: list(fb.evidence_refs)}
        sc = self.cfg["scores"]
        va, vb = fa.value, fb.value
        if va["url"] and va["url"] == vb["url"]:
            verdict = same_url_verdict(va, vb, self.cfg)
            return make(verdict[0], verdict[1], refs if verdict[0] is not None else None)
        sa, sb = signals_of(va["snapshot"]), signals_of(vb["snapshot"])
        shared = sorted(sa & sb)
        if shared:
            kinds = sorted({k for k, _ in shared})
            return make(sc["shared_signal"], f"różne URL-e, wspólne sygnały: {', '.join(kinds)} ({len(shared)})", refs)
        return make(sc["no_shared_signal"], "różne URL-e, brak wspólnego kontaktu/GTIN/ID oferty/celu CTA", refs)


def save_timeline(store, observation_id: str, history, url: str, *, known_by: Optional[str] = None):
    """Zapis osi wersji URL-a jako artefakt pochodny przez DerivedStore (F02), rodzic = pierwszy artefakt obserwacji."""
    obs = store.observation(observation_id)
    vs = history.versions(normalize_url(url), known_by=known_by)
    payload = json.dumps({"url": normalize_url(url), "known_by": known_by, "versions": [v.to_json() for v in vs]},
                         ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8")
    parent = ArtifactRef(artifact_id=obs.artifacts[0].artifact_id)
    return store.put_derived(observation_id=observation_id, role=ArtifactRole.document, data=payload,
                             media_type="application/json", parent=parent, parent_relation=ParentRelation.derived_from,
                             origin=f"history-timeline/{ADAPTERS_VERSION}")
