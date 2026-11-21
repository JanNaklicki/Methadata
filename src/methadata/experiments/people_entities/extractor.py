"""Ekstraktor encji: wzmianki w tekście HTML (z rolą: wzmianka / podpis zdjęcia) i adnotacje osób widocznych na obrazach.

Cechy (family=entity):
  kind=mention        value: entity_id, entity_type, canonical, surface, role (mention|caption), field, source=html,
                      selector, view, start/end (przesunięcia w dekodowanym HTML), span_exact; evidence_refs: TextSpan
  kind=visible_person value: entity_id, role=visible_person, label_source, verified, sha256; evidence_refs: artefakt obrazu
Osoba widoczna = wyłącznie dokładna kopia (SHA-256) ręcznie opisanego obrazu referencyjnego; brak rozpoznawania twarzy."""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional

from methadata.contracts import ids
from methadata.contracts.interfaces import AdapterKind, AdapterSpec
from methadata.contracts.models import (ArtifactRef, ArtifactRole, ExtractionState, ExtractionStatus, Feature,
                                        TextSpan, ValueType)
from methadata.experiments.lexical.text import classify
from methadata.extractors.html import HtmlExtractor
from methadata.extractors.html.extractor import _decode
from methadata.extractors.html.mhtml import looks_like_mhtml, parse_mhtml
from methadata.extractors.html.parse import norm_ws, parse_html

from .lexicon import Lexicon, ReferenceImage, default_lexicon, load_references

EXTRACTOR_VERSION = "people-entities-1"
FAMILY = "entity"
KIND_MENTION = "mention"
KIND_VISIBLE = "visible_person"
ROLE_MENTION, ROLE_CAPTION, ROLE_VISIBLE = "mention", "caption", "visible_person"

_CAPTION_TAGS = {"figcaption", "caption"}
_CAPTION_CLS = re.compile(r"(caption|podpis|photo-?credit|image-?credit|source-?photo|fot\b)", re.I)
_IMG_ROLES = (ArtifactRole.creative_image, ArtifactRole.landing_screenshot, ArtifactRole.resource)


def _is_caption(n) -> bool:
    x = n
    while x is not None and x.tag != "#root":
        if x.tag in _CAPTION_TAGS or _CAPTION_CLS.search(f"{x.attrs.get('class', '')} {x.attrs.get('id', '')}"):
            return True
        x = x.parent
    return False


def _nth_occurrence(hay: str, needle: str, k: int) -> Optional[int]:
    pos = -1
    for _ in range(k + 1):
        pos = hay.find(needle, pos + 1)
        if pos < 0:
            return None
    return pos


class PeopleEntitiesExtractor:
    spec = AdapterSpec(id="people-entities-extractor", kind=AdapterKind.extractor, version=EXTRACTOR_VERSION,
                       description="Wzmianki osób, marek i produktów w HTML (rola: wzmianka/podpis) oraz adnotacje osób "
                                   "widocznych na obrazach referencyjnych (po SHA-256)")

    def __init__(self, lexicon: Optional[Lexicon] = None, references: Optional[Mapping[str, ReferenceImage]] = None,
                 html_extractor: Optional[HtmlExtractor] = None):
        self.lexicon = lexicon or default_lexicon()
        self.references = dict(load_references() if references is None else references)
        self.html = html_extractor or HtmlExtractor()

    def extract(self, observation, *, reader, optional: Mapping[str, Any] | None = None):
        oid = observation.observation_id
        hf, _ = self.html.extract(observation, reader=reader, optional=optional)
        refs = {}
        for f in hf:
            if f.kind == "text_section":
                v = f.value
                refs[(v["start"], v["end"], v["selector"])] = f.evidence_refs
        feats: list[Feature] = []
        sts: list[ExtractionStatus] = []
        seen: set[str] = set()
        n_html = 0

        def add(kind, value, ev, key):
            fid = ids.feature_id(oid, FAMILY, kind, EXTRACTOR_VERSION, key)
            if fid in seen:
                return
            seen.add(fid)
            feats.append(Feature(feature_id=fid, observation_id=oid, family=FAMILY, kind=kind, value_type=ValueType.json,
                                 value=value, extractor_version=EXTRACTOR_VERSION, evidence_refs=list(ev)))

        for art in observation.artifacts:
            if art.role in (ArtifactRole.landing_html, ArtifactRole.landing_dom):
                n_html += 1
                try:
                    raw = reader.open_bytes(ArtifactRef(artifact_id=art.artifact_id))
                    if looks_like_mhtml(raw, art.media_type, art.origin or art.local_ref):
                        main = parse_mhtml(raw).main_part()
                        text = _decode(main.data, main.charset)[0] if main else ""
                    else:
                        text = _decode(raw, None)[0]
                    doc = parse_html(text)
                except Exception as e:  # noqa: BLE001 - uszkodzony zapis to status, nie wyjątek
                    sts.append(ExtractionStatus(observation_id=oid, artifact_id=art.artifact_id,
                                                extractor_version=EXTRACTOR_VERSION, state=ExtractionState.failed,
                                                detail=f"odczyt HTML: {type(e).__name__}"))
                    continue
                view = "html" if art.role is ArtifactRole.landing_html else "dom"
                for node in doc.nodes + [doc.root]:
                    field = classify(node)
                    if field is None:
                        continue
                    role = ROLE_MENTION
                    if field != "title" and _is_caption(node):
                        field, role = "caption", ROLE_CAPTION
                    for r in node.runs:
                        t = norm_ws("".join(r.parts))
                        if not t:
                            continue
                        raw_run = text[r.start:r.end]
                        ev0 = refs.get((r.start, r.end, node.selector or "#root"), [])
                        count: dict[str, int] = {}
                        for h in self.lexicon.find(t):
                            k = count.get(h.surface, 0)
                            count[h.surface] = k + 1
                            off = _nth_occurrence(raw_run, h.surface, k) if raw_run.count(h.surface) == t.count(h.surface) else None
                            exact = off is not None
                            s0 = r.start + off if exact else r.start
                            e0 = s0 + len(h.surface) if exact else max(r.end, r.start + 1)
                            ev = ev0
                            if ev0 and isinstance(ev0[0].locator, TextSpan):
                                ev = [ArtifactRef(artifact_id=ev0[0].artifact_id, locator=TextSpan(start=s0, end=e0))]
                            add(KIND_MENTION,
                                {"entity_id": h.entity_id, "entity_type": h.entity_type, "canonical": h.canonical,
                                 "surface": h.surface, "role": role, "field": field, "source": "html",
                                 "selector": node.selector or "#root", "view": view, "start": s0, "end": e0,
                                 "span_exact": exact, "context": t[max(0, h.start - 40):h.end + 40]},
                                ev, [art.artifact_id, h.entity_id, s0, e0, role])
        for art in observation.artifacts:
            ref = self.references.get(art.sha256 or "")
            if ref is not None and art.role in _IMG_ROLES:
                add(KIND_VISIBLE,
                    {"entity_id": ref.entity_id, "entity_type": "person", "role": ROLE_VISIBLE,
                     "canonical": self.lexicon.entities[ref.entity_id].canonical if ref.entity_id in self.lexicon.entities
                     else ref.entity_id,
                     "label_source": ref.label_source, "verified": ref.verified, "sha256": ref.sha256,
                     "source": "reference_image"},
                    [ArtifactRef(artifact_id=art.artifact_id)], [art.artifact_id, ref.entity_id])
        if n_html == 0 and not any(f.kind == KIND_VISIBLE for f in feats):
            sts.append(ExtractionStatus(observation_id=oid, extractor_version=EXTRACTOR_VERSION,
                                        state=ExtractionState.missing_input, detail="brak HTML i obrazów referencyjnych"))
        else:
            nm = sum(1 for f in feats if f.kind == KIND_MENTION)
            nv = len(feats) - nm
            sts.append(ExtractionStatus(observation_id=oid, extractor_version=EXTRACTOR_VERSION, state=ExtractionState.ok,
                                        detail=f"wzmianki={nm}, osoby_widoczne={nv}; słownik {self.lexicon.version}"))
        return feats, sts
