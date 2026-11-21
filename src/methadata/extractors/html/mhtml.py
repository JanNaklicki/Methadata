"""Rozbiór MHTML/MIME `multipart/related` z zachowaniem przesunięć bajtowych części w pliku."""
from __future__ import annotations

import email
import re
from dataclasses import dataclass, field
from email import policy
from typing import Optional


@dataclass
class Part:
    index: int
    content_type: str
    location: Optional[str]
    content_id: Optional[str]
    data: bytes                 # zdekodowana treść (QP/base64)
    byte_start: int             # zakres treści (po nagłówkach) w surowym pliku
    byte_end: int
    charset: Optional[str] = None
    headers: dict = field(default_factory=dict)


@dataclass
class Mhtml:
    headers: dict
    parts: list[Part]
    snapshot_location: Optional[str]

    def main_part(self) -> Optional[Part]:
        for p in self.parts:
            if p.content_type == "text/html":
                return p
        return None


def looks_like_mhtml(raw: bytes, media_type: Optional[str] = None, name: Optional[str] = None) -> bool:
    if media_type and "multipart/related" in media_type.lower():
        return True
    if name and name.lower().endswith((".mhtml", ".mht")):
        return True
    head = raw[:2048].decode("latin-1", "replace").lower()
    return "multipart/related" in head and ("mime-version" in head or "snapshot-content-location" in head)


def _nl(b: bytes) -> bytes:
    return re.sub(rb"\r+\n", b"\n", b)


_HDR_END = re.compile(rb"\r*\n\r*\n")


def parse_mhtml(raw: bytes) -> Mhtml:
    m = _HDR_END.search(raw)
    if not m:
        raise ValueError("MHTML: brak nagłówków")
    top = email.message_from_bytes(_nl(raw[: m.start()]), policy=policy.compat32)
    boundary = top.get_boundary()
    if not boundary:
        raise ValueError("MHTML: brak boundary")
    delim = re.compile(rb"^--" + re.escape(boundary.encode()) + rb"(--)?[ \t]*\r*$", re.M)
    marks = list(delim.finditer(raw))
    parts: list[Part] = []
    for i, mk in enumerate(marks):
        if mk.group(1):
            break
        if i + 1 >= len(marks):
            break
        start = mk.end()
        while raw[start:start + 1] == b"\r":
            start += 1
        if raw[start:start + 1] == b"\n":
            start += 1
        end = marks[i + 1].start()
        chunk = raw[start:end]
        hm = _HDR_END.search(chunk)
        if chunk.startswith((b"\r", b"\n")):      # część bez nagłówków
            hdr_len, body_off = 0, len(chunk) - len(chunk.lstrip(b"\r\n"))
        elif hm:
            hdr_len, body_off = hm.start(), hm.end()
        else:
            continue
        msg = email.message_from_bytes(_nl(chunk[:hdr_len]), policy=policy.compat32)
        body = chunk[body_off:]
        cte = (msg.get("Content-Transfer-Encoding") or "").strip().lower()
        body_n = body.replace(b"\r\r\n", b"\r\n")   # niektóre zapisy mają CR CR LF
        sub = email.message_from_bytes(_nl(chunk[:hdr_len]) + b"\n\n" + body_n, policy=policy.compat32)
        data = sub.get_payload(decode=True) if cte in ("quoted-printable", "base64") else body
        if data is None:
            data = body
        # bajty treści bez końcowego znaku nowej linii przed delimiterem
        b_end = start + len(chunk)
        if raw[b_end - 1:b_end] == b"\n":
            b_end -= 1
        while raw[b_end - 1:b_end] == b"\r":
            b_end -= 1
        ct = (msg.get_content_type() or "application/octet-stream").lower()
        parts.append(Part(index=len(parts), content_type=ct,
                          location=(msg.get("Content-Location") or "").strip() or None,
                          content_id=(msg.get("Content-ID") or "").strip() or None,
                          data=data, byte_start=start + body_off, byte_end=max(b_end, start + body_off + 1),
                          charset=msg.get_content_charset(), headers={k: str(v) for k, v in msg.items()}))
    return Mhtml(headers={k: str(v) for k, v in top.items()}, parts=parts,
                 snapshot_location=(top.get("Snapshot-Content-Location") or "").strip() or None)
