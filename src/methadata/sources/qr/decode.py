"""Dekodowanie kodów QR z obrazu przez OpenCV (cv2 importowane leniwie, extra `perceptual`)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


class QrBackendMissing(RuntimeError):
    pass


@dataclass(frozen=True)
class Decoded:
    text: str
    x: int
    y: int
    width: int
    height: int


def _cv():
    try:
        import cv2
        import numpy as np
    except ImportError as e:  # noqa: BLE001
        raise QrBackendMissing("brak opencv-python-headless/numpy (extra `perceptual`)") from e
    return cv2, np


def decode_image(img, cfg: Optional[Mapping[str, Any]] = None) -> list[Decoded]:
    """img: tablica numpy (BGR/GRAY). Prostokąt w pikselach oryginału. Próby: skale, binaryzacja adaptacyjna."""
    cv2, np = _cv()
    scales = (cfg or {}).get("scales", [1.0, 2.0, 0.5])
    max_side = (cfg or {}).get("max_side", 4000)
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h0, w0 = gray.shape[:2]
    found: dict[str, Decoded] = {}
    det = cv2.QRCodeDetector()
    variants = []
    for sc in scales:
        if max(h0, w0) * sc > max_side or max(h0, w0) * sc < 40:
            continue
        g = gray if sc == 1.0 else cv2.resize(gray, None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC if sc > 1 else cv2.INTER_AREA)
        variants.append((sc, g))
        variants.append((sc, cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10)))
    for sc, g in variants:
        try:
            ok, texts, pts, _ = det.detectAndDecodeMulti(g)
        except cv2.error:
            continue
        if not ok or pts is None:
            continue
        for t, p in zip(texts, pts):
            if not t:
                continue
            p = np.asarray(p, dtype=float) / sc
            x0, y0 = max(int(np.floor(p[:, 0].min())), 0), max(int(np.floor(p[:, 1].min())), 0)
            x1, y1 = min(int(np.ceil(p[:, 0].max())), w0), min(int(np.ceil(p[:, 1].max())), h0)
            if x1 > x0 and y1 > y0 and t not in found:
                found[t] = Decoded(t, x0, y0, x1 - x0, y1 - y0)
        if found:
            break
    return list(found.values())


def decode_bytes(data: bytes, cfg: Optional[Mapping[str, Any]] = None) -> list[Decoded]:
    """Bajty obrazu (PNG/JPEG/...). ValueError gdy nie da się zdekodować obrazu."""
    cv2, np = _cv()
    img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("nie rozpoznano obrazu")
    return decode_image(img, cfg)


def video_frames(data: bytes, interval_ms: int, max_frames: int):
    """Generator (czas_ms, obraz BGR) z wideo przez cv2.VideoCapture (bez ffmpeg w PATH).
    ValueError gdy wideo nie da się otworzyć."""
    import tempfile
    cv2, _ = _cv()
    with tempfile.NamedTemporaryFile(suffix=".bin") as f:
        f.write(data)
        f.flush()
        cap = cv2.VideoCapture(f.name)
        try:
            if not cap.isOpened():
                raise ValueError("nie można otworzyć wideo (brak kodeka)")
            fps = cap.get(cv2.CAP_PROP_FPS) or 0
            n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if fps <= 0 or n <= 0:
                raise ValueError("wideo bez fps/liczby klatek")
            dur = int(n / fps * 1000)
            for t in list(range(0, max(dur, 1), interval_ms))[:max_frames]:
                cap.set(cv2.CAP_PROP_POS_MSEC, t)
                ok, fr = cap.read()
                if ok:
                    yield t, fr
        finally:
            cap.release()
