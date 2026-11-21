"""Elementy zestawu strony z cech X01 (formularze), X02 (arkusze CSS, skrypty JS) i E12 (klasy, odnośniki).

Element = (kind, value). Rodzaje: `css_sheet` (layout_sha256 X02), `class` (nazwa klasy użyta w HTML), `script` (znormalizowany
sha256 skryptu X02: zewnętrzny lub inline), `form` (sygnatura budowy formularza X01). Każdy niesie pochodzenie
(`own` | `library` | `browser`) i dowody (ArtifactRef z lokalizacją)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Sequence

from methadata.contracts import ArtifactRef, Feature

from . import filters
from .extractor import FAMILY as KIT, KIND_CLASS, KIND_SCRIPT, KIND_SHEET

CSS_SHEET, CLASS, SCRIPT, FORM = "css_sheet", "class", "script", "form"
KINDS = (CSS_SHEET, CLASS, SCRIPT, FORM)
MIN_FORM_FIELDS = 2


@dataclass
class Element:
    kind: str
    value: str
    label: str
    origin: str = "own"          # own | library | browser
    refs: list = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind, self.value)


def form_signature(value: Mapping) -> Optional[str]:
    """Budowa formularza bez wartości zmiennych: metoda + uporządkowany zbiór (tag, typ, nazwa pola)."""
    fields = value.get("fields") or []
    sig = sorted({(f.get("tag") or "", f.get("type") or "", (f.get("name") or "").lower()) for f in fields})
    sig = [s for s in sig if s[0] in ("input", "select", "textarea")]
    if len(sig) < MIN_FORM_FIELDS:
        return None
    raw = json.dumps([(value.get("method") or "get").lower(), sig], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _artifact_names(observation) -> dict[str, str]:
    return {a.artifact_id: " ".join(x.replace(" ", "%20") for x in (a.url, a.origin, a.local_ref) if x)
            for a in observation.artifacts}


def extract_elements(observation, features: Sequence[Feature]) -> dict[tuple[str, str], Element]:
    names = _artifact_names(observation)
    out: dict[tuple[str, str], Element] = {}

    def put(kind: str, value: str, label: str, origin: str, refs: Iterable[ArtifactRef]):
        el = out.get((kind, value))
        if el is None:
            el = out[(kind, value)] = Element(kind, value, label, origin)
        elif el.origin != origin and origin != "own":
            el.origin = origin  # każde wystąpienie w bibliotece/szablonie przeglądarki oznacza element jako cudzy
        for r in refs:
            if r not in el.refs:
                el.refs.append(r)

    def host_name(f: Feature) -> str:
        return names.get(f.evidence_refs[0].artifact_id, "") if f.evidence_refs else ""

    script_refs = {}
    for f in features:
        if f.family == KIT and f.kind in (KIND_SCRIPT, KIND_SHEET) and isinstance(f.value, dict):
            script_refs[f.value.get("url", "")] = f
    for f in features:
        if f.family in ("css", "js") and f.kind in ("layout_sha256", "normalized_sha256") and isinstance(f.value, str):
            nm = host_name(f)
            origin = "library" if filters.is_library_url(nm) else ("browser" if filters.is_browser_url(nm) else "own")
            kind = CSS_SHEET if f.family == "css" else SCRIPT
            where = nm.split(" ")[0] or "inline"
            put(kind, f.value, f"{'arkusz' if kind == CSS_SHEET else 'skrypt'} {where[:80]}", origin, f.evidence_refs)
        elif f.family == KIT and f.kind == KIND_CLASS and isinstance(f.value, str):
            put(CLASS, f.value, f"klasa .{f.value}", filters.class_origin(f.value) or "own", f.evidence_refs)
        elif f.kind == "form" and isinstance(f.value, dict) and f.family in ("html", "dom"):
            sig = form_signature(f.value)
            if sig:
                fields = f.value.get("fields") or []
                put(FORM, sig, f"formularz {(f.value.get('method') or 'get').upper()} z {len(fields)} polami", "own", f.evidence_refs)
    return out
