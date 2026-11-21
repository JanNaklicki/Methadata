"""Rozpoznanie formatu i wymiarów z nagłówków (bez zależności zewnętrznych) oraz opcjonalny ffprobe/ffmpeg."""
from __future__ import annotations

import json
import re
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any, Optional

IMAGE_FORMATS = {"png", "jpeg", "gif", "webp", "bmp", "ico", "svg"}
VIDEO_FORMATS = {"mp4", "webm"}
AUDIO_FORMATS = {"mp3", "wav", "ogg", "flac"}

MEDIA_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp",
               "bmp": "image/bmp", "ico": "image/x-icon", "svg": "image/svg+xml", "mp4": "video/mp4",
               "webm": "video/webm", "mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg",
               "flac": "audio/flac"}


@dataclass
class Probe:
    format: Optional[str] = None
    kind: Optional[str] = None  # image | video | audio
    width: Optional[int] = None
    height: Optional[int] = None
    duration_ms: Optional[int] = None
    has_alpha: Optional[bool] = None
    has_exif: Optional[bool] = None
    tracks: list[dict[str, Any]] = field(default_factory=list)  # {"type": "video"|"audio", "index": n}
    source: str = "header"  # header | ffprobe

    def metadata(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if v not in (None, []) and k != "tracks"}
        if self.tracks:
            d["tracks"] = self.tracks
        return d


def sniff(data: bytes) -> Optional[str]:
    h = data[:16]
    if h.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if h.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if h[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return "webp"
    if h[:4] == b"RIFF" and h[8:12] == b"WAVE":
        return "wav"
    if h[:2] == b"BM":
        return "bmp"
    if h[:4] == b"\x00\x00\x01\x00":
        return "ico"
    if h[4:8] == b"ftyp":
        return "mp4"
    if h[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if h[:4] == b"OggS":
        return "ogg"
    if h[:4] == b"fLaC":
        return "flac"
    if h[:3] == b"ID3" or (len(h) > 1 and h[0] == 0xFF and h[1] & 0xE0 == 0xE0):
        return "mp3"
    head = data[:2048].lstrip().lower()
    if b"<svg" in data[:4096].lower() and (head.startswith(b"<") or head.startswith(b"\xef\xbb\xbf<")):
        return "svg"
    return None


def _png(d: bytes, p: Probe):
    if len(d) >= 29 and d[12:16] == b"IHDR":
        p.width, p.height = struct.unpack(">II", d[16:24])
        p.has_alpha = d[25] in (4, 6) or b"tRNS" in d[:4096]


def _jpeg(d: bytes, p: Probe):
    i = 2
    while i + 4 <= len(d):
        if d[i] != 0xFF:
            i += 1
            continue
        m = d[i + 1]
        if m == 0xFF:
            i += 1
            continue
        if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        (ln,) = struct.unpack(">H", d[i + 2:i + 4])
        if m == 0xE1 and d[i + 4:i + 10] == b"Exif\x00\x00":
            p.has_exif = True
        if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            p.height, p.width = struct.unpack(">HH", d[i + 5:i + 9])
            break
        i += 2 + ln
    p.has_alpha = False
    p.has_exif = bool(p.has_exif)


def _gif(d: bytes, p: Probe):
    p.width, p.height = struct.unpack("<HH", d[6:10])


def _webp(d: bytes, p: Probe):
    c = d[12:16]
    if c == b"VP8X":
        p.width = 1 + int.from_bytes(d[24:27], "little")
        p.height = 1 + int.from_bytes(d[27:30], "little")
        p.has_alpha = bool(d[20] & 0x10)
    elif c == b"VP8 ":
        p.width, p.height = struct.unpack("<HH", d[26:30])
        p.width &= 0x3FFF
        p.height &= 0x3FFF
        p.has_alpha = False
    elif c == b"VP8L":
        b = int.from_bytes(d[21:25], "little")
        p.width, p.height = (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
        p.has_alpha = bool((b >> 28) & 1)


def _bmp(d: bytes, p: Probe):
    p.width, p.height = struct.unpack("<ii", d[18:26])
    p.height = abs(p.height)


def _ico(d: bytes, p: Probe):
    n = struct.unpack("<H", d[4:6])[0]
    best = (0, 0)
    for k in range(n):
        e = d[6 + 16 * k:22 + 16 * k]
        if len(e) < 16:
            break
        w, h = (e[0] or 256), (e[1] or 256)
        best = max(best, (w, h))
    p.width, p.height = best if n else (None, None)


def _num(s: Optional[str]) -> Optional[int]:
    m = re.match(r"\s*([0-9.]+)\s*(px)?\s*$", s or "")
    return round(float(m.group(1))) if m else None


def _svg(d: bytes, p: Probe):
    tag = re.search(rb"<svg\b[^>]*>", d[:8192], re.I | re.S)
    if not tag:
        return
    t = tag.group(0).decode("utf-8", "replace")

    def attr(n):
        m = re.search(r"\b" + n + r"\s*=\s*[\"']([^\"']*)[\"']", t)
        return m.group(1) if m else None
    p.width, p.height = _num(attr("width")), _num(attr("height"))
    vb = attr("viewBox")
    if vb and (p.width is None or p.height is None):
        parts = re.split(r"[ ,]+", vb.strip())
        if len(parts) == 4:
            try:
                p.width, p.height = round(float(parts[2])), round(float(parts[3]))
            except ValueError:
                pass
    p.has_alpha = True


def _boxes(d: bytes, start: int, end: int):
    i = start
    while i + 8 <= end:
        size, typ = struct.unpack(">I4s", d[i:i + 8])
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", d[i + 8:i + 16])[0]
            hdr = 16
        elif size == 0:
            size = end - i
        if size < hdr:
            return
        yield typ, i + hdr, min(i + size, end)
        i += size


def _mp4(d: bytes, p: Probe):
    for typ, s, e in _boxes(d, 0, len(d)):
        if typ != b"moov":
            continue
        for t2, s2, e2 in _boxes(d, s, e):
            if t2 == b"mvhd":
                ver = d[s2]
                if ver == 1:
                    ts, dur = struct.unpack(">IQ", d[s2 + 20:s2 + 32])
                else:
                    ts, dur = struct.unpack(">II", d[s2 + 12:s2 + 20])
                if ts:
                    p.duration_ms = round(dur * 1000 / ts)
            elif t2 == b"trak":
                tr: dict[str, Any] = {"index": len(p.tracks)}
                w = h = 0
                for t3, s3, e3 in _boxes(d, s2, e2):
                    if t3 == b"tkhd":
                        off = 76 if d[s3] == 0 else 88
                        w, h = struct.unpack(">II", d[s3 + off:s3 + off + 8])
                    elif t3 == b"mdia":
                        for t4, s4, e4 in _boxes(d, s3, e3):
                            if t4 == b"hdlr":
                                hd = d[s4 + 8:s4 + 12]
                                tr["type"] = {b"vide": "video", b"soun": "audio"}.get(hd, hd.decode("latin1"))
                if tr.get("type") == "video" and w and p.width is None:
                    p.width, p.height = w >> 16, h >> 16
                if "type" in tr:
                    p.tracks.append(tr)


_PARSERS = {"png": _png, "jpeg": _jpeg, "gif": _gif, "webp": _webp, "bmp": _bmp, "ico": _ico, "svg": _svg,
            "mp4": _mp4}


def ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def probe_bytes(data: bytes) -> Probe:
    fmt = sniff(data)
    p = Probe(format=fmt)
    if fmt is None:
        return p
    p.kind = "image" if fmt in IMAGE_FORMATS else "video" if fmt in VIDEO_FORMATS else "audio"
    parser = _PARSERS.get(fmt)
    if parser:
        try:
            parser(data, p)
        except (struct.error, IndexError, ValueError):
            pass
    if fmt == "mp4" and p.tracks and not any(t["type"] == "video" for t in p.tracks):
        p.kind = "audio"
    needs = p.kind in ("video", "audio") and (p.duration_ms is None or not p.tracks)
    if needs and ffprobe_available():
        _ffprobe(data, p)
    return p


def _ffprobe(data: bytes, p: Probe):
    with tempfile.NamedTemporaryFile(suffix="." + (p.format or "bin")) as f:
        f.write(data)
        f.flush()
        try:
            out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                                  f.name], capture_output=True, timeout=60, check=True).stdout
            j = json.loads(out)
        except (subprocess.SubprocessError, OSError, ValueError):
            return
    p.source = "ffprobe"
    dur = j.get("format", {}).get("duration")
    if dur:
        p.duration_ms = round(float(dur) * 1000)
    p.tracks = []
    for s in j.get("streams", []):
        t = {"index": s.get("index", len(p.tracks)), "type": s.get("codec_type"), "codec": s.get("codec_name")}
        p.tracks.append({k: v for k, v in t.items() if v is not None})
        if s.get("codec_type") == "video" and p.width is None:
            p.width, p.height = s.get("width"), s.get("height")


def sample_times(duration_ms: int, interval_ms: int, max_frames: int) -> list[int]:
    """Deterministyczne znaczniki czasu klatek: 0, interval, 2*interval ... < duration, maks. max_frames."""
    if duration_ms <= 0 or interval_ms <= 0 or max_frames <= 0:
        return []
    return [t for t in range(0, duration_ms, interval_ms)][:max_frames]


def grab_frame(data: bytes, fmt: str, t_ms: int) -> Optional[bytes]:
    """Klatka PNG w chwili t_ms przez ffmpeg; None gdy brak ffmpeg lub błąd."""
    if not ffmpeg_available():
        return None
    with tempfile.NamedTemporaryFile(suffix="." + fmt) as f:
        f.write(data)
        f.flush()
        try:
            return subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t_ms / 1000:.3f}", "-i", f.name, "-frames:v", "1",
                                   "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, timeout=60,
                                  check=True).stdout or None
        except (subprocess.SubprocessError, OSError):
            return None
