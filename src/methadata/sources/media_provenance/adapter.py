"""S07: pochodzenie materiału wg C2PA — manifest, składniki źródłowe, relacje wersji, wynik walidacji."""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from methadata.contracts import ids
from methadata.contracts.interfaces import AdapterKind, AdapterSpec
from methadata.contracts.models import (ArtifactRef, ArtifactRole, ExtractionState, ExtractionStatus, Feature,
                                        Observation, PairEvidence, Relation, ValueType)

from .reader import Provenance, Unreadable, Unsupported, read_provenance, sniff_mime, validator_info

ADAPTERS_VERSION = "1.0.0"
FAMILY = "provenance"
_MEDIA_ROLES = {ArtifactRole.creative_image, ArtifactRole.video, ArtifactRole.landing_screenshot, ArtifactRole.other}


def _extractor_version() -> str:
    v = validator_info()
    return f"s07-c2pa/{ADAPTERS_VERSION}+{v['engine']}-{v['library'] or 'none'}-sdk{v['sdk'] or 'none'}"


def _is_media(a) -> bool:
    mt = (a.media_type or "").lower()
    if a.role in (ArtifactRole.creative_image, ArtifactRole.video, ArtifactRole.landing_screenshot):
        return True
    return a.role is ArtifactRole.other and (mt.startswith("image/") or mt.startswith("video/"))


class MediaProvenanceExtractor:
    spec = AdapterSpec(id="source-media-provenance-extractor", kind=AdapterKind.extractor, version=ADAPTERS_VERSION,
                       description="C2PA: manifest, składniki źródłowe, status walidacji, czas podpisu (obraz/wideo)",
                       extras=[])  # extra `provenance` (c2pa-python) opcjonalny: bez niego ograniczony parser JUMBF

    def extract(self, observation: Observation, *, reader, optional: Mapping[str, Any] | None = None):
        oid, ev = observation.observation_id, _extractor_version()
        st = lambda state, detail=None, art=None: ExtractionStatus(  # noqa: E731
            observation_id=oid, artifact_id=art, extractor_version=ev, state=state, detail=detail)
        targets = [a for a in observation.artifacts if _is_media(a)]
        if not targets:
            return [], [st(ExtractionState.missing_input, "brak artefaktu graficznego/wideo")]
        feats: list[Feature] = []
        sts: list[ExtractionStatus] = []
        for art in targets:
            try:
                data = reader.open_bytes(ArtifactRef(artifact_id=art.artifact_id))
            except Exception as e:
                sts.append(st(ExtractionState.missing_input, f"nie można odczytać bajtów: {e}", art.artifact_id))
                continue
            try:
                prov = read_provenance(data, art.media_type if art.media_type and sniff_mime(data) is None else None)
            except Unsupported as e:
                sts.append(st(ExtractionState.unsupported, str(e), art.artifact_id))
                continue
            except Unreadable as e:
                sts.append(st(ExtractionState.failed, f"manifest nieczytelny: {e}", art.artifact_id))
                continue
            feats += self._features(observation, art.artifact_id, prov, ev)
            sts.append(st(ExtractionState.ok, prov.detail, art.artifact_id))
        return feats, sts

    @staticmethod
    def _features(obs: Observation, art_id: str, p: Provenance, ev: str) -> list[Feature]:
        oid = obs.observation_id
        refs = [ArtifactRef(artifact_id=art_id)]
        out: list[Feature] = []

        def add(kind: str, value: Any, vt: ValueType):
            out.append(Feature(feature_id=ids.feature_id(oid, FAMILY, kind, ev, [art_id, value]), observation_id=oid,
                               family=FAMILY, kind=kind, value_type=vt, value=value, extractor_version=ev,
                               evidence_refs=refs))

        add("c2pa_present", p.present, ValueType.boolean)
        if p.present:
            add("c2pa_validation_state", p.validation_state or "Unverified", ValueType.string)
            add("c2pa_validation", {"state": p.validation_state, "failure_codes": p.failure_codes,
                                    "engine": p.engine, "engine_version": p.engine_version,
                                    "sdk_version": p.sdk_version, "spec_version": p.spec_version,
                                    "trust_list": "none (certyfikat spoza listy zaufania daje Valid, nie Trusted)"},
                ValueType.json)
            if p.active_manifest:
                add("c2pa_active_manifest", p.active_manifest, ValueType.string)
            add("c2pa_ancestor_manifests", list(p.ancestor_manifests), ValueType.string_list)
            if p.instance_id:
                add("c2pa_instance_id", p.instance_id, ValueType.string)
            if p.ingredients:
                add("c2pa_ingredients", p.ingredients, ValueType.json)
            if p.claim_generator:
                add("c2pa_claim_generator", p.claim_generator, ValueType.string)
            if p.signer:
                add("c2pa_signer", p.signer, ValueType.json)
            if p.signed_time:
                add("c2pa_signed_time", p.signed_time, ValueType.string)
            if p.actions:
                add("c2pa_actions", p.actions, ValueType.string_list)
        add("record_provenance", {"repository": "c2pa-manifest", "present": p.present,
                                  "validated": p.engine == "c2pa-python", "retrieved_at": obs.capture_time,
                                  "signed_time": p.signed_time, "note": p.detail}, ValueType.json)
        return out


# ---------------------------------------------------------------------------- porównanie

def _vals(feats: Sequence[Feature], kind: str) -> list[Feature]:
    return [f for f in feats if f.family == FAMILY and f.kind == kind]


def _side(feats: Sequence[Feature]) -> dict:
    present = any(f.value is True for f in _vals(feats, "c2pa_present"))
    active = {f.value for f in _vals(feats, "c2pa_active_manifest")}
    anc = {v for f in _vals(feats, "c2pa_ancestor_manifests") for v in f.value}
    states = {f.value for f in _vals(feats, "c2pa_validation_state")}
    refs = [r for f in feats if f.family == FAMILY and f.kind in (
        "c2pa_active_manifest", "c2pa_ancestor_manifests", "c2pa_validation_state") for r in f.evidence_refs]
    return {"present": present, "active": active, "anc": anc, "states": states, "refs": refs}


class MediaProvenanceComparator:
    method_id = "source-media-provenance-compare"
    spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version=ADAPTERS_VERSION,
                       description="Wspólny materiał źródłowy w manifestach C2PA (przodkowie/składniki); "
                                   "niezwalidowane lub unieważnione manifesty obniżają wynik")

    def compare(self, a: Observation, b: Observation, features: Mapping[str, Sequence[Feature]], *, run_id: str,
                optional: Mapping[str, Any] | None = None) -> Sequence[PairEvidence]:
        (ca, oa), (cb, ob) = sorted([(a.canonical_id, a), (b.canonical_id, b)], key=lambda t: t[0])
        sa, sb = _side(features.get(oa.observation_id, [])), _side(features.get(ob.observation_id, []))
        pair = (ca, cb)

        def make(score: Optional[float], why: str, refs=None):
            return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id,
                                 relation=Relation.campaign_candidate, score=score, evidence_refs=refs or {},
                                 explanation=why)]

        if not sa["present"] or not sb["present"]:
            return make(None, "brak danych: co najmniej jeden materiał nie ma manifestu C2PA "
                              "(brak manifestu nie dowodzi braku wspólnego źródła)")
        refs = {ca: sa["refs"], cb: sb["refs"]}
        if not refs[ca] or not refs[cb]:
            return make(None, "brak podstaw: cechy bez odwołań do artefaktów")
        derived = (sa["active"] & sb["anc"]) | (sb["active"] & sa["anc"])
        shared = sa["anc"] & sb["anc"]
        if derived:
            score, why = 1.0, f"jeden materiał pochodzi od drugiego (manifest {sorted(derived)[0]} w historii)"
        elif shared:
            score, why = 0.9, f"wspólny materiał źródłowy w manifestach (manifest {sorted(shared)[0]})"
        else:
            return make(None, "manifesty obecne, lecz bez wspólnego składnika; to nie dowód braku związku "
                              "(manifest wymienia tylko zadeklarowane składniki)", refs)
        states = sa["states"] | sb["states"]
        if "Invalid" in states:
            return make(min(score, 0.3), why + "; MYLĄCE: manifest nie przechodzi walidacji (Invalid), "
                                               "wspólne źródło niewiarygodne", refs)
        if "Unverified" in states:
            return make(min(score, 0.6), why + "; manifest nie został zwalidowany (ograniczony parser JUMBF)", refs)
        if "Trusted" not in states or states - {"Trusted"}:
            why += "; podpis poprawny, ale wystawca spoza listy zaufania (Valid, nie Trusted)"
        return make(score, why, refs)
