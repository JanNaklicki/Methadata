"""E09: podobieństwo grafik po zmianie JPEG, skali i kadru.

Ekstraktor `perceptual-extractor` zapisuje dla każdej grafiki (creative_image, zasoby image/*):
 - `phash`      json {artifact_id, phash(hex64), width, height, group}
 - `keypoints`  json {artifact_id, group, width, height, scale, pts, desc}  (tylko grupa illustration)
Grupa `icon` (max bok <= icon_max_side) jest kontrolna: ma pHash, nie ma punktów, komparatory ją pomijają.

Komparatory zwracają PairEvidence (relation=shared_template). Brak grafik w grupie illustration po którejś
stronie -> score=None. Progi i położenie dopasowań są w `explanation` oraz w `evidence_refs` (ImageRect)."""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from methadata.contracts import (AdapterKind, AdapterSpec, ArtifactRef, ExtractionState, ExtractionStatus, Feature,
                                 PairEvidence, Relation, ids)
from methadata.contracts.models import ArtifactRole, ImageRect, ValueType

from . import keypoints as K
from . import phash as P
from .config import DEFAULTS, load_config

EXTRACTOR_VERSION = "perceptual-1"
FAMILY = "perceptual"
KIND_PHASH, KIND_KP = "phash", "keypoints"


def _is_image(a) -> bool:
    if a.role is ArtifactRole.creative_image:
        return True
    return a.role is ArtifactRole.resource and (a.media_type or "").lower().startswith("image/")


def group_of(width: int, height: int, cfg: Mapping[str, Any] = DEFAULTS) -> str:
    return "icon" if max(width, height) <= cfg["icon_max_side"] else "illustration"


class PerceptualExtractor:
    spec = AdapterSpec(id="perceptual-extractor", kind=AdapterKind.extractor, version=EXTRACTOR_VERSION,
                       description="pHash 64 b i punkty ORB grafik; ikony jako grupa kontrolna",
                       extras=["perceptual"])

    def __init__(self, config: Optional[Mapping[str, Any]] = None):
        self.cfg = {**DEFAULTS, **(config or {})}

    def _feat(self, oid, art, kind, value):
        return Feature(feature_id=ids.feature_id(oid, FAMILY, kind, EXTRACTOR_VERSION, [art.artifact_id, kind]),
                       observation_id=oid, family=FAMILY, kind=kind, value_type=ValueType.json, value=value,
                       extractor_version=EXTRACTOR_VERSION, evidence_refs=[ArtifactRef(artifact_id=art.artifact_id)])

    def extract(self, observation, *, reader, optional: Optional[Mapping[str, Any]] = None):
        oid = observation.observation_id
        feats: list[Feature] = []
        sts: list[ExtractionStatus] = []

        def status(art, state, detail=None):
            sts.append(ExtractionStatus(observation_id=oid, artifact_id=art.artifact_id if art else None,
                                        extractor_version=EXTRACTOR_VERSION, state=state, detail=detail))
        imgs = [a for a in observation.artifacts if _is_image(a)]
        if not imgs:
            status(None, ExtractionState.ok, "brak grafik; pusty wynik to brak podstaw")
        for art in imgs:
            try:
                data = reader.open_bytes(ArtifactRef(artifact_id=art.artifact_id))
            except (FileNotFoundError, KeyError, OSError) as e:
                status(art, ExtractionState.missing_input, f"brak pliku {art.local_ref}: {type(e).__name__}")
                continue
            try:
                gray = P.load_gray(data) if data else None
            except ImportError as e:
                status(art, ExtractionState.unsupported, f"brak biblioteki ({e.name}); zainstaluj extra perceptual")
                continue
            if gray is None:
                status(art, ExtractionState.unsupported, "obraz niedekodowalny (np. SVG, uszkodzony)")
                continue
            w, h = gray.size
            group = group_of(w, h, self.cfg)
            try:
                ph = P.phash_gray(gray, self.cfg["phash_flat_std"])
            except ImportError as e:
                status(art, ExtractionState.unsupported, f"brak biblioteki ({e.name}); zainstaluj extra perceptual")
                continue
            feats.append(self._feat(oid, art, KIND_PHASH, {
                "artifact_id": art.artifact_id, "phash": None if ph is None else P.to_hex(ph),
                "width": w, "height": h, "group": group}))
            note = "obraz płaski: brak pHash" if ph is None else None
            if group == "illustration" and self.cfg.get("extract_keypoints", True):
                try:
                    ks = K.detect(gray, self.cfg)
                except ImportError as e:
                    status(art, ExtractionState.unsupported,
                           f"brak biblioteki ({e.name}); pHash zapisany, punkty pominięte")
                    continue
                feats.append(self._feat(oid, art, KIND_KP, {"artifact_id": art.artifact_id, "group": group,
                                                            **ks.to_json()}))
                if len(ks.pts) < self.cfg["kp_min_inliers"]:
                    note = (note + "; " if note else "") + f"mało punktów charakterystycznych ({len(ks.pts)})"
            status(art, ExtractionState.ok, note)
        return feats, sts


def _items(features: Sequence[Feature], kind: str, cfg) -> list[Mapping[str, Any]]:
    out = [f.value for f in features if f.family == FAMILY and f.kind == kind
           and f.value.get("group") == "illustration"]
    out.sort(key=lambda v: v["artifact_id"])
    return out[: int(cfg["max_images"])]


class _Base:
    relation = Relation.shared_template
    kind = ""
    method_id = ""

    def __init__(self, config: Optional[Mapping[str, Any]] = None):
        self.cfg = {**DEFAULTS, **(config or {})}

    def _none(self, pair, run_id, why):
        return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=self.relation,
                             score=None, explanation=why)]

    def compare(self, a, b, features, *, run_id, optional=None) -> Sequence[PairEvidence]:
        (ca, oa), (cb, ob) = sorted([(a.canonical_id, a), (b.canonical_id, b)], key=lambda t: t[0])
        pair = (ca, cb)
        ia = _items(features.get(oa.observation_id, []), self.kind, self.cfg)
        ib = _items(features.get(ob.observation_id, []), self.kind, self.cfg)
        if not ia or not ib:
            return self._none(pair, run_id, "brak podstaw: po jednej ze stron brak grafik w grupie illustration "
                                            "(ikony to grupa kontrolna i nie są porównywane)")
        try:
            res = self._best(ia, ib)
        except ImportError as e:
            return self._none(pair, run_id, f"brak podstaw: brak biblioteki ({e.name}); zainstaluj extra perceptual")
        if res is None:
            return self._none(pair, run_id, "brak podstaw: żadna para grafik nie jest porównywalna "
                                            "(obrazy płaskie lub zbyt mało punktów charakterystycznych)")
        score, refs_a, refs_b, expl = res
        return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=self.relation,
                             score=min(1.0, max(0.0, score)), evidence_refs={ca: refs_a, cb: refs_b},
                             explanation=expl)]


class PHashComparator(_Base):
    kind = KIND_PHASH
    method_id = "perceptual-phash-v1"
    spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version="1",
                       description="Najmniejsza odległość Hamminga pHash między grafikami (illustration)")

    def _best(self, ia, ib):
        thr = self.cfg["phash_threshold"]
        best = None
        for x in ia:
            for y in ib:
                if x["phash"] is None or y["phash"] is None:
                    continue
                d = P.hamming(P.from_hex(x["phash"]), P.from_hex(y["phash"]))
                if best is None or d < best[0]:
                    best = (d, x, y)
        if best is None:
            return None
        d, x, y = best
        score = max(0.0, 1.0 - d / (2.0 * thr))  # d=0 -> 1, d=próg -> 0.5, d>=2*próg -> 0
        hit = "trafienie" if d <= thr else "brak trafienia"
        expl = (f"pHash 64 b, najlepsza para grafik: odległość Hamminga {d}/64, próg {thr} ({hit}); "
                f"wynik {score:.3f} = 1 - d/(2*próg). Obraz całościowy: nie wykrywa kadrowania.")
        return (score, [ArtifactRef(artifact_id=x["artifact_id"])], [ArtifactRef(artifact_id=y["artifact_id"])], expl)


class KeypointComparator(_Base):
    kind = KIND_KP
    method_id = "perceptual-keypoints-v1"
    spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version="1",
                       description="Punkty ORB + RANSAC (podobieństwo): kadrowanie, skala; zapisuje położenie dopasowań",
                       extras=["perceptual"])

    def _best(self, ia, ib):
        cfg = self.cfg
        best = None
        usable = False
        for x in ia:
            ka = K.KeypointSet.from_json(x)
            for y in ib:
                kb = K.KeypointSet.from_json(y)
                if len(ka.desc) < 3 or len(kb.desc) < 3:
                    continue
                usable = True
                m = K.match(ka, kb, cfg)
                if m is not None and (best is None or m.inliers > best[0].inliers):
                    best = (m, x, y)
        if not usable:
            return None
        mn = cfg["kp_min_inliers"]
        if best is None:
            best_n, x, y, m = 0, ia[0], ib[0], None
        else:
            m, x, y = best
            best_n = m.inliers
        hit = best_n >= mn
        score = min(1.0, best_n / cfg["kp_target_inliers"]) if hit else 0.0
        if m is None or not hit:
            expl = (f"ORB+RANSAC: najlepsza para ma {best_n} zgodnych dopasowań < próg {mn}; brak trafienia "
                    f"(score 0). Wynik 1.0 przy >= {cfg['kp_target_inliers']} inlierach.")
            return (score, [ArtifactRef(artifact_id=x["artifact_id"])], [ArtifactRef(artifact_id=y["artifact_id"])], expl)
        ra = ArtifactRef(artifact_id=x["artifact_id"], locator=ImageRect(
            x=m.rect_a[0], y=m.rect_a[1], width=m.rect_a[2], height=m.rect_a[3]))
        rb = ArtifactRef(artifact_id=y["artifact_id"], locator=ImageRect(
            x=m.rect_b[0], y=m.rect_b[1], width=m.rect_b[2], height=m.rect_b[3]))
        expl = (f"ORB+RANSAC (transformacja podobieństwa): {m.inliers} zgodnych dopasowań z {m.candidates} kandydatów, "
                f"próg {mn}; skala A->B {m.scale:.3f}, obrót {m.angle_deg:.1f} st. Położenie: A "
                f"x={m.rect_a[0]} y={m.rect_a[1]} {m.rect_a[2]}x{m.rect_a[3]}; B x={m.rect_b[0]} y={m.rect_b[1]} "
                f"{m.rect_b[2]}x{m.rect_b[3]}. Wynik {score:.3f} = min(1, inliery/{cfg['kp_target_inliers']}).")
        return (score, [ra], [rb], expl)


def get_adapters() -> list[Any]:
    return [PerceptualExtractor(), PHashComparator(), KeypointComparator()]


__all__ = ["PerceptualExtractor", "PHashComparator", "KeypointComparator", "get_adapters", "group_of",
           "EXTRACTOR_VERSION", "load_config"]
