"""Tolerancyjny parser HTML (biblioteka standardowa) z dokładnymi pozycjami w znakach.

Nie wykonuje skryptów ani nie pobiera zasobów. Każdy węzeł ma zakres w dekodowanym tekście źródła
(`TextSpan`: znaki Unicode, koniec wyłącznie), więc dowód da się odnaleźć w oryginale.
"""
from __future__ import annotations

import html as _html
import re
from html.parser import HTMLParser
from typing import Optional

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
BLOCK = {"address", "article", "aside", "blockquote", "body", "dd", "details", "dialog", "div", "dl", "dt",
         "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
         "hgroup", "li", "main", "nav", "ol", "p", "pre", "section", "summary", "table", "tbody", "td", "tfoot",
         "th", "thead", "title", "tr", "ul", "button", "label", "option", "textarea", "html", "head"}
RAW_TEXT = {"script", "style"}
AUTOCLOSE = {"p": {"p"}, "li": {"li"}, "dt": {"dt", "dd"}, "dd": {"dt", "dd"}, "option": {"option"},
             "tr": {"tr", "td", "th"}, "td": {"td", "th"}, "th": {"td", "th"}}
_HIDDEN_RE = re.compile(r"(display\s*:\s*none|visibility\s*:\s*hidden)", re.I)
_REF_RE = re.compile(r"&(?:#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);?")
_WS = re.compile(r"\s+")


def norm_ws(s: str) -> str:
    return _WS.sub(" ", s).strip()


class Run:
    __slots__ = ("start", "end", "parts")

    def __init__(self, start: int):
        self.start, self.end, self.parts = start, start, []


class Node:
    def __init__(self, tag: str, attrs: dict, parent: Optional["Node"], open_start: int, open_end: int):
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.open_start, self.open_end = open_start, open_end
        self.end: int = open_end                 # koniec elementu (po znaczniku zamykającym)
        self.children: list[Node] = []
        self.depth = 0 if parent is None else parent.depth + 1
        self.hidden = False
        self.selector = ""
        self.runs: list[Run] = []                # tekst bezpośrednio w bloku (bez zagnieżdżonych bloków)
        self.run_open = False
        self.raw_start: Optional[int] = None     # treść script/style
        self.raw_end: Optional[int] = None
        self.raw_parts: list[str] = []
        self._counters: dict[str, int] = {}

    @property
    def raw_text(self) -> str:
        return "".join(self.raw_parts)


class Parsed:
    def __init__(self, text: str):
        self.text = text
        self.nodes: list[Node] = []
        self.root = Node("#root", {}, None, 0, 0)
        self.root.end = len(text)


class _P(HTMLParser):
    def __init__(self, text: str):
        super().__init__(convert_charrefs=False)
        self.text = text
        self.out = Parsed(text)
        self.stack: list[Node] = [self.out.root]
        self._line_starts = [0]
        for m in re.finditer("\n", text):
            self._line_starts.append(m.end())

    def pos(self) -> int:
        line, col = self.getpos()
        return self._line_starts[line - 1] + col

    # --- znaczniki
    def _open(self, tag, attrs, void_like: bool):
        start = self.pos()
        raw = self.get_starttag_text() or ""
        top = self.stack[-1]
        if tag in AUTOCLOSE and top.tag in AUTOCLOSE[tag]:
            self._close_to(top, start)
        parent = self.stack[-1]
        d = {}
        for k, v in attrs:
            d.setdefault(k.lower(), v if v is not None else "")
        n = Node(tag, d, parent, start, start + len(raw))
        n.end = n.open_end
        idn = d.get("id")
        parent._counters[tag] = parent._counters.get(tag, 0) + 1
        seg = f"{tag}#{idn}" if idn and re.fullmatch(r"[A-Za-z][\w\-]*", idn) else f"{tag}:nth-of-type({parent._counters[tag]})"
        n.selector = seg if parent.tag == "#root" else f"{parent.selector} > {seg}"
        n.hidden = parent.hidden or "hidden" in d or bool(_HIDDEN_RE.search(d.get("style", "")))
        parent.children.append(n)
        self.out.nodes.append(n)
        if tag in BLOCK:
            self._break(parent)
        if not (void_like or tag in VOID):
            self.stack.append(n)

    def handle_starttag(self, tag, attrs):
        self._open(tag, attrs, False)

    def handle_startendtag(self, tag, attrs):
        self._open(tag, attrs, True)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                start = self.pos()
                gt = self.text.find(">", start)
                end = gt + 1 if gt >= 0 else len(self.text)
                node = self.stack[i]
                self._close_to(node, start)
                node.end = end
                if node.tag in BLOCK:
                    self._break(self._block_of(len(self.stack) - 1))
                return

    def _close_to(self, node: Node, at: int):
        while self.stack[-1] is not node:
            x = self.stack.pop()
            x.end = at
        self.stack.pop()
        node.end = at

    def _block_of(self, idx: int) -> Node:
        for n in reversed(self.stack[: idx + 1]):
            if n.tag in BLOCK or n.tag == "#root":
                return n
        return self.out.root

    def _break(self, block: Node):
        # nearest block ancestor (including given) closes current run
        n: Optional[Node] = block
        while n is not None and n.tag not in BLOCK and n.tag != "#root":
            n = n.parent
        if n is not None:
            n.run_open = False

    # --- tekst
    def _text(self, start: int, end: int, s: str):
        top = self.stack[-1]
        if top.tag in RAW_TEXT:
            if top.raw_start is None:
                top.raw_start = start
            top.raw_end = end
            top.raw_parts.append(self.text[start:end])
            return
        if end <= start:
            return
        blk = self._block_of(len(self.stack) - 1)
        if not blk.run_open or not blk.runs:
            blk.runs.append(Run(start))
            blk.run_open = True
        r = blk.runs[-1]
        r.end = end
        r.parts.append(s)

    def handle_data(self, data):
        start = self.pos()
        self._text(start, start + len(data), data)

    def _ref(self):
        start = self.pos()
        m = _REF_RE.match(self.text, start)
        if not m:
            return start, start + 1, self.text[start:start + 1]
        raw = m.group(0)
        return start, m.end(), _html.unescape(raw)

    def handle_entityref(self, name):
        s, e, u = self._ref()
        self._text(s, e, u)

    def handle_charref(self, name):
        s, e, u = self._ref()
        self._text(s, e, u)

    def unknown_decl(self, data):  # CDATA itp.
        pass


def parse_html(text: str) -> Parsed:
    p = _P(text)
    p.feed(text)
    p.close()
    while len(p.stack) > 1:
        n = p.stack.pop()
        n.end = len(text)
    return p.out
