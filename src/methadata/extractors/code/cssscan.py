"""Statyczna analiza CSS: układ reguł, fonty, odnośniki i zależności (bez pobierania zasobów)."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Optional

NORM_VERSION = "css-norm/1"


class CssParseError(ValueError):
    def __init__(self, msg: str, pos: int):
        super().__init__(f"{msg} @{pos}")
        self.pos = pos


@dataclass
class Rule:
    prelude: str
    start: int
    end: int
    at: Optional[str] = None            # nazwa at-reguły albo None dla reguły stylu
    decls: list[tuple[str, str, int]] = field(default_factory=list)
    children: list["Rule"] = field(default_factory=list)
    depth: int = 0


_WS = re.compile(r"\s+")
_URL = re.compile(r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^)'"\s][^)]*?))\s*\)""", re.I)
_IMPORT = re.compile(r"""^\s*(?:url\(\s*(?:"([^"]*)"|'([^']*)'|([^)'"\s][^)]*?))\s*\)|"([^"]*)"|'([^']*)')\s*(.*)$""", re.I | re.S)
_FMT = re.compile(r"""format\(\s*["']?([^"')]+)["']?\s*\)""", re.I)
_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_DATA_AT = {"media", "supports", "layer", "container", "document", "scope"}


def _blank_comments(src: str) -> str:
    """Zastępuje komentarze spacjami (zachowuje pozycje); sprawdza niezamknięty komentarz."""
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c in "'\"":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            if j >= n:
                raise CssParseError("niezamknięty łańcuch", i)
            out.append(src[i:j + 1])
            i = j + 1
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j < 0:
                raise CssParseError("niezamknięty komentarz", i)
            out.append(re.sub(r"[^\n]", " ", src[i:j + 2]))
            i = j + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _skip_str_or_paren(s: str, i: int) -> int:
    n = len(s)
    c = s[i]
    if c in "'\"":
        j = i + 1
        while j < n and s[j] != c:
            j += 2 if s[j] == "\\" else 1
        return min(j, n - 1)
    if c == "(":
        depth, j = 0, i
        while j < n:
            if s[j] in "'\"":
                j = _skip_str_or_paren(s, j)
            elif s[j] == "(":
                depth += 1
            elif s[j] == ")":
                depth -= 1
                if depth == 0:
                    return j
            j += 1
        raise CssParseError("niezamknięty nawias", i)
    return i


def parse(src: str) -> tuple[list[Rule], str]:
    """Zwraca (reguły najwyższego poziomu, tekst bez komentarzy). Zgłasza CssParseError przy niezbalansowanych blokach."""
    s = _blank_comments(src)
    n = len(s)

    def block(i: int, depth: int, until_close: bool) -> tuple[list[Rule], int]:
        rules: list[Rule] = []
        while True:
            # prelude do '{' lub ';' lub '}'
            start = i
            j = i
            while j < n and s[j] not in "{};":
                if s[j] in "'\"(":
                    j = _skip_str_or_paren(s, j)
                j += 1
            pre = s[i:j]
            if j >= n:
                if until_close:
                    raise CssParseError("niezamknięty blok", start)
                if pre.strip():
                    rules.append(_stmt(pre, i, j, depth))
                return rules, n
            ch = s[j]
            if ch == "}":
                if not until_close:
                    raise CssParseError("nadmiarowy '}'", j)
                if pre.strip():
                    rules.append(_stmt(pre, i, j, depth))
                return rules, j + 1
            if ch == ";":
                if pre.strip():
                    rules.append(_stmt(pre, i, j + 1, depth))
                i = j + 1
                continue
            # '{'
            head = pre.strip()
            body_start = j + 1
            if head.startswith("@"):
                name = re.match(r"@([\w-]+)", head).group(1).lower() if re.match(r"@([\w-]+)", head) else ""
                r = Rule(prelude=_WS.sub(" ", head), start=i + (len(pre) - len(pre.lstrip())), end=0, at=name, depth=depth)
                if name in _DATA_AT:
                    r.children, k = block(body_start, depth + 1, True)
                else:  # @font-face, @keyframes, @page ... : deklaracje (lub bloki w keyframes)
                    r.children, r.decls, k = _decl_block(body_start, depth + 1, name)
                r.end = k
            else:
                r = Rule(prelude=_WS.sub(" ", head), start=i + (len(pre) - len(pre.lstrip())), end=0, depth=depth)
                r.children, r.decls, k = _decl_block(body_start, depth + 1, "")
                r.end = k
            rules.append(r)
            i = k

    def _stmt(pre: str, a: int, b: int, depth: int) -> Rule:
        t = pre.strip()
        at = re.match(r"@([\w-]+)", t)
        return Rule(prelude=_WS.sub(" ", t), start=a + (len(pre) - len(pre.lstrip())), end=b,
                    at=at.group(1).lower() if at else "", depth=depth)

    def _decl_block(i: int, depth: int, atname: str):
        """Treść bloku: deklaracje `p: v;` oraz zagnieżdżone reguły (CSS nesting, keyframes)."""
        decls: list[tuple[str, str, int]] = []
        kids: list[Rule] = []
        while True:
            j = i
            while j < n and s[j] not in "{};":
                if s[j] in "'\"(":
                    j = _skip_str_or_paren(s, j)
                j += 1
            if j >= n:
                raise CssParseError("niezamknięty blok", i)
            ch, seg = s[j], s[i:j]
            if ch == "{":
                sub, k = block_from(i, depth)
                kids.extend(sub)
                i = k
                continue
            if seg.strip():
                m = re.match(r"\s*([-\w\\*]+)\s*:(.*)$", seg, re.S)
                if m:
                    decls.append((m.group(1).lower(), _WS.sub(" ", m.group(2)).strip(), i + (len(seg) - len(seg.lstrip()))))
            if ch == "}":
                return kids, decls, j + 1
            i = j + 1

    def block_from(i: int, depth: int):
        # jedna zagnieżdżona reguła rozpoczynająca się w i
        rs, k = _one_rule(i, depth)
        return [rs], k

    def _one_rule(i: int, depth: int):
        j = i
        while s[j] != "{":
            if s[j] in "'\"(":
                j = _skip_str_or_paren(s, j)
            j += 1
        head = s[i:j].strip()
        r = Rule(prelude=_WS.sub(" ", head), start=i + (len(s[i:j]) - len(s[i:j].lstrip())), end=0,
                 at=(re.match(r"@([\w-]+)", head).group(1).lower() if head.startswith("@") and re.match(r"@([\w-]+)", head) else None),
                 depth=depth)
        r.children, r.decls, k = _decl_block(j + 1, depth + 1, r.at or "")
        r.end = k
        return r, k

    rules, _ = block(0, 0, False)
    return rules, s


def walk(rules: list[Rule]):
    for r in rules:
        yield r
        yield from walk(r.children)


# ------------------------------------------------------------------ normalizacja
def _norm_value(v: str) -> str:
    v = _WS.sub(" ", v.strip().rstrip(";")).strip()
    v = _HEX.sub(lambda m: m.group().lower(), v)
    v = re.sub(r"""(["'])([A-Za-z0-9_ -]*)\1""", r"\2", v)   # cudzysłowy wokół prostych nazw/formatów
    v = re.sub(r"\s*,\s*", ",", v)
    v = re.sub(r"\)\s+(?=[a-z-]+\()", ")", v)                        # url(x) format(y) -> url(x)format(y)
    v = re.sub(r"(?<![\w.])0+\.(\d)", r".\1", v)          # 0.5 -> .5
    v = re.sub(r"(?<![\w.#-])(0*\.?0+)(px|em|rem|pt|%|ex|ch|vh|vw)\b", "0", v)  # 0px -> 0
    v = re.sub(r"\s*!\s*important", "!important", v, flags=re.I)
    v = re.sub(r"""url\(\s*(['"]?)(.*?)\1\s*\)""", lambda m: f"url({m.group(2)})", v)
    return v.lower() if not re.search(r"""["']|url\(""", v) else v


def _norm_sel(sel: str) -> str:
    sel = _WS.sub(" ", sel.strip())
    sel = re.sub(r"\s*([>+~,])\s*", r"\1", sel)
    return sel.lower() if "\"" not in sel and "'" not in sel else sel


def _canon(r: Rule) -> str:
    pre = _norm_sel(r.prelude) if r.at is None else _WS.sub(" ", re.sub(r"\s*([:,()])\s*", r"\1", r.prelude.lower()))
    d = ";".join(f"{p}:{_norm_value(v)}" for p, v, _ in r.decls)
    c = "".join(_canon(x) for x in r.children)
    return f"{pre}{{{d}{c}}}"


def _unurl(m: re.Match) -> str:
    return next(g for g in m.groups() if g is not None).strip()


@dataclass
class CssAnalysis:
    structure: dict[str, Any] = field(default_factory=dict)
    layout_sha256: str = ""
    selectors_sha256: str = ""
    classes: list[str] = field(default_factory=list)
    ids: list[str] = field(default_factory=list)
    font_faces: list[dict] = field(default_factory=list)
    font_families: list[str] = field(default_factory=list)
    urls: list[dict] = field(default_factory=list)
    colors: list[str] = field(default_factory=list)
    media: list[str] = field(default_factory=list)


def _families(v: str) -> list[str]:
    out = []
    for part in re.split(r",(?![^(]*\))", re.sub(r"!important", "", v, flags=re.I)):
        p = part.strip().strip("'\"").strip()
        if p and not p.startswith(("var(", "inherit", "initial", "unset")):
            out.append(p)
    return out


def analyze(src: str, base: int = 0) -> CssAnalysis:
    rules, s = parse(src)
    A = CssAnalysis()
    all_rules = list(walk(rules))
    style = [r for r in all_rules if r.at is None]
    ats: dict[str, int] = {}
    for r in all_rules:
        if r.at is not None:
            ats[r.at or "?"] = ats.get(r.at or "?", 0) + 1
    props: dict[str, int] = {}
    ndecl = important = custom = 0
    sels: set[str] = set()
    classes: set[str] = set()
    idset: set[str] = set()
    fams: set[str] = set()
    colors: set[str] = set()
    for r in all_rules:
        for p, v, _ in r.decls:
            ndecl += 1
            props[p] = props.get(p, 0) + 1
            if p.startswith("--"):
                custom += 1
            if "!important" in v.replace(" ", "").lower():
                important += 1
            if p in ("font-family",) and (r.at is None):
                fams.update(_families(v))
            elif p == "font" and r.at is None:
                m = re.search(r"(?:\d[\w%.]*(?:/[\w%.]+)?)\s+(.+)$", v)
                if m:
                    fams.update(_families(m.group(1)))
            colors.update(x.lower() for x in _HEX.findall(v))
    for r in style:
        for sel in re.split(r",(?![^(]*\))", r.prelude):
            ns = _norm_sel(sel)
            if ns:
                sels.add(ns)
        classes.update(re.findall(r"\.(-?[_a-zA-Z][\w-]*)", re.sub(r"\[[^\]]*\]|\"[^\"]*\"|'[^']*'", "", r.prelude)))
        idset.update(re.findall(r"#(-?[_a-zA-Z][\w-]*)", re.sub(r"\[[^\]]*\]|\"[^\"]*\"|'[^']*'", "", r.prelude)))
    A.media = sorted({_WS.sub(" ", r.prelude[6:].strip().lower()) for r in all_rules if r.at == "media"})
    # @font-face
    for r in all_rules:
        if r.at == "font-face":
            d = {p: v for p, v, _ in r.decls}
            srcs = []
            for part in re.split(r",(?![^(]*\))", d.get("src", "")):
                u = _URL.search(part)
                f = _FMT.search(part)
                loc = re.search(r"local\(\s*[\"']?([^)\"']+)", part)
                if u:
                    srcs.append({"url": _unurl(u), "format": f.group(1).lower() if f else None})
                elif loc:
                    srcs.append({"local": loc.group(1).strip()})
            A.font_faces.append({"family": (d.get("font-family") or "").strip("'\" "), "weight": d.get("font-weight"),
                                 "style": d.get("font-style"), "display": d.get("font-display"),
                                 "unicode_range": d.get("unicode-range"), "src": srcs,
                                 "span": (base + r.start, base + r.end)})
    # url() i @import
    for r in all_rules:
        if r.at == "import":
            m = _IMPORT.match(re.sub(r"^@import", "", r.prelude, flags=re.I))
            if m:
                u = next((g for g in m.groups()[:5] if g is not None), "").strip()
                A.urls.append({"url": u, "kind": "import", "via": "@import", "media": (m.group(6) or "").strip() or None,
                               "span": (base + r.start, base + r.end)})
            continue
        for p, v, pos in r.decls:
            for m in _URL.finditer(v):
                u = _unurl(m)
                if not u:
                    continue
                kind = "font" if r.at == "font-face" else ("image" if re.search(r"\.(?:png|jpe?g|gif|svg|webp|avif|ico)(?:[?#]|$)", u, re.I) or u.startswith("data:image") else "other")
                A.urls.append({"url": u[:1000], "kind": kind, "via": f"{p}" + (" @font-face" if r.at == "font-face" else ""),
                               "span": (base + r.start, base + r.end)})
    A.structure = {"rules": len(style), "at_rules": dict(sorted(ats.items())), "selectors": len(sels), "declarations": ndecl,
                   "max_depth": max((r.depth for r in all_rules), default=0), "custom_properties": custom,
                   "important": important, "length": len(src),
                   "top_properties": dict(sorted(props.items(), key=lambda kv: (-kv[1], kv[0]))[:20]),
                   "font_faces": len(A.font_faces), "media_queries": len(A.media)}
    A.layout_sha256 = hashlib.sha256("".join(_canon(r) for r in rules).encode("utf-8")).hexdigest()
    A.selectors_sha256 = hashlib.sha256("\n".join(sorted(sels)).encode("utf-8")).hexdigest()
    A.classes = sorted(classes)[:3000]
    A.ids = sorted(idset)[:1000]
    A.font_families = sorted(fams)
    A.colors = sorted(colors)[:200]
    return A
