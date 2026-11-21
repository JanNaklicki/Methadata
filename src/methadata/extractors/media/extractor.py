"""Ekstraktor multimediów: format, rola, wymiary, metadane, klatki wideo z czasem i referencje do ścieżek audio."""
from __future__ import annotations

import io
from typing import Any, Mapping, Optional, Protocol, Sequence

from methadata.contracts import (AdapterKind, AdapterSpec, ArtifactRef, ExtractionState, ExtractionStatus, Feature, ids)
from methadata.contracts.models import Artifact, ArtifactRole, ImageRect, ValueType

from . import probe as P

EXTRACTOR_VERSION = "media-1"
FAMILY = "media"
ICON_MAX_SIDE = 128
DHASH_SIZE = 8


class DerivedStore(Protocol):
    """Zapis artefaktów pochodnych (F01/F02). Zwraca utrwalony Artifact z nadanym artifact_id."""
    def put_derived(self, *, parent: Artifact, data: bytes, role: str, media_type: str, origin: str) -> Artifact: ...


def _is_media(a: Artifact) -> bool:
    if a.role in (ArtifactRole.creative_image, ArtifactRole.landing_screenshot, ArtifactRole.video, ArtifactRole.audio):
        return True
    mt = (a.media_type or "").lower()
    return a.role is ArtifactRole.resource and mt.split("/")[0] in ("image", "video", "audio")


def classify_role(artifact: Artifact, p: P.Probe) -> str:
    """Rola zasobu: screenshot | icon | photo | graphic | video | audio (heurystyka, wersjonowana)."""
    if p.kind in ("video", "audio"):
        return p.kind
    if artifact.role is ArtifactRole.landing_screenshot:
        return "screenshot"
    side = max(p.width or 0, p.height or 0)
    if p.format == "ico" or (side and side <= ICON_MAX_SIDE):
        return "icon"
    if p.format == "jpeg":
        return "photo"
    return "graphic"


def _dhash(data: bytes) -> Optional[str]:
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(io.BytesIO(data)) as im:
            g = im.convert("L").resize((DHASH_SIZE + 1, DHASH_SIZE), Image.Resampling.LANCZOS)
            px = list(g.tobytes())
    except Exception:  # noqa: BLE001 - uszkodzony obraz to brak cechy, nie awaria
        return None
    bits = 0
    for r in range(DHASH_SIZE):
        for c in range(DHASH_SIZE):
            bits = (bits << 1) | (px[r * (DHASH_SIZE + 1) + c] > px[r * (DHASH_SIZE + 1) + c + 1])
    return f"{bits:016x}"


class MediaExtractor:
    spec = AdapterSpec(id="media-extractor", kind=AdapterKind.extractor, version=EXTRACTOR_VERSION,
                       description="Format, rola, wymiary i metadane obrazów; klatki z czasem i ścieżki audio wideo",
                       extras=["media"])

    def __init__(self, interval_ms: int = 1000, max_frames: int = 16):
        self.interval_ms = interval_ms
        self.max_frames = max_frames

    def _feat(self, obs_id: str, art: Artifact, kind: str, vt: ValueType, value: Any, refs=None) -> Feature:
        refs = refs if refs is not None else [ArtifactRef(artifact_id=art.artifact_id)]
        return Feature(feature_id=ids.feature_id(obs_id, FAMILY, kind, EXTRACTOR_VERSION,
                                                 [art.artifact_id, value]),
                       observation_id=obs_id, family=FAMILY, kind=kind, value_type=vt, value=value,
                       extractor_version=EXTRACTOR_VERSION, evidence_refs=refs)

    def extract(self, observation, *, reader, optional: Optional[Mapping[str, Any]] = None):
        store: Optional[DerivedStore] = (optional or {}).get("store")
        oid = observation.observation_id
        feats: list[Feature] = []
        sts: list[ExtractionStatus] = []

        def status(art: Optional[Artifact], state: ExtractionState, detail: Optional[str] = None):
            sts.append(ExtractionStatus(observation_id=oid, artifact_id=art.artifact_id if art else None,
                                        extractor_version=EXTRACTOR_VERSION, state=state, detail=detail))

        media = [a for a in observation.artifacts if _is_media(a)]
        if not media:
            status(None, ExtractionState.ok, "brak zasobów multimedialnych; pusty wynik to brak podstaw")
        for art in media:
            try:
                data = reader.open_bytes(ArtifactRef(artifact_id=art.artifact_id))
            except (FileNotFoundError, KeyError, OSError) as e:
                status(art, ExtractionState.missing_input,
                       f"brak pliku {art.role.value} ({art.local_ref}): {type(e).__name__}")
                continue
            if not data:
                status(art, ExtractionState.missing_input, f"pusty plik {art.role.value} ({art.local_ref})")
                continue
            p = P.probe_bytes(data)
            if p.format is None:
                status(art, ExtractionState.unsupported, "nierozpoznany format")
                continue
            role = classify_role(art, p)
            feats.append(self._feat(oid, art, "format", ValueType.string, p.format))
            feats.append(self._feat(oid, art, "role", ValueType.string, role))
            meta = p.metadata()
            meta["media_type"] = P.MEDIA_TYPES[p.format]
            meta["size"] = len(data)
            feats.append(self._feat(oid, art, "metadata", ValueType.json, meta))
            if p.width and p.height:
                feats.append(self._feat(oid, art, "dimensions", ValueType.json, {"width": p.width, "height": p.height}))
            if p.kind == "image":
                h = _dhash(data)
                if h:
                    feats.append(self._feat(oid, art, "dhash", ValueType.string, h))
                status(art, ExtractionState.ok, None if p.width else "nie odczytano wymiarów")
            elif p.kind == "video":
                self._video(oid, art, p, data, store, feats, status)
            else:
                feats.append(self._feat(oid, art, "audio_track", ValueType.json,
                                        {"artifact_id": art.artifact_id, "duration_ms": p.duration_ms}))
                status(art, ExtractionState.ok)
        return feats, sts

    def _video(self, oid, art, p: P.Probe, data, store, feats, status):
        audio = [t for t in p.tracks if t.get("type") == "audio"]
        if audio:
            feats.append(self._feat(oid, art, "audio_track", ValueType.json,
                                    {"artifact_id": art.artifact_id, "tracks": audio, "duration_ms": p.duration_ms}))
        if not p.duration_ms or not (p.width and p.height):
            status(art, ExtractionState.failed, "nie ustalono czasu trwania lub wymiarów wideo")
            return
        times = P.sample_times(p.duration_ms, self.interval_ms, self.max_frames)
        frames, refs = [], []
        decoded = failed = 0
        for i, t in enumerate(times):
            fr: dict[str, Any] = {"index": i, "time_ms": t}
            if store is not None:
                png = P.grab_frame(data, p.format, t)
                if png is None:
                    failed += 1
                else:
                    d = store.put_derived(parent=art, data=png, role="other", media_type="image/png",
                                          origin=f"frame@{t}ms")
                    fr["artifact_id"] = d.artifact_id
                    decoded += 1
            frames.append(fr)
            refs.append(ArtifactRef(artifact_id=art.artifact_id, locator=ImageRect(
                x=0, y=0, width=p.width, height=p.height, frame_ms=t)))
        value = {"interval_ms": self.interval_ms, "max_frames": self.max_frames, "duration_ms": p.duration_ms,
                 "frames": frames}
        feats.append(self._feat(oid, art, "video_frames", ValueType.json, value, refs or None))
        if store is None:
            status(art, ExtractionState.ok, "znaczniki czasu klatek bez dekodowania (brak magazynu pochodnych)")
        elif failed:
            status(art, ExtractionState.failed,
                   f"zdekodowano {decoded}/{len(times)} klatek (brak ffmpeg lub błąd dekodowania)")
        else:
            status(art, ExtractionState.ok)


def get_adapters():
    return [MediaExtractor()]
