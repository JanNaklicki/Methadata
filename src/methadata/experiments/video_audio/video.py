"""Klatki kluczowe i ujęcia wideo przez cv2.VideoCapture (bez ffmpeg). numpy/cv2/PIL importowane leniwie."""
from __future__ import annotations

import os
import tempfile
from typing import Any, Mapping, Optional

from methadata.experiments.perceptual import phash as PH

EXT = {"mp4": ".mp4", "webm": ".webm"}


class VideoUnsupported(Exception):
    """Brak biblioteki lub dekodera; komunikat trafia do ExtractionStatus.detail."""


def read_frames(data: bytes, fmt: str, cfg: Mapping[str, Any]) -> dict[str, Any]:
    """{"width","height","fps","duration_ms","frames":[{"time_ms","phash"|None}]}; rzuca VideoUnsupported."""
    try:
        import cv2
        import numpy as np
        from PIL import Image
    except ImportError as e:
        raise VideoUnsupported(f"brak biblioteki ({e.name}); zainstaluj extra perceptual") from e
    interval = int(cfg["frame_interval_ms"])
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "v" + EXT.get(fmt, ".bin"))
        with open(path, "wb") as f:
            f.write(data)
        cap = cv2.VideoCapture(path)
        try:
            if not cap.isOpened():
                raise VideoUnsupported("cv2 nie otworzył pliku (kodek bez obsługi w tej instalacji)")
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            if fps <= 0 or w <= 0 or h <= 0:
                raise VideoUnsupported("nie ustalono fps lub wymiarów wideo")
            frames: list[dict[str, Any]] = []
            nxt, idx = 0.0, 0
            while len(frames) < int(cfg["max_frames"]):
                ok = cap.grab()
                if not ok:
                    break
                t = idx * 1000.0 / fps
                if t + 1e-6 >= nxt:
                    ok, fr = cap.retrieve()
                    if not ok:
                        break
                    g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY) if fr.ndim == 3 else fr
                    ph = PH.phash_gray(Image.fromarray(np.ascontiguousarray(g)), float(cfg["flat_std"]))
                    frames.append({"time_ms": int(round(t)), "phash": None if ph is None else PH.to_hex(ph)})
                    nxt += interval
                idx += 1
            total = idx if idx else n
            duration = int(round(total * 1000.0 / fps))
        finally:
            cap.release()
    return {"width": w, "height": h, "fps": round(fps, 3), "duration_ms": duration, "frames": frames}


def shots(frames: list[dict[str, Any]], duration_ms: int, cfg: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Ujęcia z cięć między sąsiednimi próbkami; hash ujęcia = klatka ze środka (None gdy płaska)."""
    if not frames:
        return []
    cut = int(cfg["shot_cut_threshold"])
    groups: list[list[dict[str, Any]]] = [[frames[0]]]
    for prev, cur in zip(frames, frames[1:]):
        a, b = prev["phash"], cur["phash"]
        if a is None or b is None:
            is_cut = (a is None) != (b is None)
        else:
            is_cut = PH.hamming(PH.from_hex(a), PH.from_hex(b)) >= cut
        if is_cut:
            groups.append([cur])
        else:
            groups[-1].append(cur)
    out = []
    for k, g in enumerate(groups):
        start = g[0]["time_ms"]
        end = groups[k + 1][0]["time_ms"] if k + 1 < len(groups) else duration_ms
        out.append({"start_ms": start, "end_ms": max(end, start + 1), "phash": g[len(g) // 2]["phash"],
                    "frames": len(g)})
    return out
