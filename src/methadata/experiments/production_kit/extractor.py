"""Ekstraktor E12: rodzina `kit`. Z HTML wyprowadza nazwy klas (z pierwszym miejscem użycia) i odnośniki do skryptów
lokalnych/arkuszy. Kod nigdy nie jest wykonywany; HTML jest czytany jako dane (X01 parser)."""
from __future__ import annotations

from typing import Any, Mapping, Optional
from urllib.parse import urljoin, urlsplit

from methadata.contracts import ids
from methadata.contracts.interfaces import AdapterKind, AdapterSpec
from methadata.contracts.models import (ArtifactRef, ArtifactRole, ExtractionState, ExtractionStatus, Feature, TextSpan,
                                        ValueType)
from methadata.extractors.html.extractor import _decode
from methadata.extractors.html.mhtml import looks_like_mhtml, parse_mhtml
from methadata.extractors.html.parse import parse_html

VERSION = "1"
EXTRACTOR_VERSION = f"e12-kit/{VERSION}"
FAMILY = "kit"
KIND_CLASS = "class"
KIND_SCRIPT = "script_ref"
KIND_SHEET = "sheet_ref"
MAX_CLASSES = 3000
_ROLES = (ArtifactRole.landing_html, ArtifactRole.landing_dom)


def _is_local(url: str, base: str) -> bool:
    """Skrypt lokalny: ścieżka względna lub ten sam host co strona."""
    if not url or url.startswith("data:"):
        return False
    sp = urlsplit(url)
    if not sp.netloc and not url.startswith("//"):
        return True
    host = urlsplit(base).netloc.lower() if base else ""
    return bool(host) and sp.netloc.lower() == host


class KitExtractor:
    spec = AdapterSpec(id="kit-extractor", kind=AdapterKind.extractor, version=VERSION,
                       description="E12: klasy CSS użyte w HTML, odnośniki do skryptów lokalnych i arkuszy (z lokalizacją)")

    def extract(self, observation, *, reader, optional: Optional[Mapping[str, Any]] = None):
        oid = observation.observation_id
        arts = [a for r in _ROLES for a in observation.artifacts if a.role is r]

        def st(state, detail, art=None):
            return ExtractionStatus(observation_id=oid, artifact_id=art, extractor_version=EXTRACTOR_VERSION,
                                    state=state, detail=detail)

        if not arts:
            return [], [st(ExtractionState.missing_input, "brak artefaktu landing_html/landing_dom")]
        art = arts[0]
        try:
            raw = reader.open_bytes(ArtifactRef(artifact_id=art.artifact_id))
            text, _ = _decode(raw, None)
            base = art.url or ""
            if looks_like_mhtml(raw, art.media_type, art.origin or art.local_ref):
                mh = parse_mhtml(raw)
                main = mh.main_part()
                if main is None:
                    return [], [st(ExtractionState.missing_input, "MHTML bez części text/html", art.artifact_id)]
                text, _ = _decode(main.data, main.charset)
                base = main.location or mh.snapshot_location or base
            doc = parse_html(text)
        except Exception as e:  # kod i HTML to dane: błąd = status, nie wyjątek
            return [], [st(ExtractionState.failed, f"{type(e).__name__}: {e}", art.artifact_id)]
        feats: list[Feature] = []
        seen: set[str] = set()

        def add(kind, vt, value, span):
            fid = ids.feature_id(oid, FAMILY, kind, EXTRACTOR_VERSION, [art.artifact_id, value])
            if fid in seen:
                return
            seen.add(fid)
            s, e = span
            feats.append(Feature(feature_id=fid, observation_id=oid, family=FAMILY, kind=kind, value_type=vt, value=value,
                                 extractor_version=EXTRACTOR_VERSION,
                                 evidence_refs=[ArtifactRef(artifact_id=art.artifact_id,
                                                            locator=TextSpan(start=s, end=max(e, s + 1)))]))

        ncls = 0
        seen_cls: set[str] = set()
        for n in doc.nodes:
            for c in (n.attrs.get("class") or "").split():
                if ncls < MAX_CLASSES and c not in seen_cls:
                    seen_cls.add(c)
                    add(KIND_CLASS, ValueType.string, c, (n.open_start, n.open_end))
                    ncls += 1
            if n.tag == "script" and n.attrs.get("src"):
                u = urljoin(base, n.attrs["src"]) if base else n.attrs["src"]
                add(KIND_SCRIPT, ValueType.json, {"url": u[:1000], "local": _is_local(n.attrs["src"], base) or _is_local(u, base)},
                    (n.open_start, n.open_end))
            elif n.tag == "link" and "stylesheet" in (n.attrs.get("rel") or "").lower() and n.attrs.get("href"):
                u = urljoin(base, n.attrs["href"]) if base else n.attrs["href"]
                add(KIND_SHEET, ValueType.json, {"url": u[:1000], "local": _is_local(n.attrs["href"], base) or _is_local(u, base)},
                    (n.open_start, n.open_end))
        return feats, [st(ExtractionState.ok, f"klas={ncls}; odnośników={len(feats) - ncls}", art.artifact_id)]
