"""Próbka kontrolna E16: deterministyczne wideo (sceny z ilustracji E09) i audio (segmenty harmoniczne) bez ffmpeg.
Wynik na tych danych jest SYNTETYCZNY i raportowany osobno od danych rzeczywistych."""
from __future__ import annotations

import io
import os
import random
import tempfile
import wave
from typing import Any, Mapping, Optional, Sequence

from methadata.experiments.perceptual.bench import synthetic_illustration

SR = 16000


def synthetic_video(scenes: Sequence[int], scene_s: float = 2.0, fps: int = 10, size=(320, 240),
                    noise: float = 0.0, seed: int = 0, crop: float = 1.0) -> bytes:
    """MP4 (mp4v) złożone ze scen: scena = ilustracja o danym ziarnie przez `scene_s` sekund. Bez ścieżki audio."""
    import cv2
    import numpy as np
    rng = np.random.default_rng(seed)
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "v.mp4")
        wr = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
        if not wr.isOpened():
            raise RuntimeError("cv2.VideoWriter nie zapisuje mp4v w tej instalacji")
        for s in scenes:
            im = np.asarray(synthetic_illustration(s, size).convert("RGB"))[:, :, ::-1]
            if crop < 1.0:  # przybliżenie na środek (kadrowanie), z powrotem do pełnego kadru
                ch, cw = int(im.shape[0] * crop), int(im.shape[1] * crop)
                y0, x0 = (im.shape[0] - ch) // 2, (im.shape[1] - cw) // 2
                im = cv2.resize(np.ascontiguousarray(im[y0:y0 + ch, x0:x0 + cw]), size)
            for _ in range(int(scene_s * fps)):
                f = im if noise <= 0 else np.clip(im + rng.normal(0, noise, im.shape), 0, 255).astype(np.uint8)
                wr.write(np.ascontiguousarray(f))
        wr.release()
        with open(path, "rb") as f:
            return f.read()


def synthetic_audio(content_seed: int, seconds: float = 10.0, voice_seed: Optional[int] = None,
                    segment_s: float = 0.25) -> bytes:
    """WAV 16-bit mono: co `segment_s` nowy ton podstawowy (zależny od content_seed) z harmonicznymi wzmocnionymi
    przez formanty zależne od voice_seed (barwa)."""
    import numpy as np
    vr = random.Random(content_seed if voice_seed is None else voice_seed)
    formants = [vr.uniform(400, 3000) for _ in range(3)]
    cr = random.Random(content_seed)
    out = []
    for _ in range(int(seconds / segment_s)):
        f0 = cr.uniform(110, 400)
        n = int(SR * segment_s)
        t = np.arange(n) / SR
        sig = np.zeros(n)
        for h in range(1, 20):
            fh = f0 * h
            if fh > 4000:
                break
            amp = sum(np.exp(-((fh - f) / 250.0) ** 2) for f in formants) + 0.05
            sig += amp * np.sin(2 * np.pi * fh * t)
        sig *= np.hanning(n) ** 0.3 * 0.2 / max(1e-6, np.abs(sig).max())
        out.append(sig)
    x = np.concatenate(out)
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)
    return b.getvalue()


def wav_slice(wav: bytes, start_s: float, end_s: float) -> bytes:
    with wave.open(io.BytesIO(wav)) as w:
        sr = w.getframerate()
        w.setpos(int(start_s * sr))
        raw = w.readframes(int((end_s - start_s) * sr))
    b = io.BytesIO()
    with wave.open(b, "wb") as o:
        o.setnchannels(1)
        o.setsampwidth(2)
        o.setframerate(sr)
        o.writeframes(raw)
    return b.getvalue()
