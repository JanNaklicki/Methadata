"""TF-IDF (słowa i znakowe n-gramy) + cosinus, czysty Python. Model (df, N) da się utrwalić do JSON."""
from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from typing import Iterable, Mapping, Optional, Sequence

ANALYZERS = ("word", "char")
CHAR_NGRAM = (3, 5)
MIN_DF_STORED = 2  # df=1 nie jest zapisywane: termin spoza modelu dostaje idf jak df<=1 (patrz idf())
_WORD = re.compile(r"\w{2,}", re.U)


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def words(text: str) -> list[str]:
    return _WORD.findall(normalize(text))


def char_ngrams(text: str, lo: int = CHAR_NGRAM[0], hi: int = CHAR_NGRAM[1]) -> list[str]:
    out: list[str] = []
    for w in words(text):
        p = f" {w} "
        for n in range(lo, hi + 1):
            out += [p[i:i + n] for i in range(len(p) - n + 1)] if len(p) >= n else []
    return out


def terms(text: str, analyzer: str) -> list[str]:
    return words(text) if analyzer == "word" else char_ngrams(text)


class IdfModel:
    """Słownik i IDF jednego pola dla jednego analizatora: N dokumentów, df terminów (df >= MIN_DF_STORED)."""

    def __init__(self, n_docs: int = 0, df: Optional[Mapping[str, int]] = None):
        self.n_docs = n_docs
        self.df = dict(df or {})

    def idf(self, term: str) -> float:
        # wygładzony: ln((1+N)/(1+df)) + 1; brak terminu w modelu => df=1 (termin co najmniej z tego dokumentu)
        return math.log((1 + self.n_docs) / (1 + self.df.get(term, 1))) + 1.0

    @classmethod
    def fit(cls, docs: Iterable[Iterable[str]], min_df_stored: int = MIN_DF_STORED) -> "IdfModel":
        df: Counter = Counter()
        n = 0
        for d in docs:
            n += 1
            df.update(set(d))
        return cls(n, {t: c for t, c in df.items() if c >= min_df_stored})


def vectorize(tokens: Sequence[str], model: IdfModel) -> dict[str, float]:
    tf = Counter(tokens)
    return {t: (1.0 + math.log(c)) * model.idf(t) for t, c in tf.items()}


def norm(v: Mapping[str, float]) -> float:
    return math.sqrt(sum(x * x for x in v.values()))


def cosine(a: Mapping[str, float], b: Mapping[str, float]) -> Optional[float]:
    """None, gdy któryś wektor jest pusty (brak podstaw, nie zerowe podobieństwo)."""
    na, nb = norm(a), norm(b)
    if na == 0 or nb == 0:
        return None
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b[t] for t, x in a.items() if t in b) / (na * nb)


def contributions(a: Mapping[str, float], b: Mapping[str, float]) -> list[tuple[str, float]]:
    """Wkład terminów wspólnych do cosinusa, malejąco (remis: alfabetycznie)."""
    na, nb = norm(a), norm(b)
    if na == 0 or nb == 0:
        return []
    c = [(t, a[t] * b[t] / (na * nb)) for t in a if t in b]
    return sorted(c, key=lambda x: (-x[1], x[0]))


class LexicalModel:
    """IDF dla (pole, analizator). Utrwalany na części rozwojowej: to_json()/from_json()."""
    VERSION = "lexical-idf-1"

    def __init__(self, parts: Optional[Mapping[tuple[str, str], IdfModel]] = None, meta: Optional[dict] = None):
        self.parts = dict(parts or {})
        self.meta = dict(meta or {})

    def get(self, field: str, analyzer: str) -> IdfModel:
        return self.parts.get((field, analyzer)) or IdfModel()

    @classmethod
    def fit(cls, docs: Sequence[Mapping[str, str]], *, meta: Optional[dict] = None) -> "LexicalModel":
        """docs: lista {pole: tekst}; dokument bez pola nie liczy się do N tego pola."""
        fields = sorted({f for d in docs for f in d})
        parts = {(f, an): IdfModel.fit(terms(d[f], an) for d in docs if d.get(f)) for f in fields for an in ANALYZERS}
        return cls(parts, {"n_docs": len(docs), **(meta or {})})

    def to_json(self) -> str:
        return json.dumps({"version": self.VERSION, "meta": self.meta, "char_ngram": list(CHAR_NGRAM),
                           "min_df_stored": MIN_DF_STORED,
                           "parts": {f"{f}|{a}": {"n_docs": m.n_docs, "df": dict(sorted(m.df.items()))}
                                     for (f, a), m in sorted(self.parts.items())}},
                          ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> "LexicalModel":
        d = json.loads(s)
        if d.get("version") != cls.VERSION:
            raise ValueError(f"nieobsługiwana wersja modelu: {d.get('version')!r}")
        return cls({tuple(k.split("|", 1)): IdfModel(v["n_docs"], v["df"]) for k, v in d["parts"].items()}, d.get("meta"))
