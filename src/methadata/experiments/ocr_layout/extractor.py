"""Ekstraktor OCR: tekst z prostokątami (cechy `ocr/ocr_boxes`, `ocr_text`, `ocr_layout`) dla zrzutów i kreacji."""
from __future__ import annotations

import json
from typing import Any, Mapping, Optional

from methadata.contracts import (AdapterKind, AdapterSpec, ArtifactRef, ExtractionState, ExtractionStatus, Feature, ids)
from methadata.contracts.models import ArtifactRole, ParentRelation, ValueType

from .compare import FAMILY, KIND_BOXES, image_ref
from .engine import OcrEngine, OcrUnavailable, default_engine
from .layout import analyze_layout
from .model import OcrResult

EXTRACTOR_VERSION = "ocr-layout-1"


def _is_image(a) -> bool:
    if a.role in (ArtifactRole.landing_screenshot, ArtifactRole.creative_image):
        return True
    return a.role is ArtifactRole.resource and (a.media_type or "").lower().startswith("image/")


class OcrExtractor:
    spec = AdapterSpec(id="ocr-layout-extractor", kind=AdapterKind.extractor, version=EXTRACTOR_VERSION,
                       description="OCR z prostokątami (względnymi) + układ tytuł–ilustracja–przycisk",
                       extras=["ocr", "media"])

    def __init__(self, engine: Optional[OcrEngine] = None, min_side: int = 64):
        self._engine = engine
        self.min_side = min_side

    def _get_engine(self, optional) -> OcrEngine:
        e = (optional or {}).get("ocr_engine") or self._engine
        if e is None:
            e = self._engine = default_engine()
        return e

    def extract(self, observation, *, reader, optional: Optional[Mapping[str, Any]] = None):
        oid = observation.observation_id
        store = (optional or {}).get("store")
        feats: list[Feature] = []
        sts: list[ExtractionStatus] = []

        def status(art, state, detail=None):
            sts.append(ExtractionStatus(observation_id=oid, artifact_id=art.artifact_id if art else None,
                                        extractor_version=EXTRACTOR_VERSION, state=state, detail=detail))

        imgs = [a for a in observation.artifacts if _is_image(a)]
        if not imgs:
            status(None, ExtractionState.ok, "brak obrazów; pusty wynik to brak podstaw")
            return feats, sts
        try:
            engine = self._get_engine(optional)
        except OcrUnavailable as e:
            for a in imgs:
                status(a, ExtractionState.unsupported, f"silnik OCR niedostępny: {e}")
            return feats, sts

        def add(art, kind, vt, value, refs):
            feats.append(Feature(feature_id=ids.feature_id(oid, FAMILY, kind, EXTRACTOR_VERSION, [art.artifact_id, value]),
                                 observation_id=oid, family=FAMILY, kind=kind, value_type=vt, value=value,
                                 extractor_version=EXTRACTOR_VERSION, evidence_refs=refs))

        for art in imgs:
            try:
                data = reader.open_bytes(ArtifactRef(artifact_id=art.artifact_id))
            except (FileNotFoundError, KeyError, OSError) as e:
                status(art, ExtractionState.missing_input, f"brak pliku ({art.local_ref}): {type(e).__name__}")
                continue
            if not data:
                status(art, ExtractionState.missing_input, "pusty plik obrazu")
                continue
            try:
                res: OcrResult = engine.recognize(data)
            except Exception as e:  # noqa: BLE001 - uszkodzony obraz/awaria silnika to status, nie wyjątek
                status(art, ExtractionState.failed, f"OCR nie powiódł się: {type(e).__name__}: {e}"[:300])
                continue
            if min(res.width, res.height) < self.min_side:
                status(art, ExtractionState.ok, "obraz zbyt mały na OCR; brak podstaw")
                continue
            refs = [image_ref(art.artifact_id, res, l.rect) for l in res.lines]
            add(art, KIND_BOXES, ValueType.json, res.to_dict(), refs or [ArtifactRef(artifact_id=art.artifact_id)])
            if not res.lines:
                status(art, ExtractionState.ok, "pusty OCR (brak rozpoznanego tekstu); brak podstaw, nie zerowe podobieństwo")
                continue
            add(art, "ocr_text", ValueType.string, "\n".join(l.text for l in res.lines), refs)
            add(art, "ocr_layout", ValueType.json, analyze_layout(res), [ArtifactRef(artifact_id=art.artifact_id)])
            if store is not None:
                store.put_derived(observation_id=oid, role=ArtifactRole.other,
                                  data=json.dumps(res.to_dict(), ensure_ascii=False, sort_keys=True).encode("utf-8"),
                                  media_type="application/json", parent=ArtifactRef(artifact_id=art.artifact_id),
                                  parent_relation=ParentRelation.derived_from, origin=f"ocr-boxes:{engine.name}")
            status(art, ExtractionState.ok)
        return feats, sts
