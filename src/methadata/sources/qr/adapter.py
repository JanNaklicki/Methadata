"""S02: kody QR ze zdjęć, zrzutów i klatek filmu: typ celu, znormalizowana wartość, prostokąt, czas klatki."""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Sequence

from methadata.contracts import ids
from methadata.contracts.interfaces import AdapterKind, AdapterSpec
from methadata.contracts.models import (ArtifactRef, ArtifactRole, ExtractionState, ExtractionStatus, Feature, ImageRect,
                                        Observation, PairEvidence, Relation, ValueType)

from .normalize import classify, load_config

ADAPTERS_VERSION = "1.0.0"
EXTRACTOR_VERSION = f"s02-qr/{ADAPTERS_VERSION}"
FAMILY = "qr"
KIND = "target"
_FRAME_RE = re.compile(r"frame@(\d+)ms")
_IMG_ROLES = (ArtifactRole.creative_image, ArtifactRole.landing_screenshot)
_MAX_REFS = 16


def _is_video(a) -> bool:
    return a.role is ArtifactRole.video or (a.media_type or "").lower().startswith("video/")


def _is_image(a) -> bool:
    mt = (a.media_type or "").lower()
    ref = (a.origin or a.local_ref or "").lower()
    return not _is_video(a) and (a.role in _IMG_ROLES or mt.startswith("image/")
                                 or ref.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")))


class QrExtractor:
    """Wejścia: obrazy i zrzuty (czas klatki z origin `frame@Nms`), klatki wideo oraz sam film (cv2.VideoCapture)."""
    spec = AdapterSpec(id="source-qr-extractor", kind=AdapterKind.extractor, version=ADAPTERS_VERSION,
                       description="Dekodowanie QR (typ celu, znormalizowana wartość, prostokąt, czas klatki)")

    def __init__(self, config: Optional[Mapping[str, Any]] = None):
        self.cfg = dict(config) if config else load_config()

    def extract(self, observation: Observation, *, reader, optional: Mapping[str, Any] | None = None):
        from .decode import QrBackendMissing, decode_bytes, decode_image, video_frames
        oid = observation.observation_id
        st = lambda state, detail=None, art=None: ExtractionStatus(  # noqa: E731
            observation_id=oid, artifact_id=art, extractor_version=EXTRACTOR_VERSION, state=state, detail=detail)
        jobs = [a for a in observation.artifacts if _is_video(a) or _is_image(a)]
        if not jobs:
            return [], [st(ExtractionState.missing_input, "brak obrazu, zrzutu ani wideo")]
        hits: list[tuple[Any, Optional[int], Any]] = []  # (artefakt, czas klatki, Decoded)
        sts: list[ExtractionStatus] = []
        for art in jobs:
            try:
                data = reader.open_bytes(ArtifactRef(artifact_id=art.artifact_id))
            except Exception as e:  # noqa: BLE001
                sts.append(st(ExtractionState.missing_input, f"nie można odczytać bajtów: {type(e).__name__}", art.artifact_id))
                continue
            n0 = len(hits)
            try:
                if _is_video(art):
                    v = self.cfg["video"]
                    frames = 0
                    for t, fr in video_frames(data, v["interval_ms"], v["max_frames"]):
                        frames += 1
                        hits += [(art, t, d) for d in decode_image(fr, self.cfg["decode"])]
                    if frames == 0:
                        raise ValueError("nie zdekodowano żadnej klatki wideo")
                else:
                    m = _FRAME_RE.search(art.origin or "")
                    t = int(m.group(1)) if m else None
                    hits += [(art, t, d) for d in decode_bytes(data, self.cfg["decode"])]
            except (QrBackendMissing, ValueError) as e:
                sts.append(st(ExtractionState.failed, str(e), art.artifact_id))
                continue
            sts.append(st(ExtractionState.ok, None if len(hits) > n0 else "nie znaleziono kodu QR", art.artifact_id))
        return self._features(observation, hits), sts

    def _features(self, obs: Observation, hits) -> list[Feature]:
        groups: dict[tuple, list] = {}
        for art, t, d in hits:
            tg = classify(d.text, self.cfg)
            if tg is not None:
                groups.setdefault((tg.type, tg.value), []).append((art.artifact_id, t, d, tg))
        feats = []
        for occ in groups.values():
            occ.sort(key=lambda o: (o[1] is None, o[1] or 0))
            aid, t0, d0, tg = occ[0]
            value = {"type": tg.type, "target": tg.value, "host": tg.host, "shortener": tg.shortener,
                     "rect": {"x": d0.x, "y": d0.y, "width": d0.width, "height": d0.height},
                     "frame_ms": t0, "frames_ms": [o[1] for o in occ if o[1] is not None],
                     "captured_at": obs.capture_time, "status": "ok", "raw": d0.text,
                     "provenance": {"extractor": EXTRACTOR_VERSION, "artifact_id": aid}}
            refs = [ArtifactRef(artifact_id=a_, locator=ImageRect(x=d.x, y=d.y, width=d.width, height=d.height, frame_ms=t))
                    for a_, t, d, _ in occ[:_MAX_REFS]]
            feats.append(Feature(feature_id=ids.feature_id(obs.observation_id, FAMILY, KIND, EXTRACTOR_VERSION,
                                                           f"{tg.type}|{tg.value}"),
                                 observation_id=obs.observation_id, family=FAMILY, kind=KIND, value_type=ValueType.json,
                                 value=value, extractor_version=EXTRACTOR_VERSION, evidence_refs=refs))
        return feats


# ---------------------------------------------------------------------------- porównanie

def _targets(feats: Sequence[Feature]) -> list[Feature]:
    return [f for f in feats if f.family == FAMILY and f.kind == KIND and isinstance(f.value, dict)]


class QrComparator:
    method_id = "source-qr-compare"
    spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version=ADAPTERS_VERSION,
                       description="Ten sam konkretny cel QR silny; wspólny host skracacza słaby")

    def __init__(self, config: Optional[Mapping[str, Any]] = None):
        self.cfg = dict(config) if config else load_config()

    def compare(self, a: Observation, b: Observation, features: Mapping[str, Sequence[Feature]], *, run_id: str,
                optional: Mapping[str, Any] | None = None) -> Sequence[PairEvidence]:
        (ca, oa), (cb, ob) = sorted([(a.canonical_id, a), (b.canonical_id, b)], key=lambda t: t[0])
        ta, tb = _targets(features.get(oa.observation_id, [])), _targets(features.get(ob.observation_id, []))
        sc = self.cfg["scores"]
        pair = (ca, cb)

        def make(score, why, fa=(), fb=()):
            refs = {ca: [r for f in fa for r in f.evidence_refs], cb: [r for f in fb for r in f.evidence_refs]} if fa else {}
            return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=Relation.campaign_candidate,
                                 score=score, evidence_refs=refs, explanation=why)]

        if not ta or not tb:
            return make(None, "brak podstaw: co najmniej jedna strona nie ma odczytanego kodu QR "
                              "(brak kodu to nie dowód różnych celów)")
        cands: list[tuple[float, str, Feature, Feature]] = []
        for x in ta:
            for y in tb:
                vx, vy = x.value, y.value
                if vx["type"] != vy["type"]:
                    continue
                if vx["target"] == vy["target"]:
                    cands.append((sc["same_target"], f"ten sam cel QR ({vx['type']}): {vx['target']}", x, y))
                elif vx["type"] == "url" and vx["host"] == vy["host"]:
                    if vx["shortener"]:
                        cands.append((sc["same_shortener_other_target"],
                                      f"mylące dopasowanie: wspólny host skracacza {vx['host']}, ale różne cele "
                                      f"({vx['target']} / {vy['target']})", x, y))
                    elif vx["target"].split("?")[0] == vy["target"].split("?")[0]:
                        cands.append((sc["same_path_other_query"],
                                      f"ta sama strona {vx['target'].split('?')[0]}, różne parametry zapytania", x, y))
                    else:
                        cands.append((sc["same_host_other_target"],
                                      f"ten sam host {vx['host']}, ale różne cele ({vx['target']} / {vy['target']})", x, y))
        if cands:
            best = max(cands, key=lambda c: c[0])
            return make(best[0], best[1], [best[2]], [best[3]])
        return make(sc["no_shared_target"], "różne cele QR, brak wspólnego hosta", ta, tb)
