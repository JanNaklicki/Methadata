"""Ekstrakcja pól tekstowych z HTML (X01): tytuł, korpus (artykuł), CTA, komentarze; bez menu i skryptów."""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional

from methadata.contracts import ids
from methadata.contracts.interfaces import AdapterKind, AdapterSpec
from methadata.contracts.models import (ArtifactRef, ArtifactRole, ExtractionState, ExtractionStatus, Feature,
                                        ValueType)
from methadata.extractors.html import HtmlExtractor
from methadata.extractors.html.extractor import _decode
from methadata.extractors.html.mhtml import looks_like_mhtml, parse_mhtml
from methadata.extractors.html.parse import norm_ws, parse_html

EXTRACTOR_VERSION = "lexical-text-1"
FAMILY = "lexical"
KIND = "text_field"
FIELDS = ("title", "body", "cta", "comments")

_MENU_TAGS = {"nav", "menu", "menuitem"}
_MENU_ROLES = {"navigation", "menu", "menubar", "menuitem"}
_MENU_CLS = re.compile(r"(^|[\s_-])(menu|nav|navbar|navigation|breadcrumbs?|sidebar-menu|mega-?menu)($|[\s_-])", re.I)
_COMMENT_CLS = re.compile(r"(comment|komentarz|komentarze|disqus|reply|odpowiedz)", re.I)
_CTA_CLS = re.compile(r"(^|[\s_-])(btn|button|cta|call-?to-?action|submit)($|[\s_-])", re.I)
_SKIP = {"script", "style", "noscript", "template", "head", "svg"}


def _cls(n) -> str:
    return f"{n.attrs.get('class', '')} {n.attrs.get('id', '')}"


def classify(n) -> Optional[str]:
    """Pole tekstu węzła albo None (menu, skrypt, ukryte). Menu = węzeł lub przodek nav/menu/role/klasa."""
    chain = []
    x = n
    while x is not None and x.tag != "#root":
        chain.append(x)
        x = x.parent
    for x in chain:
        if x.tag in _SKIP and not (n.tag == "title" and x.tag == "head"):
            return None
        if x.hidden and x.tag != "head":
            return None
        if x.tag in _MENU_TAGS or x.attrs.get("role", "").lower() in _MENU_ROLES or _MENU_CLS.search(_cls(x)):
            return None
    if n.tag == "title":
        return "title"
    if any(_COMMENT_CLS.search(_cls(x)) for x in chain):
        return "comments"
    if any(x.tag == "button" or x.attrs.get("role", "").lower() == "button" or
           (x.tag in ("a", "div", "span") and _CTA_CLS.search(_cls(x))) for x in chain):
        return "cta"
    return "body"


class LexicalTextExtractor:
    spec = AdapterSpec(id="lexical-text-extractor", kind=AdapterKind.extractor, version=EXTRACTOR_VERSION,
                       description="Pola tekstowe (tytuł, korpus, CTA, komentarze) z HTML bez menu i skryptów")

    def __init__(self, html_extractor: Optional[HtmlExtractor] = None):
        self.html = html_extractor or HtmlExtractor()

    def extract(self, observation, *, reader, optional: Mapping[str, Any] | None = None):
        oid = observation.observation_id
        hf, hs = self.html.extract(observation, reader=reader, optional=optional)
        sts = [ExtractionStatus(observation_id=oid, artifact_id=s.artifact_id, extractor_version=EXTRACTOR_VERSION,
                                state=s.state, detail=f"X01: {s.detail}" if s.detail else "X01")
               for s in hs if s.state is ExtractionState.failed]
        # X01 -> (artefakt, start, end) -> evidence_refs; węzły z własnego parsowania tego samego tekstu
        refs = {}
        for f in hf:
            if f.kind == "text_section":
                v = f.value
                refs[(f.family, v["start"], v["end"], v["selector"])] = f.evidence_refs
        feats: list[Feature] = []
        seen = set()
        for art in observation.artifacts:
            if art.role not in (ArtifactRole.landing_html, ArtifactRole.landing_dom):
                continue
            fam = "html" if art.role is ArtifactRole.landing_html else "dom"
            try:
                raw = reader.open_bytes(ArtifactRef(artifact_id=art.artifact_id))
                if looks_like_mhtml(raw, art.media_type, art.origin or art.local_ref):
                    main = parse_mhtml(raw).main_part()
                    text = _decode(main.data, main.charset)[0] if main else ""
                else:
                    text = _decode(raw, None)[0]
                doc = parse_html(text)
            except Exception:
                continue
            for n in doc.nodes + [doc.root]:
                field = classify(n)
                if field is None:
                    continue
                for r in n.runs:
                    t = norm_ws("".join(r.parts))
                    if not t:
                        continue
                    ev = refs.get((fam, r.start, r.end, n.selector or "#root")) or \
                        next((v for k, v in refs.items() if k[1:] == (r.start, r.end, n.selector or "#root")), [])
                    val = {"field": field, "text": t, "selector": n.selector or "#root", "view": fam}
                    fid = ids.feature_id(oid, FAMILY, KIND, EXTRACTOR_VERSION, [art.artifact_id, field, r.start, r.end])
                    if fid in seen:
                        continue
                    seen.add(fid)
                    feats.append(Feature(feature_id=fid, observation_id=oid, family=FAMILY, kind=KIND,
                                         value_type=ValueType.json, value=val, extractor_version=EXTRACTOR_VERSION,
                                         evidence_refs=list(ev)))
        if not feats:
            sts.append(ExtractionStatus(observation_id=oid, extractor_version=EXTRACTOR_VERSION,
                                        state=ExtractionState.missing_input, detail="brak tekstu HTML po usunięciu menu i skryptów"))
        else:
            sts.append(ExtractionStatus(observation_id=oid, extractor_version=EXTRACTOR_VERSION, state=ExtractionState.ok,
                                        detail=", ".join(f"{k}={sum(1 for f in feats if f.value['field'] == k)}" for k in FIELDS)))
        return feats, sts
