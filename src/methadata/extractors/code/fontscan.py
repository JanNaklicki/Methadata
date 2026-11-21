"""Nagłówki fontów (WOFF/WOFF2/TTF/OTF/TTC): kontener, tablice, nazwy z `name`, `head`, `maxp`. Bez rasteryzacji."""
from __future__ import annotations

import struct
import zlib
from typing import Any, Optional

NORM_VERSION = "font-info/1"
_NAME_IDS = {0: "copyright", 1: "family", 2: "subfamily", 3: "unique_id", 4: "full_name", 5: "version", 6: "postscript",
             8: "manufacturer", 9: "designer"}


class FontParseError(ValueError):
    pass


def sniff(data: bytes) -> Optional[str]:
    h = data[:4]
    if h == b"wOFF":
        return "woff"
    if h == b"wOF2":
        return "woff2"
    if h in (b"\x00\x01\x00\x00", b"true", b"typ1"):
        return "ttf"
    if h == b"OTTO":
        return "otf"
    if h == b"ttcf":
        return "ttc"
    return None


def _decode_name(rec_platform: int, raw: bytes) -> str:
    try:
        if rec_platform in (0, 3):
            return raw.decode("utf-16-be")
        return raw.decode("mac_roman")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _parse_name(t: bytes) -> dict[str, str]:
    if len(t) < 6:
        return {}
    _, count, soff = struct.unpack(">HHH", t[:6])
    out: dict[str, str] = {}
    for i in range(min(count, 512)):
        o = 6 + 12 * i
        if o + 12 > len(t):
            break
        plat, enc, lang, nid, ln, off = struct.unpack(">HHHHHH", t[o:o + 12])
        key = _NAME_IDS.get(nid)
        if not key:
            continue
        raw = t[soff + off:soff + off + ln]
        val = _decode_name(plat, raw).strip("\x00 ")
        if val and (key not in out or (plat == 3 and lang == 0x409)):
            out[key] = val[:300]
    return out


def parse(data: bytes) -> dict[str, Any]:
    kind = sniff(data)
    if kind is None:
        raise FontParseError("nierozpoznany nagłówek fontu")
    info: dict[str, Any] = {"container": kind, "size": len(data)}
    tables: dict[str, bytes] = {}
    try:
        if kind == "woff2":
            if len(data) < 48:
                raise FontParseError("zbyt krótki nagłówek WOFF2")
            flavor = data[4:8]
            length, ntab = struct.unpack(">IH", data[8:14])
            info.update(flavor=flavor.decode("latin-1"), declared_length=length, num_tables=ntab,
                        total_sfnt_size=struct.unpack(">I", data[16:20])[0], names={},
                        note="WOFF2 jest kompresowany (brotli): odczytano tylko nagłówek")
            return info
        if kind == "woff":
            flavor = data[4:8]
            length, ntab = struct.unpack(">IH", data[8:14])
            info.update(flavor=flavor.decode("latin-1"), declared_length=length, num_tables=ntab)
            for i in range(ntab):
                o = 44 + 20 * i
                tag, off, clen, olen, _ = struct.unpack(">4sIIII", data[o:o + 20])
                blob = data[off:off + clen]
                if tag.decode("latin-1") in ("name", "head", "maxp"):
                    tables[tag.decode("latin-1")] = zlib.decompress(blob) if clen < olen else blob
                info.setdefault("tables", []).append(tag.decode("latin-1"))
        else:
            base = 0
            if kind == "ttc":
                nfonts = struct.unpack(">I", data[8:12])[0]
                base = struct.unpack(">I", data[12:16])[0]
                info["collection_fonts"] = nfonts
            info["flavor"] = data[base:base + 4].decode("latin-1")
            ntab = struct.unpack(">H", data[base + 4:base + 6])[0]
            info["num_tables"] = ntab
            for i in range(ntab):
                o = base + 12 + 16 * i
                tag, _, off, ln = struct.unpack(">4sIII", data[o:o + 16])
                t = tag.decode("latin-1")
                info.setdefault("tables", []).append(t)
                if t in ("name", "head", "maxp"):
                    tables[t] = data[off:off + ln]
        info["names"] = _parse_name(tables["name"]) if "name" in tables else {}
        if "head" in tables and len(tables["head"]) >= 54:
            h = tables["head"]
            info["units_per_em"] = struct.unpack(">H", h[18:20])[0]
        if "maxp" in tables and len(tables["maxp"]) >= 6:
            info["num_glyphs"] = struct.unpack(">H", tables["maxp"][4:6])[0]
        info["tables"] = sorted(info.get("tables", []))
        return info
    except (struct.error, zlib.error, IndexError) as e:
        raise FontParseError(f"uszkodzony font: {e}") from e
