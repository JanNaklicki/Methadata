"""Ekstrakcja ról scenariusza regułami: kto, co się wydarzyło, obietnica, produkt, wezwanie.

Każde wypełnione pole ma cytat będący dosłownym podciągiem tekstu sekcji (`text[start:end] == quote`) i odwołania
do artefaktów. Brak cytatu = pole puste (None). Nazwy własne są wykrywane heurystycznie (wielka litera poza początkiem
zdania lub znana skądinąd w dokumencie, akronimy) i maskowane przy porównaniu układu ról."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, Sequence

from .lexicon import LEXICON

SLOTS = ("who", "event", "promise", "product", "call")
FIELDS_USED = ("title", "body", "cta")  # komentarze użytkowników nie są częścią scenariusza

_TOK = re.compile(r"\w+(?:[-’'.]\w+)*", re.U)
_OPEN = set("„\"“«(—–-:[")
_SENT_END = re.compile(r"(?<=[.!?…])[”\"»)]*\s+(?=[„\"“«A-ZŻŹĆĄŚĘŁÓŃ0-9—–])|\n+")
_ABBR = ("zł", "tys", "mln", "mld", "np", "itd", "im", "ul", "godz", "dr", "prof")


def stem(tok: str, n: int = 6) -> str:
    return tok.lower()[:n]


@dataclass(frozen=True)
class Name:
    start: int
    end: int
    text: str
    stems: tuple[str, ...]
    kind: str  # person | org (akronim)


@dataclass
class Slot:
    slot: str
    quote: str
    start: int            # w znakach tekstu sekcji
    end: int
    section: int          # indeks sekcji wejściowej
    field: str
    cls: Optional[str] = None            # klasa ramy (np. event=attack), None gdy nie dotyczy
    names: list[str] = field(default_factory=list)   # nazwy własne w cytacie
    name_stems: list[str] = field(default_factory=list)
    masked: list[str] = field(default_factory=list)  # znormalizowane tokeny po zamianie nazw/liczb na znaczniki
    extra: dict[str, Any] = field(default_factory=dict)

    def as_value(self) -> dict:
        return {"slot": self.slot, "quote": self.quote, "start": self.start, "end": self.end, "field": self.field,
                "cls": self.cls, "names": self.names, "name_stems": self.name_stems, "masked": self.masked,
                "extra": self.extra}


class SlotBackend(Protocol):
    """Opcjonalny backend modelowy (domyślnie wyłączony). Zwraca {slot: {"quote": str, "cls": str|None}}.
    Cytat musi występować dosłownie w tekście którejś sekcji, inaczej pole zostaje puste."""
    def extract(self, sections: Sequence[Mapping[str, Any]]) -> Mapping[str, Mapping[str, Any]]: ...


def sentences(text: str) -> list[tuple[int, int]]:
    out, pos = [], 0
    for m in _SENT_END.finditer(text):
        seg = text[pos:m.start() + (1 if text[m.start():m.start() + 1] in ".!?…" else 0)]
        if m.group(0).strip() == "" and seg.rstrip().lower().endswith(_ABBR) and "\n" not in m.group(0):
            continue
        s = pos
        e = m.start() + len(text[m.start():m.end()].rstrip())
        e = pos + len(text[pos:e].rstrip()) if e > pos else e
        if text[s:e].strip():
            out.append((s, e))
        pos = m.end()
    if text[pos:].strip():
        out.append((pos, pos + len(text[pos:].rstrip())))
    return out


def _is_name_tok(t: str, sentence_start: bool, known: set[str], stop: set[str]) -> Optional[str]:
    if t in stop or not t[0].isalpha() and not t[0].isdigit():
        return None
    if t.isupper() and (len(t) > 5 or t.isdigit()):
        return None  # krzyczący wyraz (PILNE, REKLAMA), nie nazwa
    if t.isupper():
        return "org" if len(t) >= 2 else None
    if not t[0].isupper():
        return None
    if sentence_start and stem(t) not in known:
        return None
    return "person"


def find_names(text: str, known: set[str], stop: set[str], stop_stems: Sequence[str] = ()) -> list[Name]:
    toks = list(_TOK.finditer(text))
    sents = sentences(text)
    starts = {s for s, _ in sents}
    flags = []
    letters = [c for c in text if c.isalpha()]
    shouting = bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.6
    for i, m in enumerate(toks):
        prev = text[toks[i - 1].end():m.start()] if i else " "
        at_start = i == 0 or m.start() in starts or any(c in _OPEN for c in prev) or bool(re.search(r"[.!?…]", prev))
        w = m.group(0).rstrip(".")
        fl = None if shouting or stem(w, 5) in stop_stems or stem(w) in stop_stems else _is_name_tok(w, at_start, known, stop)
        flags.append(fl)
    for i in range(len(toks) - 1):  # „Krzysztof Stanowski” na początku zdania: sąsiedztwo dwóch wyrazów z wielką literą
        if flags[i] is None and not shouting and toks[i].group(0)[0].isupper() and not toks[i].group(0).isupper() \
                and flags[i + 1] is not None and text[toks[i].end():toks[i + 1].start()].strip() == "" \
                and toks[i].group(0) not in stop:
            flags[i] = "person"
    out: list[Name] = []
    i = 0
    while i < len(toks):
        if flags[i] is None:
            i += 1
            continue
        j = i
        while j + 1 < len(toks) and flags[j + 1] is not None and text[toks[j].end():toks[j + 1].start()].strip() in ("", "„", "“", "\""):
            j += 1
        words = [toks[k].group(0).rstrip(".") for k in range(i, j + 1)]
        kinds = {flags[k] for k in range(i, j + 1)}
        out.append(Name(toks[i].start(), toks[j].end(), text[toks[i].start():toks[j].end()],
                        tuple(stem(w) for w in words), "org" if kinds == {"org"} else "person"))
        i = j + 1
    return out


def known_stems(texts: Sequence[str], stop: set[str]) -> set[str]:
    """Rdzenie wyrazów z wielką literą w środku zdania (poza początkiem i po otwarciu cytatu)."""
    kn: set[str] = set()
    for t in texts:
        toks = list(_TOK.finditer(t))
        starts = {s for s, _ in sentences(t)}
        for i, m in enumerate(toks):
            prev = t[toks[i - 1].end():m.start()] if i else " "
            w = m.group(0)
            if i and m.start() not in starts and not any(c in _OPEN for c in prev) and not re.search(r"[.!?…]", prev) \
                    and w[0].isupper() and w not in stop and not (w.isupper() and len(w) > 5):
                kn.add(stem(w))
    return kn


def mask_tokens(text: str, names: Sequence[Name], base: int, stoptok: set[str]) -> list[str]:
    """Tokeny cytatu: nazwy własne -> NAME, liczby -> NUM, pozostałe: rdzeń 6 znaków (bez słów funkcyjnych)."""
    out: list[str] = []
    spans = [(n.start - base, n.end - base) for n in names]
    cur = None
    for m in _TOK.finditer(text):
        k = next((x for x, sp in enumerate(spans) if sp[0] <= m.start() < sp[1]), None)
        if k is not None:
            if k != cur:
                out.append("NAME")
            cur = k
            continue
        cur = None
        w = m.group(0).lower()
        if re.fullmatch(r"[\d.,]+", w):
            out.append("NUM")
        elif len(w) >= 3 and w not in stoptok:
            out.append(w[:6])
    return out


class _Ctx:
    def __init__(self, sections: Sequence[Mapping[str, Any]], lex: Mapping[str, Any]):
        self.lex = lex
        self.stop = set(lex["stop_names"])
        self.stoptok = set(lex["stop_tokens"])
        self.generic = set(lex.get("generic_name_stems", ()))
        self.secs = [(i, s) for i, s in enumerate(sections) if s["field"] in FIELDS_USED and s["text"].strip()]
        known = known_stems([s["text"] for _, s in self.secs], self.stop)
        self.names = {i: find_names(s["text"], known, self.stop, tuple(lex.get("stop_name_stems", ()))) for i, s in self.secs}
        self.sents: list[tuple[int, int, int, str]] = []  # (sec, start, end, field)
        for i, s in self.secs:
            for a, b in sentences(s["text"]):
                self.sents.append((i, a, b, s["field"]))
        self.text = {i: s["text"] for i, s in self.secs}

    def sent_text(self, k):
        i, a, b, _ = self.sents[k]
        return self.text[i][a:b]

    def low(self, k):
        """Zdanie małymi literami, z nazwami własnymi zastąpionymi spacjami (nazwa nie jest dowodem roli)."""
        i, a, b, _ = self.sents[k]
        t = list(self.text[i][a:b].lower())
        for n in self.names[i]:
            for x in range(max(a, n.start), min(b, n.end)):
                t[x - a] = " "
        return "".join(t)

    def make(self, slot: str, i: int, a: int, b: int, fld: str, cls=None, extra=None) -> Slot:
        t = self.text[i]
        while a < b and t[a].isspace():
            a += 1
        while b > a and t[b - 1].isspace():
            b -= 1
        nm = [n for n in self.names[i] if n.start >= a and n.end <= b]
        return Slot(slot, t[a:b], a, b, i, fld, cls, [n.text for n in nm],
                    sorted({s for n in nm for s in n.stems if len(s) >= 3 and s not in self.generic}),
                    mask_tokens(t[a:b], nm, a, self.stoptok), extra or {})


def _tighten(t: str, a: int, b: int, pos: int, limit: int) -> tuple[int, int]:
    """Skraca zbyt długie zdanie do klauzuli wokół pozycji `pos` (granice: przecinek, myślnik, średnik, dwukropek)."""
    if b - a <= limit:
        return a, b
    seps = [m.end() for m in re.finditer(r"[,;:]\s|\s[—–]\s", t[a:b])]
    lo = max([a] + [a + s for s in seps if a + s <= pos])
    hi = min([b] + [a + m.start() for m in re.finditer(r"[,;:]\s|\s[—–]\s", t[a:b]) if a + m.start() >= pos])
    if hi - lo < 40:
        lo, hi = max(a, pos - limit // 2), min(b, pos + limit // 2)
    return lo, hi


def _rx(patterns: Sequence[str]) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(f"(?:{p})" for p in patterns) + ")", re.I)


def _count(rx: re.Pattern, s: str) -> int:
    return len(rx.findall(s))


def extract_slots(sections: Sequence[Mapping[str, Any]], lex: Mapping[str, Any] = LEXICON,
                  backend: Optional[SlotBackend] = None) -> dict[str, Optional[Slot]]:
    """sections: [{field, text, refs}] w kolejności dokumentu. Zwraca {slot: Slot|None}."""
    cx = _Ctx(sections, lex)
    res: dict[str, Optional[Slot]] = {s: None for s in SLOTS}
    if not cx.sents:
        return res
    limit = int(lex["max_quote_chars"])

    # --- co się wydarzyło
    ev_rx = {c: _rx(p) for c, p in lex["event"].items()}
    best = None
    for k, (i, a, b, fld) in enumerate(cx.sents):
        low = cx.low(k)
        hits = {c: _count(r, low) for c, r in ev_rx.items()}
        n = sum(hits.values())
        if not n:
            continue
        score = n + (2 if fld == "title" else 0) - (0.5 if fld == "cta" else 0)
        if best is None or score > best[0]:
            best = (score, k, max(hits, key=lambda c: (hits[c], -list(hits).index(c))))
    ev_k = None
    if best:
        _, ev_k, cls = best
        i, a, b, fld = cx.sents[ev_k]
        t = cx.text[i]
        m = next(ev_rx[cls].finditer(cx.low(ev_k)))
        a2, b2 = _tighten(t, a, b, a + m.start(), limit)
        res["event"] = cx.make("event", i, a2, b2, fld, cls)

    # --- kto: nazwa osoby w zdaniu zdarzenia, a bez zdarzenia w tytule
    cand = []
    if ev_k is not None:
        i, a, b, fld = cx.sents[ev_k]
        cand = [(i, n, fld) for n in cx.names[i] if a <= n.start and n.end <= b and n.kind == "person"]
    if not cand:
        cand = [(i, n, "title") for i, s in cx.secs if s["field"] == "title"
                for n in cx.names[i] if n.kind == "person"][:1]
    if cand:
        i, n, fld = cand[0]
        t = cx.text[i]
        toks = list(_TOK.finditer(t))
        li = next((x for x, m in enumerate(toks) if m.start() >= n.start), 0)
        ri = max(x for x, m in enumerate(toks) if m.end() <= n.end)
        sa, sb = next(((a, b) for a, b in sentences(t) if a <= n.start < b), (0, len(t)))
        left = [m for m in toks[max(0, li - 3):li] if m.start() >= sa]
        right = [m for m in toks[ri + 1:ri + 4] if m.end() <= sb]
        a = left[0].start() if left else n.start
        b = right[-1].end() if right else n.end
        who = cx.make("who", i, a, b, fld, "person")
        prof_rx = _rx([f"{p}\\w*" for p in lex["profession"]])
        prof = None
        for k, (si, sa2, sb2, sf) in enumerate(cx.sents[:12]):
            m = prof_rx.search(cx.text[si][sa2:sb2].lower())
            if m:
                prof = (m.group(0)[:6], cx.sent_text(k))
                break
        who.extra = {"profession": prof[0] if prof else None, "profession_quote": prof[1][:200] if prof else None,
                     "mention": n.text}
        res["who"] = who

    # --- obietnica
    strong = {c: _rx(p) for c, p in lex["promise"].items()}
    weak = _rx(lex["promise_weak"])
    pen = _rx(lex["promise_penalty"])
    noun_any = re.compile(r"\b(?:platform|aplikacj)\w*", re.I)
    gen_any = _rx(lex["product_generic"])
    best = None
    for k, (i, a, b, fld) in enumerate(cx.sents):
        if k == ev_k:
            continue
        low = cx.low(k)
        hits = {c: _count(r, low) for c, r in strong.items()}
        if not sum(hits.values()):
            continue
        n = (2 * sum(hits.values()) + _count(weak, low) + 2 * sum(1 for v in hits.values() if v)
             + (1.5 if noun_any.search(low) or gen_any.search(low) else 0)
             - (3 if pen.search(low) else 0) - (3 if cx.sent_text(k).lstrip().startswith(("—", "–")) else 0))
        if best is None or n > best[0]:
            best = (n, k, max(hits, key=lambda c: (hits[c], -list(hits).index(c))))
    if best:
        _, k, cls = best
        i, a, b, fld = cx.sents[k]
        t = cx.text[i]
        m = strong[cls].search(cx.low(k))
        a2, b2 = _tighten(t, a, b, a + m.start(), limit)
        res["promise"] = cx.make("promise", i, a2, b2, fld, cls)

    # --- produkt: rzeczownik produktu + (opcjonalnie) nazwa własna bezpośrednio po nim
    nouns = lex["product_nouns"]
    noun_rx = re.compile(r"\b(" + "|".join(nouns) + r")\w*", re.I)
    gen_rx = _rx(lex["product_generic"])
    best = None
    for k, (i, a, b, fld) in enumerate(cx.sents):
        t = cx.text[i]
        for m in noun_rx.finditer(t[a:b]):
            end = a + m.end()
            nm = next((n for n in cx.names[i] if 0 <= n.start - end <= 1 and n.end <= b), None)
            score = (3 if nm else 0) + (1 if gen_rx.search(t[a:b]) else 0) + (0.5 if fld == "body" else 0)
            if best is None or score > best[0]:
                best = (score, k, a + m.start(), nm, nouns[m.group(1).lower()])
    if best and best[0] >= 1:
        _, k, pos, nm, cls = best
        i, a, b, fld = cx.sents[k]
        t = cx.text[i]
        lo, hi = _tighten(t, a, b, pos, limit)
        if nm:
            lo, hi = min(lo, nm.start), max(hi, nm.end)
        s = cx.make("product", i, lo, hi, fld, cls)
        s.extra = {"product_name": nm.text if nm else None}
        res["product"] = s

    # --- wezwanie: pole CTA, potem zdanie z czasownikiem/rzeczownikiem wezwania
    call = {c: _rx(p) for c, p in lex["call"].items()}
    urg = _rx(lex["call_urgency"])
    best = None
    for k, (i, a, b, fld) in enumerate(cx.sents):
        if k == ev_k:
            continue
        low = cx.low(k)
        hits = {c: _count(r, low) for c, r in call.items()}
        n = sum(hits.values())
        if not n:
            continue
        score = n + (3 if fld == "cta" else 0) + (1 if urg.search(low) else 0) - (0.5 if fld == "title" else 0)
        if best is None or score > best[0]:
            best = (score, k, max(hits, key=lambda c: (hits[c], -list(hits).index(c))))
    if best:
        _, k, cls = best
        i, a, b, fld = cx.sents[k]
        t = cx.text[i]
        m = call[cls].search(cx.low(k))
        a2, b2 = _tighten(t, a, b, a + m.start(), limit)
        res["call"] = cx.make("call", i, a2, b2, fld, cls)

    if backend is not None:
        res = _apply_backend(cx, res, backend, sections)
    return res


def _apply_backend(cx: _Ctx, res: dict, backend: SlotBackend, sections) -> dict:
    """Wynik modelu ma pierwszeństwo, ale tylko z cytatem dosłownie obecnym w tekście."""
    for slot, v in backend.extract(sections).items():
        if slot not in SLOTS or not v or not v.get("quote"):
            continue
        q = v["quote"]
        for i, s in cx.secs:
            p = s["text"].find(q)
            if p >= 0:
                res[slot] = cx.make(slot, i, p, p + len(q), s["field"], v.get("cls"), {"backend": True})
                break
    return res
