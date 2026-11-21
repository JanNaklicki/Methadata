"""Statyczna analiza JS: tokenizer (bez wykonywania kodu), importy, literały, zdarzenia, wzorce, sygnatura.

Kod jest traktowany wyłącznie jako dane tekstowe. Tokenizer jest heurystyczny (nie pełny parser ES), ale wykrywa
niezamknięte łańcuchy/komentarze/szablony i niezbalansowane nawiasy -> `JsParseError`.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Any, Optional

NORM_VERSION = "js-norm/1"
SHINGLE_N = 4
SHINGLE_K = 64

KEYWORDS = frozenset("""break case catch class const continue debugger default delete do else export extends finally for
function if import in instanceof new return super switch this throw try typeof var let void while with yield await async
of static get set null true false undefined NaN Infinity""".split())
REGEX_AFTER_KW = frozenset("return typeof instanceof in of new delete void throw case do else yield await".split())
DECL_KW = frozenset(("var", "let", "const"))
GLOBALS = frozenset("""window document self globalThis navigator location history console screen localStorage sessionStorage
Math JSON Object Array String Number Boolean Date RegExp Promise Symbol Map Set WeakMap WeakSet Error TypeError Function
Image XMLHttpRequest WebSocket Worker fetch Event CustomEvent Blob URL FormData parseInt parseFloat isNaN encodeURIComponent
decodeURIComponent encodeURI decodeURI escape unescape setTimeout setInterval clearTimeout clearInterval requestAnimationFrame
atob btoa eval alert confirm prompt define require module exports importScripts postMessage performance crypto indexedDB
jQuery $ _ dataLayer gtag ga fbq arguments process global Intl Reflect Proxy""".split())
_PUNCT = ["<<=", ">>=", ">>>", "**=", "...", "===", "!==", "&&=", "||=", "??=", "=>", "==", "!=", "<=", ">=", "&&", "||",
          "??", "?.", "++", "--", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<", ">>", "**"]
_ID = re.compile(r"[A-Za-z_$\u0080-\uffff][\w$\u0080-\uffff]*")
_NUM = re.compile(r"0[xX][0-9a-fA-F_]+n?|0[bB][01_]+n?|0[oO][0-7_]+n?|(?:\d[\d_]*\.?\d*|\.\d+)(?:[eE][+-]?\d+)?n?")
_OPEN, _CLOSE = "([{", ")]}"


class JsParseError(ValueError):
    def __init__(self, msg: str, pos: int):
        super().__init__(f"{msg} @{pos}")
        self.pos = pos


@dataclass
class Tok:
    t: str       # id kw num str tpl re punc
    s: str       # tekst źródłowy
    a: int
    b: int
    v: str = ""  # wartość (dla str: odescapowana zawartość)


_ESC = re.compile(r"\\(x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|u\{[0-9a-fA-F]+\}|.)", re.S)
_SIMPLE = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


def _unesc(s: str) -> str:
    def r(m):
        g = m.group(1)
        if g[0] == "x":
            return chr(int(g[1:], 16))
        if g[0] == "u":
            h = g[1:].strip("{}")
            try:
                return chr(int(h, 16))
            except (ValueError, OverflowError):
                return ""
        return _SIMPLE.get(g, g)
    return _ESC.sub(r, s) if "\\" in s else s


def tokenize(src: str) -> list[Tok]:
    n = len(src)
    i = 0
    out: list[Tok] = []
    stack: list[tuple[str, int]] = []
    if src.startswith("#!"):
        i = src.find("\n") if "\n" in src else n
    while i < n:
        c = src[i]
        if c in " \t\r\n\f\v\u00a0\ufeff\u2028\u2029":
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j < 0:
                raise JsParseError("niezamknięty komentarz", i)
            i = j + 2
            continue
        if src.startswith("<!--", i) or (src.startswith("-->", i) and (not out)):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c in "'\"":
            j = i + 1
            while j < n and src[j] != c:
                if src[j] == "\\":
                    j += 1
                elif src[j] == "\n":
                    raise JsParseError("niezamknięty łańcuch", i)
                j += 1
            if j >= n:
                raise JsParseError("niezamknięty łańcuch", i)
            out.append(Tok("str", src[i:j + 1], i, j + 1, _unesc(src[i + 1:j])))
            i = j + 1
            continue
        if c == "`":
            j, depth = i + 1, 0
            while j < n:
                ch = src[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == "`" and depth == 0:
                    break
                if src.startswith("${", j):
                    depth += 1
                    j += 2
                    continue
                if ch == "}" and depth:
                    depth -= 1
                elif ch == "`" and depth:  # zagnieżdżony szablon: pomiń
                    k = j + 1
                    while k < n and src[k] != "`":
                        k += 2 if src[k] == "\\" else 1
                    j = k
                j += 1
            if j >= n:
                raise JsParseError("niezamknięty szablon", i)
            body = src[i + 1:j]
            out.append(Tok("tpl", src[i:j + 1], i, j + 1, "" if "${" in body else _unesc(body)))
            i = j + 1
            continue
        m = _ID.match(src, i)
        if m:
            w = m.group()
            out.append(Tok("kw" if w in KEYWORDS else "id", w, i, m.end(), w))
            i = m.end()
            continue
        if c.isdigit() or (c == "." and i + 1 < n and src[i + 1].isdigit()):
            m = _NUM.match(src, i)
            out.append(Tok("num", m.group(), i, m.end(), m.group()))
            i = m.end()
            continue
        if c == "/":
            prev = out[-1] if out else None
            allowed = prev is None or (prev.t == "punc" and prev.s not in (")", "]", "}")) or \
                (prev.t == "kw" and prev.s in REGEX_AFTER_KW)
            if allowed:
                j, cls = i + 1, False
                while j < n and src[j] != "\n":
                    ch = src[j]
                    if ch == "\\":
                        j += 1
                    elif ch == "[":
                        cls = True
                    elif ch == "]":
                        cls = False
                    elif ch == "/" and not cls:
                        break
                    j += 1
                if j < n and src[j] == "/":
                    k = j + 1
                    while k < n and (src[k].isalpha()):
                        k += 1
                    out.append(Tok("re", src[i:k], i, k, src[i:k]))
                    i = k
                    continue
                if prev is None or prev.t == "punc":
                    raise JsParseError("niezamknięty literał regex", i)
        p = next((x for x in _PUNCT if src.startswith(x, i)), c)
        if p in _OPEN:
            stack.append((p, i))
        elif p in _CLOSE:
            if not stack or _OPEN.index(stack[-1][0]) != _CLOSE.index(p):
                raise JsParseError(f"niezbalansowany {p!r}", i)
            stack.pop()
        out.append(Tok("punc", p, i, i + len(p), p))
        i += len(p)
    if stack:
        raise JsParseError(f"niezamknięty {stack[-1][0]!r}", stack[-1][1])
    return out


# ------------------------------------------------------------------ normalizacja i sygnatura
_DROP = frozenset((";", ",", "{", "}"))


def normalize(toks: list[Tok]) -> list[str]:
    """Strumień tokenów odporny na formatowanie, minifikację i zmianę nazw zmiennych lokalnych."""
    out: list[str] = []
    n = len(toks)
    for i, t in enumerate(toks):
        prev = toks[i - 1] if i else None
        nxt = toks[i + 1] if i + 1 < n else None
        if t.t == "punc":
            if t.s in _DROP:
                continue
            if t.s == "!" and nxt is not None and nxt.t == "num" and nxt.s in ("0", "1"):
                out.append("true" if nxt.s == "0" else "false")
                toks[i + 1] = Tok("punc", "", nxt.a, nxt.b)  # znacznik pominięcia
                continue
            if t.s == "":
                continue
            out.append(t.s)
        elif t.t == "kw":
            if t.s in DECL_KW:
                continue
            if t.s == "void" and nxt is not None and nxt.t == "num" and nxt.s == "0":
                out.append("undefined")
                toks[i + 1] = Tok("punc", "", nxt.a, nxt.b)
                continue
            out.append(t.s)
        elif t.t == "id":
            after_dot = prev is not None and prev.t == "punc" and prev.s in (".", "?.")
            key = nxt is not None and nxt.t == "punc" and nxt.s == ":" and prev is not None and prev.t == "punc" \
                and prev.s in ("{", ",")
            out.append(t.s if (after_dot or key or t.s in GLOBALS) else "I")
        elif t.t == "num":
            out.append("N")
        elif t.t in ("str", "tpl"):
            out.append("S")
        else:
            out.append("R")
    return out


def _h64(s: str) -> int:
    return int.from_bytes(hashlib.sha1(s.encode("utf-8")).digest()[:8], "big")


def shingles(norm: list[str], n: int = SHINGLE_N, k: int = SHINGLE_K) -> list[str]:
    if len(norm) < n:
        grams = [" ".join(norm)] if norm else []
    else:
        grams = [" ".join(norm[i:i + n]) for i in range(len(norm) - n + 1)]
    return [f"{x:016x}" for x in sorted({_h64(g) for g in grams})[:k]]


def similarity(a: list[str], b: list[str], k: int = SHINGLE_K) -> float:
    """Estymata Jaccarda dwóch sygnatur bottom-k."""
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    union = sorted(sa | sb)[:k]
    if not union:
        return 0.0
    return sum(1 for x in union if x in sa and x in sb) / len(union)


# ------------------------------------------------------------------ analiza
@dataclass
class JsAnalysis:
    imports: list[dict] = field(default_factory=list)
    literals: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    patterns: dict[str, int] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    norm: list[str] = field(default_factory=list)
    norm_sha256: str = ""
    shingles: list[str] = field(default_factory=list)


_API = {
    "eval": "eval", "Function": "function_ctor", "document.write": "document_write", "document.writeln": "document_write",
    "atob": "base64_decode", "btoa": "base64_encode", "String.fromCharCode": "from_char_code", "fetch": "fetch",
    "XMLHttpRequest": "xhr", "navigator.sendBeacon": "send_beacon", "WebSocket": "websocket",
    "localStorage": "local_storage", "sessionStorage": "session_storage", "document.cookie": "cookie",
    "navigator.userAgent": "user_agent", "navigator.language": "navigator_language", "navigator.plugins": "plugins",
    "window.open": "window_open", "location.href": "location_href", "location.replace": "location_replace",
    "location.assign": "location_assign", "document.createElement": "create_element", "history.pushState": "push_state",
    "history.replaceState": "push_state", "screen.width": "screen_size", "indexedDB": "indexed_db",
    "crypto.subtle": "subtle_crypto", "setTimeout": "set_timeout", "setInterval": "set_interval",
    "importScripts": "import_scripts", "postMessage": "post_message", "innerHTML": "inner_html",
    "outerHTML": "inner_html", "insertAdjacentHTML": "inner_html", "toDataURL": "canvas_data_url",
    "getContext": "canvas_context", "document.referrer": "referrer", "window.top": "frame_busting",
    "top.location": "frame_busting", "MutationObserver": "mutation_observer", "IntersectionObserver": "intersection_observer",
    "dataLayer": "data_layer", "gtag": "gtag", "fbq": "fbq", "ga": "ga", "unescape": "unescape", "decodeURIComponent": "decode_uri",
}
_EVENT_FUNCS = {"addEventListener": "addEventListener", "removeEventListener": "removeEventListener", "on": "on",
                "one": "on", "off": "off", "bind": "bind", "once": "on", "delegate": "delegate", "live": "on",
                "trigger": "trigger", "triggerHandler": "trigger"}
_URL = re.compile(r"^(?:https?:)?//[^\s'\"<>]{3,}$", re.I)
_DOMAIN = re.compile(r"^(?:[a-z0-9-]{1,63}\.)+[a-z]{2,24}(?::\d+)?(?:/\S*)?$", re.I)
_FILE_TLD = {"js", "css", "png", "jpg", "jpeg", "gif", "svg", "html", "htm", "json", "map", "php", "woff", "woff2", "ttf",
             "otf", "eot", "min", "txt", "xml", "ico", "webp", "mp4", "mp3", "webm", "ts", "jsx", "tsx", "py", "md", "asp", "aspx"}
_IDTOK = re.compile(r"^(?:GTM|UA|G|AW|GA|DC|MC)-[A-Za-z0-9-]{4,}$")
_BLOB = re.compile(r"^[A-Za-z0-9+/_\-]{32,}={0,2}$")
_HEX = re.compile(r"^[0-9a-fA-F]{16,}$")
_EMAIL = re.compile(r"^[\w.+-]+@[\w-]+(?:\.[\w-]+)+$")
_PATH = re.compile(r"^/[\w\-./%]+\.(?:js|css|php|html?|json|png|jpe?g|gif|svg|woff2?|mp4)(?:\?\S*)?$", re.I)
MAX_LITERALS = 400


def _classify(v: str) -> Optional[str]:
    if _URL.match(v):
        return "url"
    if _IDTOK.match(v):
        return "tracking_id"
    if _EMAIL.match(v):
        return "email"
    if " " not in v and _DOMAIN.match(v) and v.split("/")[0].split(":")[0].rsplit(".", 1)[1].lower() not in _FILE_TLD:
        return "domain"
    if _PATH.match(v):
        return "path"
    if _HEX.match(v):
        return "hex_blob"
    if _BLOB.match(v) and any(ch.isdigit() for ch in v) and any(ch.isalpha() for ch in v):
        return "blob"
    return None


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    freq: dict[str, int] = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    return -sum(c / len(s) * math.log2(c / len(s)) for c in freq.values())


def analyze(src: str, base: int = 0) -> JsAnalysis:
    """Analiza kodu; pozycje (`a`,`b`) w wyniku są przesunięte o `base` (offset w dokumencie nadrzędnym)."""
    toks = tokenize(src)
    A = JsAnalysis()
    n = len(toks)

    def span(a: int, b: int) -> tuple[int, int]:
        return (base + a, base + b)

    def s(i: int, k: int = 0) -> str:
        return toks[i + k].s if 0 <= i + k < n else ""

    def isstr(i: int) -> bool:
        return 0 <= i < n and toks[i].t == "str"

    seen_lit: set[tuple[str, str]] = set()

    def imp(kind: str, spec: str, tk: Tok):
        A.imports.append({"kind": kind, "spec": spec, "span": span(tk.a, tk.b)})

    def event(name: str, via: str, tk: Tok):
        for nm in name.split():
            nm = nm.split(".")[0] if via in ("on", "off", "bind", "delegate", "trigger") else nm
            if nm and re.match(r"^[A-Za-z][\w:\-]*$", nm):
                A.events.append({"name": nm, "via": via, "span": span(tk.a, tk.b)})

    patterns: dict[str, int] = {}
    i = 0
    while i < n:
        t = toks[i]
        prev = toks[i - 1] if i else None
        # --- importy
        if t.t == "kw" and t.s == "import" and not (prev and prev.s in (".", "?.")):
            if isstr(i + 1):
                imp("import", toks[i + 1].v, toks[i + 1])
            elif s(i, 1) == "(":
                if isstr(i + 2):
                    imp("dynamic_import", toks[i + 2].v, toks[i + 2])
                else:
                    patterns["dynamic_import_nonliteral"] = patterns.get("dynamic_import_nonliteral", 0) + 1
            elif s(i, 1) != ".":
                j = i + 1
                while j < n and j < i + 400 and (toks[j].t in ("id", "kw") or toks[j].s in ("{", "}", ",", "*")) and s(j) != "from":
                    j += 1
                if s(j) == "from" and isstr(j + 1):
                    imp("import", toks[j + 1].v, toks[j + 1])
        elif t.t == "kw" and t.s == "export" and not (prev and prev.s in (".", "?.")):
            j = i + 1
            while j < n and j < i + 400 and (toks[j].t in ("id", "kw") or toks[j].s in ("{", "}", ",", "*")) and s(j) != "from":
                j += 1
            if s(j) == "from" and isstr(j + 1):
                imp("export_from", toks[j + 1].v, toks[j + 1])
        elif t.t == "id" and not (prev and prev.s in (".", "?.")):
            nm = t.s
            if nm == "require" and s(i, 1) == "(" and isstr(i + 2):
                imp("require", toks[i + 2].v, toks[i + 2])
            elif nm == "importScripts" and s(i, 1) == "(":
                j = i + 2
                while j < n and s(j) != ")":
                    if isstr(j):
                        imp("importScripts", toks[j].v, toks[j])
                    j += 1
            elif nm == "define" and s(i, 1) == "(" and s(i, 2) == "[":
                j = i + 3
                while j < n and s(j) != "]":
                    if isstr(j):
                        imp("amd", toks[j].v, toks[j])
                    j += 1
        # --- łańcuchy właściwości (wzorce, zdarzenia)
        call_dot = bool(prev and prev.t == "punc" and prev.s in (".", "?.") and i >= 2 and toks[i - 2].s in (")", "]"))
        if t.t in ("id", "kw") and (call_dot or not (prev and prev.t == "punc" and prev.s in (".", "?."))):
            j, chain = i, [t.s]
            while s(j, 1) in (".", "?.") and j + 2 < n and toks[j + 2].t in ("id", "kw"):
                chain.append(toks[j + 2].s)
                j += 2
            full = ".".join(chain)
            last = chain[-1]
            # wzorce: dopasuj sufiksy łańcucha
            for k in range(len(chain)):
                key = ".".join(chain[k:])
                if key in _API:
                    patterns[_API[key]] = patterns.get(_API[key], 0) + 1
                    break
            if s(j, 1) == "(":
                if last in _EVENT_FUNCS and isstr(j + 2) and (len(chain) > 1 or call_dot or last == "addEventListener"):
                    event(toks[j + 2].v, _EVENT_FUNCS[last], toks[j + 2])
                if last in ("register",) and "serviceWorker" in chain and isstr(j + 2):
                    imp("service_worker", toks[j + 2].v, toks[j + 2])
            if last.startswith("on") and len(last) > 2 and last.islower() and s(j, 1) == "=" and (len(chain) > 1 or call_dot):
                event(last[2:], "property", toks[j])
            if i > 0 and prev.t == "kw" and prev.s == "new":
                if full in ("Worker", "SharedWorker") and s(j, 1) == "(" and isstr(j + 2):
                    imp("worker", toks[j + 2].v, toks[j + 2])
                if full in ("CustomEvent", "Event") and s(j, 1) == "(" and isstr(j + 2):
                    event(toks[j + 2].v, "new_event", toks[j + 2])
                if full in _API:
                    patterns[_API[full]] = patterns.get(_API[full], 0) + 1
                if full == "Function":
                    patterns["function_ctor"] = patterns.get("function_ctor", 0) + 1
            if full in ("fbq", "ga", "gtag", "_gaq.push") and s(j, 1) == "(" and isstr(j + 2) and s(j, 3) == ",":
                if isstr(j + 4) and toks[j + 2].v in ("init", "create", "config"):
                    A.literals.append({"kind": "tracking_id", "value": toks[j + 4].v, "via": f"{full}:{toks[j + 2].v}",
                                       "span": span(toks[j + 4].a, toks[j + 4].b)})
                    seen_lit.add(("tracking_id", toks[j + 4].v))
            i = j
        # --- literały
        if t.t in ("str", "tpl") and t.v:
            v = t.v
            if len(v) > 200:
                key = ("long_string", hashlib.sha256(v.encode("utf-8", "replace")).hexdigest())
                if key not in seen_lit and len(A.literals) < MAX_LITERALS:
                    seen_lit.add(key)
                    A.literals.append({"kind": "long_string", "value": key[1], "length": len(v), "span": span(t.a, t.b)})
            else:
                kind = _classify(v)
                if kind and (kind, v) not in seen_lit and len(A.literals) < MAX_LITERALS:
                    seen_lit.add((kind, v))
                    A.literals.append({"kind": kind, "value": v, "span": span(t.a, t.b)})
        i += 1
    # --- sygnatura
    norm = normalize([Tok(x.t, x.s, x.a, x.b, x.v) for x in toks])
    A.norm = norm
    A.norm_sha256 = hashlib.sha256(" ".join(norm).encode("utf-8")).hexdigest()
    A.shingles = shingles(norm)
    A.patterns = dict(sorted(patterns.items()))
    ids = [x.s for x in toks if x.t == "id"]
    lines = src.count("\n") + 1
    short = sum(1 for x in ids if len(x) <= 2)
    A.metrics = {"length": len(src), "tokens": n, "lines": lines, "avg_line_length": round(len(src) / lines, 1),
                 "identifiers": len(ids), "short_identifier_ratio": round(short / len(ids), 3) if ids else 0.0,
                 "hex_identifiers": sum(1 for x in ids if re.match(r"^_0x[0-9a-f]+$", x)),
                 "escaped_strings": sum(1 for x in toks if x.t == "str" and ("\\x" in x.s or "\\u" in x.s)),
                 "string_entropy": round(_entropy("".join(x.v for x in toks if x.t == "str")[:20000]), 2),
                 "minified_hint": lines < 10 and len(src) / lines > 300 or (len(ids) > 20 and short / len(ids) > 0.6)}
    return A
