"""Statyczna analiza SVG (xml.etree; bez DTD/encji, bez pobierania zasobów, skrypty tylko jako dane)."""
from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

NORM_VERSION = "svg-norm/1"
SVG_NS = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"
_NOISE_NS = ("inkscape", "sodipodi", "dc", "cc", "rdf", "xmlns", "sketch", "serif", "adobe", "i", "x", "a")
_NOISE_TAGS = {"metadata", "title", "desc", "namedview", "RDF", "Work"}
_NUM = re.compile(r"-?\d*\.?\d+(?:[eE][-+]?\d+)?")
_WS = re.compile(r"\s+")


class SvgParseError(ValueError):
    pass


@dataclass
class SvgAnalysis:
    structure: dict[str, Any] = field(default_factory=dict)
    geometry_sha256: str = ""
    hrefs: list[dict] = field(default_factory=list)
    fonts: list[str] = field(default_factory=list)
    scripts: list[dict] = field(default_factory=list)   # {code, start, end}
    styles: list[dict] = field(default_factory=list)    # {css, start, end}
    flags: dict[str, Any] = field(default_factory=dict)
    ids: list[str] = field(default_factory=list)


def _local(tag: str) -> tuple[str, str]:
    if tag.startswith("{"):
        ns, _, name = tag[1:].partition("}")
        return ns, name
    return "", tag


def _round(s: str) -> str:
    return _NUM.sub(lambda m: (f"{float(m.group()):.2f}".rstrip("0").rstrip(".") or "0"), s)


def analyze(text: str, base: int = 0) -> SvgAnalysis:
    if re.search(r"<!ENTITY", text, re.I):
        raise SvgParseError("DTD z encjami odrzucone (ochrona przed rozwinięciem encji)")
    try:
        xml = re.sub(r"""^(\s*<\?xml[^>]*?)\sencoding=["'][^"']*["']""", r"\1", text)
        root = ET.fromstring(xml.encode("utf-8"))
    except ET.ParseError as e:
        raise SvgParseError(f"XML: {e}") from e
    ns, name = _local(root.tag)
    if name != "svg":
        raise SvgParseError(f"korzeń to <{name}>, nie <svg>")
    A = SvgAnalysis()
    counts: dict[str, int] = {}
    canon: list[str] = []
    fonts: set[str] = set()
    ids: list[str] = []
    depth_max = 0
    path_len = 0
    flags = {"has_script": False, "has_foreign_object": False, "has_animation": False, "has_event_attrs": False,
             "has_external_refs": False, "has_style": False, "has_text": False}

    def loc(needle: str) -> tuple[int, int]:
        k = text.find(needle) if needle else -1
        return (base + k, base + k + len(needle)) if k >= 0 else (base, base + max(len(text), 1))

    def add_fonts(v: str):
        for p in re.split(r",(?![^(]*\))", v):
            p = p.strip().strip("'\"").strip()
            if p and p.lower() not in ("inherit", "initial", "unset"):
                fonts.add(p)

    def visit(el: ET.Element, depth: int):
        nonlocal depth_max, path_len
        ens, tag = _local(el.tag)
        if ens not in ("", SVG_NS) or tag in _NOISE_TAGS:
            return
        depth_max = max(depth_max, depth)
        counts[tag] = counts.get(tag, 0) + 1
        attrs = {}
        for k, v in el.attrib.items():
            kns, kn = _local(k)
            if kns and kns != XLINK:
                continue
            attrs[("xlink:" if kns == XLINK else "") + kn] = v
        if "id" in attrs:
            ids.append(attrs["id"])
        if tag == "script":
            flags["has_script"] = True
            code = el.text or ""
            if code.strip():
                k = text.find(code)
                A.scripts.append({"code": code, "start": base + k if k >= 0 else base, "end": base + (k + len(code) if k >= 0 else len(text))})
        elif tag == "style":
            flags["has_style"] = True
            css = el.text or ""
            if css.strip():
                k = text.find(css)
                A.styles.append({"css": css, "start": base + k if k >= 0 else base, "end": base + (k + len(css) if k >= 0 else len(text))})
        elif tag == "foreignObject":
            flags["has_foreign_object"] = True
        elif tag in ("animate", "animateTransform", "animateMotion", "set"):
            flags["has_animation"] = True
        elif tag in ("text", "tspan"):
            flags["has_text"] = True
        if tag == "path":
            path_len += len(attrs.get("d", ""))
        for k, v in attrs.items():
            if k.startswith("on") and k[2:].isalpha():
                flags["has_event_attrs"] = True
            if k in ("href", "xlink:href") and v.strip():
                u = v.strip()
                internal = u.startswith("#")
                data = u.lower().startswith("data:")
                A.hrefs.append({"url": u[:1000], "kind": "internal" if internal else ("data" if data else "external"),
                                "tag": tag, "span": loc(u[:200])})
                if not internal and not data:
                    flags["has_external_refs"] = True
            if k == "font-family":
                add_fonts(v)
            if k == "style":
                for m in re.finditer(r"font-family\s*:\s*([^;]+)", v):
                    add_fonts(m.group(1))
                for m in re.finditer(r"url\(\s*['\"]?([^)'\"]+)", v):
                    u = m.group(1).strip()
                    if not u.startswith("#"):
                        A.hrefs.append({"url": u[:1000], "kind": "data" if u.lower().startswith("data:") else "external",
                                        "tag": tag, "span": loc(u[:200])})
                        if not u.lower().startswith("data:"):
                            flags["has_external_refs"] = True
            elif re.search(r"url\(\s*['\"]?https?:", v):
                flags["has_external_refs"] = True
        norm_attrs = sorted((k, _round(_WS.sub(" ", v.strip()))) for k, v in attrs.items()
                            if k not in ("id", "class") and not k.startswith("on"))
        canon.append(f"{depth}{tag}[" + ",".join(f"{k}={v}" for k, v in norm_attrs) + "]")
        for c in el:
            visit(c, depth + 1)

    visit(root, 0)
    for st in A.styles:
        for m in re.finditer(r"font-family\s*:\s*([^;}]+)", st["css"]):
            add_fonts(m.group(1))
    A.fonts = sorted(fonts)
    A.ids = sorted(set(ids))[:2000]
    A.flags = flags
    A.geometry_sha256 = hashlib.sha256("\n".join(canon).encode("utf-8")).hexdigest()
    A.structure = {"elements": sum(counts.values()), "tags": dict(sorted(counts.items())), "max_depth": depth_max,
                   "view_box": root.attrib.get("viewBox"), "width": root.attrib.get("width"), "height": root.attrib.get("height"),
                   "path_data_chars": path_len, "ids": len(set(ids)), "length": len(text)}
    return A
