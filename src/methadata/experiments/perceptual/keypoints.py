"""Lokalne punkty charakterystyczne (ORB) + weryfikacja geometryczna RANSAC (transformacja podobieństwa).
Odporność na kadrowanie, skalowanie i obrót; pHash zawodzi przy kadrowaniu."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from .config import DEFAULTS


@dataclass
class KeypointSet:
    width: int
    height: int
    scale: float                 # skala robocza / oryginał
    pts: np.ndarray              # (n,2) float32, współrzędne w pikselach ORYGINAŁU
    desc: np.ndarray             # (n,32) uint8

    def to_json(self) -> dict[str, Any]:
        return {"width": self.width, "height": self.height, "scale": round(self.scale, 6),
                "pts": [[round(float(x), 1), round(float(y), 1)] for x, y in self.pts],
                "desc": self.desc.tobytes().hex()}

    @classmethod
    def from_json(cls, v: Mapping[str, Any]) -> "KeypointSet":
        import numpy as np
        desc = np.frombuffer(bytes.fromhex(v["desc"]), dtype=np.uint8).reshape(-1, 32) if v["desc"] else np.zeros((0, 32), np.uint8)
        pts = np.array(v["pts"], dtype=np.float32).reshape(-1, 2)
        return cls(int(v["width"]), int(v["height"]), float(v["scale"]), pts, desc)


def detect(gray, cfg: Mapping[str, Any] = DEFAULTS) -> Optional[KeypointSet]:
    import cv2
    import numpy as np
    from PIL import Image
    w, h = gray.size
    side = max(w, h)
    k = min(1.0, cfg["kp_work_side"] / side)
    work = gray if k == 1.0 else gray.resize((max(1, round(w * k)), max(1, round(h * k))), Image.Resampling.LANCZOS)
    arr = np.asarray(work, dtype=np.uint8)
    if min(arr.shape) < 32:
        return KeypointSet(w, h, k, np.zeros((0, 2), np.float32), np.zeros((0, 32), np.uint8))
    orb = cv2.ORB_create(nfeatures=int(cfg["kp_max_features"]), edgeThreshold=15, patchSize=31)
    kps, desc = orb.detectAndCompute(arr, None)
    if not kps or desc is None:
        return KeypointSet(w, h, k, np.zeros((0, 2), np.float32), np.zeros((0, 32), np.uint8))
    pts = np.array([kp.pt for kp in kps], dtype=np.float32) / k
    return KeypointSet(w, h, k, pts, desc)


@dataclass
class GeoMatch:
    inliers: int
    candidates: int              # dopasowania po teście Lowe'a, przed RANSAC
    scale: float                 # skala transformacji A -> B
    angle_deg: float
    rect_a: tuple[int, int, int, int]   # x, y, w, h otoczki inlierów w A (piksele oryginału)
    rect_b: tuple[int, int, int, int]
    pairs: list[tuple[tuple[float, float], tuple[float, float]]] = field(default_factory=list)


def _rect(pts, w: int, h: int) -> tuple[int, int, int, int]:
    import numpy as np
    x0, y0 = np.floor(pts.min(axis=0)).astype(int)
    x1, y1 = np.ceil(pts.max(axis=0)).astype(int)
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(w, int(x1)), min(h, int(y1))
    return x0, y0, max(1, x1 - x0), max(1, y1 - y0)


def match(a: KeypointSet, b: KeypointSet, cfg: Mapping[str, Any] = DEFAULTS) -> Optional[GeoMatch]:
    """None, gdy brak wystarczających dopasowań do estymacji (< 3). Wynik z inliers < min_inliers
    wywołujący decyduje jako brak trafienia."""
    import cv2
    import numpy as np
    if len(a.desc) < 3 or len(b.desc) < 3:
        return None
    bf = cv2.BFMatcher(cv2.NORM_HAMMING)
    knn = bf.knnMatch(a.desc, b.desc, k=2)
    good = [m[0] for m in knn if len(m) == 2 and m[0].distance < cfg["kp_ratio"] * m[1].distance]
    if len(good) < 3:
        return None
    pa = np.float32([a.pts[m.queryIdx] for m in good])
    pb = np.float32([b.pts[m.trainIdx] for m in good])
    thr = float(cfg["kp_ransac_px"]) / max(1e-6, min(a.scale, b.scale))
    mat, mask = cv2.estimateAffinePartial2D(pa, pb, method=cv2.RANSAC, ransacReprojThreshold=thr, maxIters=2000,
                                            confidence=0.995, refineIters=10)
    if mat is None or mask is None:
        return None
    sc = float(np.hypot(mat[0, 0], mat[1, 0]))
    lo, hi = cfg["kp_scale_range"]
    keep = mask.ravel().astype(bool)
    n = int(keep.sum())
    if not (lo <= sc <= hi):
        n = 0
    ia, ib = pa[keep], pb[keep]
    if n == 0:
        return GeoMatch(0, len(good), sc, 0.0, (0, 0, 1, 1), (0, 0, 1, 1))
    ang = float(np.degrees(np.arctan2(mat[1, 0], mat[0, 0])))
    return GeoMatch(n, len(good), sc, ang, _rect(ia, a.width, a.height), _rect(ib, b.width, b.height),
                    [((float(p[0]), float(p[1])), (float(q[0]), float(q[1]))) for p, q in zip(ia[:50], ib[:50])])
