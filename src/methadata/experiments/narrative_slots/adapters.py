"""Adaptery E07: ekstraktor pól scenariusza (kto/co/obietnica/produkt/wezwanie) i komparator układu ról.

Ekstraktor korzysta z pól tekstowych X01 (LexicalTextExtractor) i zapisuje po jednej cesze `narrative/slot` na
wypełnione pole (cytat + odwołania). Komparator zwraca PairEvidence (relacja shared_narrative) z wynikiem = podobieństwo
ról po zamaskowaniu nazw; zgodność nazw własnych jest raportowana osobno w wyjaśnieniu. Bez cech `narrative/slot`
komparator liczy pola z cech tekstowych E02 (lexical/text_field)."""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from methadata.contracts import (AdapterKind, AdapterSpec, ArtifactRef, ArtifactRole, ExtractionState, ExtractionStatus,
                                 Feature, PairEvidence, Relation, ValueType, ids)
from methadata.experiments.lexical.adapters import sections
from methadata.experiments.lexical.text import LexicalTextExtractor

from .compare import compare_roles
from .lexicon import LEXICON
from .slots import SLOTS, SlotBackend, extract_slots

VERSION = "narrative-slots-1"
FAMILY, KIND = "narrative", "slot"
METHOD_ID = "narrative-slots-v1"


def _landing_ref(observation) -> list[ArtifactRef]:
    for a in observation.artifacts:
        if a.role in (ArtifactRole.landing_html, ArtifactRole.landing_dom):
            return [ArtifactRef(artifact_id=a.artifact_id)]
    return []


class NarrativeSlotsExtractor:
    """backend: opcjonalny SlotBackend (model); domyślnie None = same reguły. Cytat modelu bez dosłownego pokrycia
    w tekście jest odrzucany (pole puste)."""

    def __init__(self, backend: Optional[SlotBackend] = None, lexicon: Mapping[str, Any] = LEXICON):
        self.backend, self.lexicon = backend, lexicon
        self.text = LexicalTextExtractor()
        self.spec = AdapterSpec(id="narrative-slots-extractor", kind=AdapterKind.extractor, version=VERSION,
                                description="Pola scenariusza regułami z cytatem: kto, co się wydarzyło, obietnica, "
                                            "produkt, wezwanie" + (" + backend modelowy" if backend else ""))

    def extract(self, observation, *, reader, optional: Mapping[str, Any] | None = None):
        tf, ts = self.text.extract(observation, reader=reader, optional=optional)
        oid = observation.observation_id
        stat = [s for s in ts if s.state is not ExtractionState.ok]
        secs = sections(tf)
        res = extract_slots(secs, self.lexicon, self.backend)
        feats, order = [], {id(s): i for i, s in enumerate(secs)}
        for name in SLOTS:
            sl = res[name]
            if sl is None:
                continue
            refs = list(secs[sl.section]["refs"]) or _landing_ref(observation)
            val = sl.as_value()
            val["view"] = "text"
            fid = ids.feature_id(oid, FAMILY, KIND, VERSION, [name, sl.section, sl.start, sl.end])
            feats.append(Feature(feature_id=fid, observation_id=oid, family=FAMILY, kind=KIND,
                                 value_type=ValueType.json, value=val, extractor_version=VERSION, evidence_refs=refs))
        empty = [n for n in SLOTS if res[n] is None]
        if not secs:
            stat.append(ExtractionStatus(observation_id=oid, extractor_version=VERSION, state=ExtractionState.missing_input,
                                         detail="brak tekstu HTML do ekstrakcji pól"))
        else:
            stat.append(ExtractionStatus(observation_id=oid, extractor_version=VERSION, state=ExtractionState.ok,
                                         detail=f"wypełnione={len(feats)}, puste={','.join(empty) or '-'}"))
        return feats, stat


def slots_from_features(features: Sequence[Any]) -> tuple[dict[str, Optional[dict]], dict[str, list[ArtifactRef]], str]:
    """{pole: wartość|None}, {pole: odwołania}, źródło ('features' | 'lexical' | 'none')."""
    slot_f = [f for f in features if f.family == FAMILY and f.kind == KIND]
    if slot_f:
        vals = {f.value["slot"]: f.value for f in slot_f}
        refs = {f.value["slot"]: list(f.evidence_refs) for f in slot_f}
        return {s: vals.get(s) for s in SLOTS}, refs, "features"
    secs = sections(features)
    if not secs:
        return {s: None for s in SLOTS}, {}, "none"
    res = extract_slots(secs)
    return ({s: (res[s].as_value() if res[s] else None) for s in SLOTS},
            {s: list(secs[res[s].section]["refs"]) for s in SLOTS if res[s]}, "lexical")


class NarrativeSlotsComparator:
    def __init__(self, method_id: str = METHOD_ID):
        self.method_id = method_id
        self.spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version=VERSION,
                                description="Podobieństwo układu ról scenariusza po zamaskowaniu nazw własnych; "
                                            "zgodność nazw raportowana osobno")

    def compare(self, a, b, features, *, run_id, optional: Mapping[str, Any] | None = None) -> Sequence[PairEvidence]:
        (ca, oa), (cb, ob) = sorted([(a.canonical_id, a), (b.canonical_id, b)], key=lambda t: t[0])
        pair = (ca, cb)

        def none(why):
            return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=Relation.shared_narrative,
                                 score=None, explanation=why)]
        sa, ra, srca = slots_from_features(features.get(oa.observation_id, []))
        sb, rb, srcb = slots_from_features(features.get(ob.observation_id, []))
        r = compare_roles(sa, sb)
        if r["role_score"] is None:
            return none(f"brak podstaw: mniej niż 2 pola scenariusza z cytatem po obu stronach "
                        f"(wspólnych {r['slots_both']}; źródło A: {srca}, B: {srcb})")
        refs: dict[str, list[ArtifactRef]] = {ca: [], cb: []}
        for cid, rr in ((ca, ra), (cb, rb)):
            seen = set()
            for s in SLOTS:
                if r["per_slot"][s].get("state") == "both":
                    for x in rr.get(s, []):
                        k = x.model_dump_json()
                        if k not in seen:
                            seen.add(k)
                            refs[cid].append(x)
        if not refs[ca] or not refs[cb]:
            return none("brak podstaw: pola scenariusza bez odwołań do artefaktów")
        parts = []
        for s in SLOTS:
            p = r["per_slot"][s]
            if p["state"] == "both":
                qa, qb = (sa[s]["quote"], sb[s]["quote"])
                parts.append(f"{s}: rola {p['role_sim']:.2f} [{p['cls'][0]}/{p['cls'][1]}] A=„{qa[:70]}” B=„{qb[:70]}”")
            elif p["state"] != "empty_both":
                parts.append(f"{s}: puste po jednej stronie")
        nm = "brak nazw" if r["name_score"] is None else f"{r['name_score']:.2f}"
        expl = (f"Układ ról po zamaskowaniu nazw własnych: {r['role_score']:.4f} (rama zgodna: "
                f"{'tak' if r['frame_match'] else 'nie'}). Zgodność konkretnych nazw (osobno, nie wchodzi do wyniku): "
                f"{nm}, wspólne rdzenie: {', '.join(r['shared_names']) or '-'}. " + "; ".join(parts))
        return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=Relation.shared_narrative,
                             score=min(1.0, max(0.0, r["role_score"])), evidence_refs=refs, explanation=expl)]


def get_adapters() -> list[Any]:
    return [NarrativeSlotsExtractor(), NarrativeSlotsComparator()]
