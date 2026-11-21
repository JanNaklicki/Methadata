"""Ślady materiału z cech X01/E02: zasób, formularz, fragment tekstu (shingle słów).

Ślad = (trace_id, kind, label) + dowody (feature_id, ArtifactRef). Wejściem są wyłącznie cechy z kontraktu."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence
from urllib.parse import urlsplit

from methadata.contracts import ArtifactRef

KIND_RESOURCE = "resource"
KIND_FORM = "form"
KIND_TEXT = "text"
KINDS = (KIND_RESOURCE, KIND_FORM, KIND_TEXT)

_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class TraceConfig:
    shingle_words: int = 6      # długość fragmentu tekstu w słowach
    shingle_mod: int = 1        # zachowaj shingle o hash % mod == 0 (1 = wszystkie); deterministyczne próbkowanie
    min_text_words: int = 6     # krótsze pola nie dają śladów tekstowych
    min_form_fields: int = 1    # formularz bez pól nie jest śladem
    use_hidden_text: bool = False
    kinds: tuple = KINDS

    def to_dict(self) -> dict:
        return {"shingle_words": self.shingle_words, "shingle_mod": self.shingle_mod,
                "min_text_words": self.min_text_words, "min_form_fields": self.min_form_fields,
                "use_hidden_text": self.use_hidden_text, "kinds": list(self.kinds)}


@dataclass
class Hit:
    """Wystąpienie śladu w jednym materiale: skąd pochodzi (identyfikatory źródłowe)."""
    observation_id: str
    feature_ids: list[str] = field(default_factory=list)
    refs: list[ArtifactRef] = field(default_factory=list)

    def add(self, feature_id: str, refs: Iterable[ArtifactRef]):
        if feature_id not in self.feature_ids:
            self.feature_ids.append(feature_id)
        for r in refs:
            if r not in self.refs:
                self.refs.append(r)


def _tid(kind: str, key: str) -> str:
    return f"{kind[:3]}:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def resource_key(value: dict) -> Optional[tuple[str, str]]:
    """(klucz, etykieta) zasobu zewnętrznego; wg zawartości (sha256), gdy znana, inaczej host+ścieżka bez zapytania."""
    if value.get("rel") == "link" or value.get("state") == "inline":
        return None
    resolved = value.get("resolved") or ""
    sp = urlsplit(resolved)
    if sp.scheme not in ("http", "https") or not sp.netloc:
        return None
    label = f"{sp.netloc.lower()}{sp.path}"
    if value.get("sha256"):
        return "sha256:" + value["sha256"], f"{label} [{value['sha256'][:10]}]"
    if not sp.path.strip("/"):
        return None  # sam host (np. strona główna) nie jest śladem zasobu
    return "url:" + label, label


def form_key(value: dict) -> Optional[tuple[str, str]]:
    fields = value.get("fields") or []
    sig = sorted(f"{f.get('type')}:{(f.get('name') or '').lower()}" for f in fields if f.get("tag") != "button")
    if not sig:
        return None
    sp = urlsplit(value.get("action_resolved") or value.get("action") or "")
    action = f"{sp.netloc.lower()}{sp.path}"
    key = "|".join([value.get("method") or "get", action, ",".join(sig)])
    return key, f"form {value.get('method', 'get').upper()} {action or '(bez action)'} pola=[{', '.join(sig)}]"


def shingles(text: str, n: int, mod: int = 1) -> list[tuple[str, str]]:
    words = [w.lower() for w in _WORD.findall(text)]
    out = []
    for i in range(len(words) - n + 1):
        s = " ".join(words[i:i + n])
        if mod > 1 and int(hashlib.sha256(s.encode("utf-8")).hexdigest()[:8], 16) % mod:
            continue
        out.append((s, s))
    return out


def extract_traces(observation_id: str, features: Sequence[Any], cfg: TraceConfig = TraceConfig()) -> dict[str, tuple[str, str, Hit]]:
    """cechy jednej obserwacji -> {trace_id: (kind, label, Hit)}. Deterministyczne, niezależne od kolejności cech."""
    feats = sorted(features, key=lambda f: f.feature_id)
    out: dict[str, tuple[str, str, Hit]] = {}

    def put(kind: str, key: str, label: str, f):
        if kind not in cfg.kinds:
            return
        tid = _tid(kind, key)
        if tid not in out:
            out[tid] = (kind, label, Hit(observation_id))
        out[tid][2].add(f.feature_id, f.evidence_refs)

    has_lex = any(f.family == "lexical" and f.kind == "text_field" for f in feats)
    for f in feats:
        if f.kind == "resource_edge" and isinstance(f.value, dict):
            k = resource_key(f.value)
            if k:
                put(KIND_RESOURCE, k[0], k[1], f)
        elif f.kind == "form" and isinstance(f.value, dict):
            if len(f.value.get("fields") or []) >= cfg.min_form_fields:
                k = form_key(f.value)
                if k:
                    put(KIND_FORM, k[0], k[1], f)
        else:
            text = None
            if f.family == "lexical" and f.kind == "text_field" and isinstance(f.value, dict):
                if f.value.get("field") != "comments" and f.value.get("view", "html") != "dom":
                    text = f.value.get("text")
            elif not has_lex and f.kind == "text_section" and isinstance(f.value, dict):
                if (cfg.use_hidden_text or not f.value.get("hidden")) and f.value.get("tag") not in ("title", "script", "style"):
                    text = f.value.get("text")
            if text and len(_WORD.findall(text)) >= cfg.min_text_words:
                for key, label in shingles(text, cfg.shingle_words, cfg.shingle_mod):
                    put(KIND_TEXT, key, label, f)
    return out
