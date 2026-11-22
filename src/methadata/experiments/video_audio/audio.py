"""Audio: dekodowanie WAV (stdlib), odcisk spektralny, profil głosu (pomocniczy) i opcjonalny backend transkrypcji.

Bez ffmpeg dekodujemy tylko WAV. Ścieżki audio w MP4/WebM wymagają ffmpeg (opcjonalnie, wykrywany w PATH);
bez niego odcisk audio ma status unsupported. numpy importowane leniwie."""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import wave
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

N_BANDS = 17            # pasm -> 16 bitów na ramkę
VOICE_BANDS = 24
Transcriber = Callable[[bytes], Sequence[Mapping[str, Any]]]  # WAV -> [{"start_ms","end_ms","text"}]
SpeakerEmbedder = Callable[[Any, int], Optional[dict[str, Any]]]  # (samples, sr) -> {"mean":[...], "voiced_s":..., "backend":...}


class AudioUnsupported(Exception):
    pass


def decode_wav(data: bytes, max_seconds: float = 120.0):
    """(próbki mono float32 [-1,1], sr). Rzuca AudioUnsupported."""
    try:
        import numpy as np
    except ImportError as e:
        raise AudioUnsupported(f"brak biblioteki ({e.name}); zainstaluj extra perceptual") from e
    try:
        with wave.open(io.BytesIO(data)) as w:
            ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(min(n, int(max_seconds * sr)))
    except (wave.Error, EOFError) as e:
        raise AudioUnsupported(f"WAV nieobsługiwany: {e}") from e
    if sw == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sw == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sw == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise AudioUnsupported(f"WAV {8 * sw}-bitowy nieobsługiwany")
    if ch > 1:
        x = x[: len(x) // ch * ch].reshape(-1, ch).mean(axis=1)
    if sr <= 0 or len(x) == 0:
        raise AudioUnsupported("pusty WAV")
    return x, sr


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def to_wav(data: bytes, fmt: str, max_seconds: float = 120.0) -> Optional[bytes]:
    """WAV 16 kHz mono z kontenera przez ffmpeg; None gdy brak ffmpeg lub ścieżki."""
    if fmt == "wav":
        return data
    if not ffmpeg_available():
        return None
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "in." + fmt)
        with open(src, "wb") as f:
            f.write(data)
        try:
            r = subprocess.run(["ffmpeg", "-v", "error", "-i", src, "-vn", "-ac", "1", "-ar", "16000",
                                "-t", str(max_seconds), "-f", "wav", "-"], capture_output=True, timeout=120, check=True)
        except (subprocess.SubprocessError, OSError):
            return None
    return r.stdout or None


def _stft_power(x, sr: int, frame_s: float, hop_s: float):
    import numpy as np
    fl, hop = int(sr * frame_s), int(sr * hop_s)
    if len(x) < fl or hop <= 0:
        return np.zeros((0, 1)), np.zeros(0), fl
    nfft = 1 << (fl - 1).bit_length()
    win = np.hanning(fl).astype(np.float32)
    starts = np.arange(0, len(x) - fl + 1, hop)
    idx = starts[:, None] + np.arange(fl)[None, :]
    seg = x[idx]
    rms = np.sqrt((seg ** 2).mean(axis=1))
    return np.abs(np.fft.rfft(seg * win, n=nfft, axis=1)) ** 2, rms, nfft


def _band_energy(power, sr: int, nfft: int, nb: int, lo: float, hi: float):
    import numpy as np
    hi = min(hi, sr / 2 * 0.95)
    edges = np.geomspace(lo, hi, nb + 1)
    freqs = np.fft.rfftfreq(nfft, 1.0 / sr)
    out = np.empty((power.shape[0], nb))
    for b in range(nb):
        m = (freqs >= edges[b]) & (freqs < edges[b + 1])
        out[:, b] = np.log(power[:, m].sum(axis=1) + 1e-10) if m.any() else -23.0
    return out


def fingerprint(x, sr: int, cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Odcisk: 16 bitów na ramkę (znak zmiany różnicy energii sąsiednich pasm, jak Haitsma-Kalker).
    {"hop_ms","frame_ms","bits":[int],"valid":"0101.."}; ramki ciche są nieważne."""
    import numpy as np
    hop_s = cfg["audio_hop_ms"] / 1000.0
    power, rms, nfft = _stft_power(x, sr, hop_s * 2, hop_s)
    base = {"hop_ms": int(cfg["audio_hop_ms"]), "frame_ms": int(hop_s * 2000)}
    if power.shape[0] < 2:
        return {**base, "bits": [], "valid": ""}
    e = _band_energy(power, sr, nfft, N_BANDS, 300.0, 3400.0)
    d = e[:, :-1] - e[:, 1:]
    bits = (d[1:] - d[:-1]) > 0
    vals = (bits * (1 << np.arange(N_BANDS - 1))).sum(axis=1)
    ok = (rms[1:] > cfg["audio_silence_rms"]) & (rms[:-1] > cfg["audio_silence_rms"])
    return {**base, "bits": [int(v) for v in vals], "valid": "".join("1" if v else "0" for v in ok)}


def voice_profile(x, sr: int, cfg: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """Pomocniczy profil głosu: średnia i odchylenie logarytmicznego widma pasmowego głośnych ramek.
    Nie jest modelem mówcy (grube porównanie barwy); None gdy za mało mowy."""
    power, rms, nfft = _stft_power(x, sr, 0.032, 0.016)
    if power.shape[0] == 0:
        return None
    voiced = rms > max(cfg["audio_silence_rms"] * 5, 0.01)
    if voiced.sum() * 0.016 < cfg["voice_min_voiced_s"]:
        return None
    e = _band_energy(power[voiced], sr, nfft, VOICE_BANDS, 80.0, 4000.0)
    e = e - e.mean(axis=1, keepdims=True)  # niezależność od głośności
    return {"mean": [round(float(v), 4) for v in e.mean(axis=0)], "std": [round(float(v), 4) for v in e.std(axis=0)],
            "voiced_s": round(float(voiced.sum() * 0.016), 2), "backend": "spectral-envelope-v1"}


@lru_cache(maxsize=2)
def _load_whisper(model: str, cache_dir: Optional[str]):
    from faster_whisper import WhisperModel
    return WhisperModel(model, download_root=cache_dir)


def faster_whisper_transcriber(model: str = "tiny", cache_dir: Optional[str] = None) -> Optional[Transcriber]:
    """Opcjonalny backend (faster-whisper); None gdy brak biblioteki. Wagi do katalogu cache poza repo."""
    try:
        m = _load_whisper(model, cache_dir)
    except Exception:  # noqa: BLE001
        return None

    def run(wav: bytes):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.wav")
            with open(p, "wb") as f:
                f.write(wav)
            segs, _ = m.transcribe(p)
            return [{"start_ms": int(s.start * 1000), "end_ms": int(s.end * 1000), "text": s.text.strip()}
                    for s in segs]
    return run


@lru_cache(maxsize=2)
def _load_speechbrain(source: str, savedir: str):
    from speechbrain.inference.speaker import EncoderClassifier
    return EncoderClassifier.from_hparams(source=source, savedir=savedir)


def speechbrain_speaker_embedder(
    source: str = "speechbrain/spkrec-ecapa-voxceleb",
    cache_dir: Optional[str] = None,
) -> Optional[SpeakerEmbedder]:
    """Opcjonalny backend mówcy (SpeechBrain ECAPA-TDNN, 192-dim); None gdy brak biblioteki lub wag."""
    savedir = cache_dir or os.environ.get("METHADATA_MODEL_CACHE") or str(Path.home() / ".cache" / "huggingface" / "hub")
    target = str(Path(savedir) / "speechbrain-ecapa")
    try:
        clf = _load_speechbrain(source, target)
    except Exception:  # noqa: BLE001
        return None

    def run(x, sr: int) -> Optional[dict[str, Any]]:
        import numpy as np
        import torch

        if sr <= 0 or len(x) < int(sr * 1.0):
            return None
        # Wylicz orientacyjny czas głośnych ramek (jak w voice_profile).
        frame = max(1, int(sr * 0.032))
        hop = max(1, int(sr * 0.016))
        if len(x) >= frame:
            starts = np.arange(0, len(x) - frame + 1, hop)
            rms = np.sqrt(np.array([(x[s:s + frame] ** 2).mean() for s in starts], dtype=np.float32))
            voiced_s = float((rms > 0.01).sum() * 0.016)
        else:
            voiced_s = float(len(x) / sr)
        if voiced_s < 1.0:
            return None
        samples = x.astype(np.float32, copy=False)
        if sr != 16000:
            new_len = max(1, int(round(len(samples) * 16000 / sr)))
            samples = np.interp(
                np.linspace(0, len(samples) - 1, new_len, dtype=np.float32),
                np.arange(len(samples), dtype=np.float32),
                samples,
            ).astype(np.float32)
        wav_tensor = torch.from_numpy(samples).unsqueeze(0)
        with torch.no_grad():
            emb = clf.encode_batch(wav_tensor).squeeze().detach().cpu().numpy().astype(np.float32)
        norm = float(np.linalg.norm(emb))
        if norm > 0:
            emb = emb / norm
        return {
            "mean": [round(float(v), 5) for v in emb.tolist()],
            "voiced_s": round(voiced_s, 2),
            "backend": "speechbrain-ecapa-voxceleb",
        }

    return run
