"""Rozpoznawanie typu pliku z zawartości (nagłówek), z rozszerzeniem jedynie jako wskazówką."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

HEAD_BYTES = 65536

MEDIA = {
    "png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml",
    "bmp": "image/bmp", "ico": "image/x-icon", "avif": "image/avif",
    "pdf": "application/pdf", "zip": "application/zip", "har": "application/json",
    "json": "application/json", "html": "text/html", "mhtml": "multipart/related",
    "css": "text/css", "js": "text/javascript", "text": "text/plain",
    "mp4": "video/mp4", "webm": "video/webm", "mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg",
    "woff": "font/woff", "woff2": "font/woff2", "gzip": "application/gzip", "unknown": "application/octet-stream",
}
IMAGE_KINDS = {"png", "jpeg", "gif", "webp", "svg", "bmp", "ico", "avif"}
VIDEO_KINDS = {"mp4", "webm"}
AUDIO_KINDS = {"mp3", "wav", "ogg"}
DOC_KINDS = {"pdf", "json", "text", "mhtml"}

_EXT_KIND = {
    "png": "png", "jpg": "jpeg", "jpeg": "jpeg", "gif": "gif", "webp": "webp", "svg": "svg", "bmp": "bmp",
    "ico": "ico", "avif": "avif", "pdf": "pdf", "zip": "zip", "har": "har", "json": "json", "html": "html",
    "htm": "html", "xhtml": "html", "mhtml": "mhtml", "mht": "mhtml", "css": "css", "js": "js", "mjs": "js",
    "txt": "text", "md": "text", "mp4": "mp4", "webm": "webm", "mp3": "mp3", "wav": "wav", "ogg": "ogg",
    "woff": "woff", "woff2": "woff2",
}
_EXT_RE = re.compile(r"\.([A-Za-z0-9]{2,5})(?=$|[@.\-_~])")


@dataclass(frozen=True)
class Detected:
    kind: str          # np. "html", "png", "unknown"
    media_type: str
    by: str            # "content" | "extension" | "fallback"
    ext_hint: str | None = None
    note: str | None = None


def extension_hint(name: str) -> str | None:
    """Ostatnie znane rozszerzenie w nazwie, także w `x.js.pobrane` czy `hls.js@latest`."""
    found = None
    for m in _EXT_RE.finditer(name.rsplit("/", 1)[-1]):
        if m.group(1).lower() in _EXT_KIND:
            found = m.group(1).lower()
    return found


def _text(head: bytes) -> str | None:
    if b"\x00" in head:
        return None
    for enc in ("utf-8", "utf-16"):
        try:
            if enc == "utf-16" and not head.startswith((b"\xff\xfe", b"\xfe\xff")):
                continue
            return head.decode(enc, errors="strict" if enc == "utf-16" else "ignore")
        except UnicodeError:
            continue
    return None


_JS_TOKENS = re.compile(r"\bfunction\b|\bvar\s|\bconst\s|\blet\s|=>|\bwindow\.|\bdocument\.|\(function|!function|\brequire\(|\bexport\s|\bimport\s")
_CSS_RULE = re.compile(r"(^|[}\s;])[@.#:\w\[\]\-*>+~, =\"'()]+\{[^{}]*:[^{}]*\}")


def sniff(head: bytes, name: str = "", size: int | None = None) -> Detected:
    hint = extension_hint(name)

    def det(kind: str, by: str = "content", note: str | None = None) -> Detected:
        return Detected(kind, MEDIA[kind], by, hint, note)

    h = head
    if h.startswith(b"\x89PNG\r\n\x1a\n"):
        return det("png")
    if h.startswith(b"\xff\xd8\xff"):
        return det("jpeg")
    if h[:6] in (b"GIF87a", b"GIF89a"):
        return det("gif")
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return det("webp")
    if h[:4] == b"RIFF" and h[8:12] == b"WAVE":
        return det("wav")
    if h[:2] == b"BM" and hint == "bmp":
        return det("bmp")
    if h[4:8] == b"ftyp":
        return det("avif") if h[8:12] in (b"avif", b"avis") else det("mp4")
    if h[:4] == b"\x1a\x45\xdf\xa3":
        return det("webm")
    if h[:3] == b"ID3" or (len(h) > 1 and h[0] == 0xFF and h[1] & 0xE0 == 0xE0 and hint == "mp3"):
        return det("mp3")
    if h[:4] == b"OggS":
        return det("ogg")
    if h[:4] == b"wOFF":
        return det("woff")
    if h[:4] == b"wOF2":
        return det("woff2")
    if h[:5] == b"%PDF-":
        return det("pdf")
    if h[:4] == b"PK\x03\x04" or h[:4] == b"PK\x05\x06":
        return det("zip")
    if h[:2] == b"\x1f\x8b":
        return det("gzip")
    if h[:4] == b"\x00\x00\x01\x00":
        return det("ico")

    txt = _text(h)
    if txt is None:
        if hint in _EXT_KIND and _EXT_KIND[hint] not in ("html", "css", "js", "json", "har", "text", "svg", "mhtml"):
            return det(_EXT_KIND[hint], "extension", "zawartość nie potwierdza rozszerzenia")
        return det("unknown", "fallback", "nierozpoznana zawartość binarna")
    t = txt.lstrip("﻿ \t\r\n")
    low = t[:HEAD_BYTES].lower()

    if re.match(r"(<\?xml[^>]*>\s*)?(<!--.*?-->\s*)*(<!doctype svg[^>]*>\s*)?<svg\b", low, re.S):
        return det("svg")
    if re.match(r"(from|mime-version|date|subject|content-type):", low) and "multipart/related" in low[:4096]:
        return det("mhtml")
    starts_tag = low.startswith("<")
    if re.match(r"<!doctype\s+html", low) or (starts_tag and re.search(r"<(html|head|body)[\s>]", low)) or \
            (starts_tag and len(re.findall(r"<(meta|link|div|script|title|p|a|img|span)\b", low)) >= 2) or \
            re.match(r"(<!--.*?-->\s*)+<(html|head|body|!doctype)", low, re.S):
        return det("html")
    if t[:1] in "{[":
        if re.search(r'"log"\s*:\s*\{', t[:4096]) and re.search(r'"(entries|creator|pages)"\s*:', t):
            return det("har")
        if size is not None and size <= 2_000_000 and len(head) >= size:
            try:
                json.loads(txt)
                return det("json")
            except ValueError:
                pass
        elif re.match(r'[\[{]\s*("|\{|\[|\d|\]|\})', t):
            return det("json", note="JSON ocena po nagłówku")
    js_score = len(_JS_TOKENS.findall(low))
    css_hit = bool(_CSS_RULE.search(low[:20000])) or low.lstrip().startswith(("@charset", "@import", "@media", ":root", "@font-face"))
    if css_hit and js_score == 0:
        return det("css")
    if js_score >= 2 or (js_score and hint == "js"):
        return det("js")
    if css_hit:
        return det("css")
    if hint in ("js", "css", "html", "json", "svg", "text"):
        return det(_EXT_KIND[hint], "extension", "zawartość niejednoznaczna")
    if hint == "har":
        return det("text", "extension", "rozszerzenie .har bez struktury HAR")
    return det("text")
