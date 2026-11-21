"""Odczyt manifestu C2PA z bajtów obrazu/filmu.

Ścieżka główna: biblioteka c2pa-python (extra `provenance`, importowana leniwie) z pełną walidacją.
Ścieżka zapasowa (bez biblioteki): ograniczony parser JUMBF dla JPEG (APP11) i PNG (caBX) — wykrywa manifest,
etykiety manifestów (aktywny i przodkowie), NIE waliduje podpisu ani skrótów (validation_state="Unverified").
"""
from __future__ import annotations

import io
import struct
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Provenance:
    present: bool
    engine: str                      # "c2pa-python" | "jumbf-fallback"
    engine_version: str
    sdk_version: Optional[str] = None
    spec_version: Optional[str] = None
    validation_state: Optional[str] = None   # Valid | Trusted | Invalid | Unverified (fallback)
    failure_codes: list[str] = field(default_factory=list)
    active_manifest: Optional[str] = None
    ancestor_manifests: list[str] = field(default_factory=list)  # etykiety manifestów przodków
    instance_id: Optional[str] = None
    title: Optional[str] = None
    claim_generator: Optional[str] = None
    signer: dict[str, Any] = field(default_factory=dict)
    signed_time: Optional[str] = None
    actions: list[str] = field(default_factory=list)
    ingredients: list[dict[str, Any]] = field(default_factory=list)
    detail: Optional[str] = None


class Unsupported(Exception):
    pass


class Unreadable(Exception):
    pass


def sniff_mime(data: bytes) -> Optional[str]:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[4:8] == b"ftyp":
        return "video/mp4"
    return None


def c2pa_available() -> bool:
    try:
        import c2pa  # noqa: F401
        return True
    except Exception:
        return False


def validator_info() -> dict[str, Optional[str]]:
    """Wersja walidatora: biblioteka, rdzeń c2pa-rs, wersja specyfikacji (z wyniku walidacji)."""
    try:
        import c2pa
        return {"engine": "c2pa-python", "library": getattr(c2pa, "__version__", None), "sdk": c2pa.sdk_version()}
    except Exception:
        return {"engine": "jumbf-fallback", "library": None, "sdk": None}


# ------------------------------------------------------------------ ścieżka główna

def _from_lib(data: bytes, mime: str) -> Provenance:
    import c2pa
    ver = getattr(c2pa, "__version__", "?")
    sdk = c2pa.sdk_version()
    base = dict(engine="c2pa-python", engine_version=ver, sdk_version=sdk)
    try:
        reader = c2pa.Reader(mime, io.BytesIO(data))
    except Exception as e:  # typy błędów c2pa: ManifestNotFound, NotSupported, Other...
        kind = type(e).__name__
        if "ManifestNotFound" in kind:
            return Provenance(present=False, detail="brak manifestu C2PA", **base)
        if "NotSupported" in kind:
            raise Unsupported(str(e))
        raise Unreadable(str(e))
    with reader:
        import json
        doc = json.loads(reader.json())
        state = reader.get_validation_state()
    active = doc.get("active_manifest")
    manifests = doc.get("manifests") or {}
    m = manifests.get(active) or {}
    results = doc.get("validation_results") or {}
    fails = [f.get("code") for f in (results.get("activeManifest") or {}).get("failure", []) if f.get("code")]
    gens = m.get("claim_generator_info") or []
    gen = None
    if gens and isinstance(gens[0], dict):
        gen = " ".join(str(x) for x in (gens[0].get("name"), gens[0].get("version")) if x) or None
    elif m.get("claim_generator"):
        gen = str(m["claim_generator"])
    sig = m.get("signature_info") or {}
    actions: list[str] = []
    for a in m.get("assertions") or []:
        if str(a.get("label", "")).startswith("c2pa.actions"):
            for act in (a.get("data") or {}).get("actions", []):
                name = act.get("action")
                if name:
                    dst = act.get("digitalSourceType")
                    actions.append(f"{name}|{dst.rsplit('/', 1)[-1]}" if dst else name)
    ings = []
    for ing in m.get("ingredients") or []:
        ings.append({"title": ing.get("title"), "relationship": ing.get("relationship"),
                     "manifest_label": ing.get("active_manifest"), "instance_id": ing.get("instance_id"),
                     "document_id": ing.get("document_id")})
    return Provenance(
        present=True, validation_state=state, failure_codes=fails, spec_version=results.get("specVersion"),
        active_manifest=active, ancestor_manifests=sorted(k for k in manifests if k != active),
        instance_id=m.get("instance_id"), title=m.get("title"), claim_generator=gen,
        signer={k: sig[k] for k in ("alg", "issuer", "common_name", "cert_serial_number") if k in sig},
        signed_time=sig.get("time"), actions=actions, ingredients=ings, **base)


# ------------------------------------------------------------------ ścieżka zapasowa (JUMBF)

def _jpeg_jumbf(data: bytes) -> bytes:
    """Składa JUMBF z segmentów APP11 ('JP' + En + Z); pierwszy segment niesie nagłówek boksu."""
    segs: dict[int, dict[int, bytes]] = {}
    i = 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        marker = data[i + 1]
        if marker == 0xDA or marker == 0xD9:
            break
        ln = struct.unpack(">H", data[i + 2:i + 4])[0]
        body = data[i + 4:i + 2 + ln]
        if marker == 0xEB and body[:2] == b"JP" and len(body) >= 8:
            en, z = struct.unpack(">HI", body[2:8])
            segs.setdefault(en, {})[z] = body[8:]
        i += 2 + ln
    out = b""
    for en in sorted(segs):
        parts = segs[en]
        for n, z in enumerate(sorted(parts)):
            out += parts[z] if n == 0 else parts[z][8:]  # kolejne segmenty powtarzają LBox+TBox
    return out


def _png_jumbf(data: bytes) -> bytes:
    i, out = 8, b""
    while i + 8 <= len(data):
        ln, typ = struct.unpack(">I4s", data[i:i + 8])
        if typ == b"caBX":
            out += data[i + 8:i + 8 + ln]
        i += 12 + ln
    return out


def _boxes(buf: bytes, start: int = 0, end: Optional[int] = None):
    end = len(buf) if end is None else end
    i = start
    while i + 8 <= end:
        ln, typ = struct.unpack(">I4s", buf[i:i + 8])
        hdr = 8
        if ln == 1 and i + 16 <= end:
            ln, hdr = struct.unpack(">Q", buf[i + 8:i + 16])[0], 16
        elif ln == 0:
            ln = end - i
        if ln < hdr or i + ln > end:
            return
        yield typ, i + hdr, i + ln
        i += ln


def _label(buf: bytes, s: int, e: int) -> Optional[str]:
    for typ, a, b in _boxes(buf, s, e):
        if typ == b"jumd" and b - a > 17:
            raw = buf[a + 17:b]  # 16 B UUID + 1 B toggles, potem etykieta zakończona zerem
            return raw.split(b"\x00", 1)[0].decode("utf-8", "replace")
    return None


def _from_jumbf(data: bytes, mime: str) -> Provenance:
    base = dict(engine="jumbf-fallback", engine_version="1")
    if mime == "image/jpeg":
        jumbf = _jpeg_jumbf(data)
    elif mime == "image/png":
        jumbf = _png_jumbf(data)
    else:
        raise Unsupported(f"zapasowy parser JUMBF nie obsługuje {mime}; zainstaluj extra 'provenance'")
    if not jumbf:
        return Provenance(present=False, detail="brak manifestu C2PA (brak boksów JUMBF)", **base)
    labels: list[str] = []
    for typ, s, e in _boxes(jumbf):
        if typ == b"jumb" and _label(jumbf, s, e) == "c2pa":
            for t2, s2, e2 in _boxes(jumbf, s, e):
                if t2 == b"jumb":
                    lab = _label(jumbf, s2, e2)
                    if lab:
                        labels.append(lab)
    if not labels:
        return Provenance(present=False, detail="JUMBF bez magazynu C2PA", **base)
    active = labels[-1]  # aktywny manifest jest ostatnim w magazynie
    return Provenance(present=True, validation_state="Unverified", active_manifest=active,
                      ancestor_manifests=sorted(labels[:-1]),
                      detail="ograniczony parser JUMBF: bez walidacji podpisu i skrótów", **base)


def read_provenance(data: bytes, mime: Optional[str] = None, *, force_fallback: bool = False) -> Provenance:
    mime = mime or sniff_mime(data)
    if mime is None:
        raise Unsupported("nierozpoznany format pliku")
    if not force_fallback and c2pa_available():
        return _from_lib(data, mime)
    return _from_jumbf(data, mime)
