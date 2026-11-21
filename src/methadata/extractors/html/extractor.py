"""Ekstraktor X01: HTML / MHTML -> cechy (tekst, DOM, meta, formularze, skrypty, zdarzenia, zasoby).

Każda cecha niesie `evidence_refs` z `text_span` w dekodowanym tekście artefaktu HTML (lub `byte_range`
w kontenerze MHTML, gdy nie ma magazynu na artefakty pochodne). Widok: `landing_html` -> family `html`
(zapis źródłowy), `landing_dom` -> family `dom` (zapis renderowany).
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping, Optional, Protocol, Sequence
from urllib.parse import unquote, urljoin, urldefrag, urlsplit

from methadata.contracts import ids
from methadata.contracts.interfaces import AdapterSpec, ArtifactReader, AdapterKind
from methadata.contracts.models import (Artifact, ArtifactRef, ArtifactRole, ByteRange, ExtractionState,
                                        ExtractionStatus, Feature, Observation, ParentRelation, TextSpan,
                                        ValueType)

from .mhtml import Mhtml, Part, looks_like_mhtml, parse_mhtml
from .parse import Node, Parsed, norm_ws, parse_html

VERSION = "1.0.0"
EXTRACTOR_VERSION = f"x01-html/{VERSION}"
ROLES = {ArtifactRole.landing_html: "html", ArtifactRole.landing_dom: "dom"}
_CSS_URL = re.compile(r"""url\(\s*(['"]?)(.*?)\1\s*\)|@import\s+(['"])(.*?)\3""", re.I | re.S)
_EVENT = re.compile(r"^on[a-z]+$")
_SKIP_SCHEMES = ("javascript:", "mailto:", "tel:", "about:", "blob:", "#")
_RESOURCE_ATTRS = {  # (tag, attr) -> relacja; link rel rozstrzygane osobno
    ("img", "src"): "img", ("script", "src"): "script", ("iframe", "src"): "frame", ("frame", "src"): "frame",
    ("source", "src"): "media", ("video", "src"): "media", ("audio", "src"): "media", ("track", "src"): "media",
    ("embed", "src"): "embed", ("object", "data"): "embed", ("video", "poster"): "img", ("input", "src"): "img",
}


from methadata.ingest.store import DerivedStore  # noqa: E402,F401  (kanoniczny interfejs)


def _decode(data: bytes, hint: Optional[str]) -> tuple[str, str]:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace"), "utf-8"
    cands = []
    if hint:
        cands.append(hint)
    m = re.search(rb"<meta[^>]+charset\s*=\s*[\"']?\s*([A-Za-z0-9_\-]+)", data[:4096], re.I)
    if m:
        cands.append(m.group(1).decode("ascii", "ignore"))
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    for c in cands:
        try:
            return data.decode(c), c.lower()
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", "replace"), "utf-8-replace"


def _trunc(s: str, n: int = 2000) -> str:
    return s if len(s) <= n else s[:n]


class HtmlExtractor:
    spec = AdapterSpec(id="extract-html", kind=AdapterKind.extractor, version=VERSION,
                       description="HTML/MHTML: tekst, DOM, meta, formularze, skrypty inline, zdarzenia, zasoby osadzone")

    def __init__(self, store: Optional[DerivedStore] = None):
        self.store = store

    # ------------------------------------------------------------------ API
    def extract(self, observation: Observation, *, reader: ArtifactReader,
                optional: Mapping[str, Any] | None = None):
        feats: list[Feature] = []
        sts: list[ExtractionStatus] = []
        targets = [a for a in observation.artifacts if a.role in ROLES]
        if not targets:
            sts.append(ExtractionStatus(observation_id=observation.observation_id, extractor_version=EXTRACTOR_VERSION,
                                        state=ExtractionState.missing_input, detail="brak artefaktu landing_html/landing_dom"))
            return feats, sts
        for art in targets:
            try:
                raw = reader.open_bytes(ArtifactRef(artifact_id=art.artifact_id))
            except Exception as e:  # brak pliku w magazynie
                sts.append(ExtractionStatus(observation_id=observation.observation_id, artifact_id=art.artifact_id,
                                            extractor_version=EXTRACTOR_VERSION, state=ExtractionState.missing_input,
                                            detail=f"nie można odczytać bajtów: {e}"))
                continue
            try:
                f, s = self._one(observation, art, raw)
                feats += f
                sts += s
            except Exception as e:
                sts.append(ExtractionStatus(observation_id=observation.observation_id, artifact_id=art.artifact_id,
                                            extractor_version=EXTRACTOR_VERSION, state=ExtractionState.failed,
                                            detail=f"{type(e).__name__}: {e}"))
        return feats, sts

    # ------------------------------------------------------------------ jeden artefakt
    def _one(self, obs: Observation, art: Artifact, raw: bytes):
        family = ROLES[art.role]
        oid = obs.observation_id
        mh: Optional[Mhtml] = None
        parts_by_loc: dict[str, Part] = {}
        container_ref = ArtifactRef(artifact_id=art.artifact_id)
        host_art_id = art.artifact_id
        base = art.url or ""
        hint = None
        part_artifacts: dict[int, Artifact] = {}
        main: Optional[Part] = None
        capture = "html"
        if looks_like_mhtml(raw, art.media_type, art.origin or art.local_ref):
            capture = "mhtml"
            mh = parse_mhtml(raw)
            main = mh.main_part()
            if main is None:
                return [], [ExtractionStatus(observation_id=oid, artifact_id=art.artifact_id,
                                             extractor_version=EXTRACTOR_VERSION, state=ExtractionState.missing_input,
                                             detail="MHTML bez części text/html")]
            for p in mh.parts:
                if p.location:
                    parts_by_loc.setdefault(urldefrag(p.location)[0], p)
            base = main.location or mh.snapshot_location or base
            text, enc = _decode(main.data, main.charset)
            if self.store is not None:
                for p in mh.parts:
                    part_artifacts[p.index] = self._persist_part(obs, art, p)
                host_art_id = part_artifacts[main.index].artifact_id
        else:
            text, enc = _decode(raw, None)
        doc = parse_html(text)

        def ref(s: int, e: int) -> list[ArtifactRef]:
            """Dowód tekstowy: fragment dekodowanego HTML; bez magazynu pochodnych – zakres bajtów części MHTML."""
            if mh is not None and self.store is None:
                return [ArtifactRef(artifact_id=art.artifact_id,
                                    locator=ByteRange(start=main.byte_start, end=main.byte_end))]
            e = max(e, s + 1)
            return [ArtifactRef(artifact_id=host_art_id, locator=TextSpan(start=s, end=min(e, max(len(text), s + 1))))]

        seen: set[str] = set()
        feats: list[Feature] = []

        def add(kind: str, vt: ValueType, value: Any, refs: Sequence[ArtifactRef] = ()):
            fid = ids.feature_id(oid, family, kind, EXTRACTOR_VERSION, [art.artifact_id, value])
            if fid in seen:
                return
            seen.add(fid)
            feats.append(Feature(feature_id=fid, observation_id=oid, family=family, kind=kind, value_type=vt,
                                 value=value, extractor_version=EXTRACTOR_VERSION, evidence_refs=list(refs)))

        sts: list[ExtractionStatus] = []
        # --- baza URL
        for n in doc.nodes:
            if n.tag == "base" and n.attrs.get("href"):
                base = urljoin(base, n.attrs["href"])
                break
        # --- dokument
        els = doc.nodes
        add("document", ValueType.json, {"capture": capture, "view": "rendered" if family == "dom" else "source",
                                          "encoding": enc, "chars": len(text), "elements": len(els), "base_url": base or None},
            ref(0, len(text)) if text else [])
        # --- tekst
        visible: list[str] = []
        for n in els + [doc.root]:
            if n.tag in ("script", "style"):
                continue
            for r in n.runs:
                t = norm_ws("".join(r.parts))
                if not t:
                    continue
                if n.tag == "title" or n.hidden:
                    pass
                else:
                    visible.append(t)
                add("text_section", ValueType.json,
                    {"selector": n.selector or "#root", "tag": n.tag, "text": t, "hidden": n.hidden,
                     "start": r.start, "end": r.end}, ref(r.start, r.end))
        full = " ".join(visible)
        add("text_sha256", ValueType.string, ids.sha256_bytes(full.encode("utf-8")), [])
        add("visible_text", ValueType.string, full, ref(0, len(text)) if text else [])
        # --- DOM
        counts: dict[str, int] = {}
        for n in els:
            counts[n.tag] = counts.get(n.tag, 0) + 1
        add("tag_counts", ValueType.json, dict(sorted(counts.items())), [])
        skel = ">".join(f"{n.depth}{n.tag}" for n in els)
        add("dom_skeleton_sha256", ValueType.string, ids.sha256_bytes(skel.encode()), [])
        add("max_depth", ValueType.number, max((n.depth for n in els), default=0), [])
        # --- meta
        title = next((n for n in els if n.tag == "title" and n.runs), None)
        if title:
            add("title", ValueType.string, norm_ws("".join("".join(r.parts) for r in title.runs)), ref(title.open_start, title.end))
        for n in els:
            if n.tag == "meta":
                key = n.attrs.get("name") or n.attrs.get("property") or n.attrs.get("http-equiv") or \
                    ("charset" if "charset" in n.attrs else None)
                if key:
                    add("meta", ValueType.json, {"key": key.lower(), "content": n.attrs.get("content", n.attrs.get("charset", "")),
                                                 "selector": n.selector}, ref(n.open_start, n.open_end))
            elif n.tag == "link" and (n.attrs.get("rel") or "").lower() in ("canonical", "alternate", "amphtml"):
                add("meta", ValueType.json, {"key": "link:" + n.attrs["rel"].lower(), "content": n.attrs.get("href", ""),
                                             "selector": n.selector}, ref(n.open_start, n.open_end))
        # --- ramki
        for n in els:
            if n.tag in ("iframe", "frame"):
                src = n.attrs.get("src", "")
                add("frame", ValueType.json, {"selector": n.selector, "src": src,
                                              "resolved": urljoin(base, src) if src else None,
                                              "srcdoc": "srcdoc" in n.attrs}, ref(n.open_start, n.open_end))
        # --- formularze
        forms = [n for n in els if n.tag == "form"]
        add("form_count", ValueType.number, len(forms), [])
        page_host = urlsplit(base).netloc
        for fm in forms:
            fields = []
            for c in _descend(fm):
                if c.tag in ("input", "select", "textarea", "button"):
                    fields.append({"tag": c.tag, "name": c.attrs.get("name"), "type": (c.attrs.get("type") or ("text" if c.tag == "input" else c.tag)).lower(),
                                   "selector": c.selector})
            action = fm.attrs.get("action", "")
            resolved = urljoin(base, action) if (action or base) else None
            target_host = urlsplit(resolved).netloc if resolved else ""
            add("form", ValueType.json, {
                "selector": fm.selector, "action": action, "action_resolved": resolved,
                "method": (fm.attrs.get("method") or "get").lower(), "enctype": fm.attrs.get("enctype"),
                "fields": fields, "has_password": any(f["type"] == "password" for f in fields),
                "cross_origin": bool(target_host and page_host and target_host != page_host)},
                ref(fm.open_start, fm.end))
        # --- skrypty inline, zdarzenia, style inline
        scripts: list[str] = []
        ninline = 0
        for n in els:
            if n.tag == "script" and not n.attrs.get("src"):
                code = n.raw_text
                if not code.strip() or n.raw_start is None:
                    continue
                h = ids.sha256_bytes(code.encode("utf-8"))
                scripts.append(h)
                span = ref(n.raw_start, n.raw_end)
                add("script_inline", ValueType.json, {"selector": n.selector, "sha256": h, "length": len(code),
                                                      "type": n.attrs.get("type", ""), "code": _trunc(code)}, span)
                if self.store is not None:
                    self._persist_inline(obs, art, host_art_id, n, code, ninline, "application/javascript")
                ninline += 1
            for k, v in n.attrs.items():
                if _EVENT.match(k) and v.strip():
                    add("event_handler", ValueType.json, {"selector": n.selector, "tag": n.tag, "attr": k,
                                                          "sha256": ids.sha256_bytes(v.encode()), "code": _trunc(v)},
                        ref(n.open_start, n.open_end))
        add("script_inline_sha256", ValueType.string_list, sorted(set(scripts)), [])
        # --- zasoby / krawędzie dokument-zasób
        missing: list[str] = []
        nav: list[str] = []
        for n, attr, rel, raw_url in _references(doc):
            if not raw_url or raw_url.strip().lower().startswith(_SKIP_SCHEMES):
                continue
            u = raw_url.strip()
            if rel == "link":
                nav.append(urljoin(base, u))
                continue
            state, res_art, part = "missing", None, None
            if u.lower().startswith("data:"):
                state = "inline"
            else:
                resolved = urldefrag(urljoin(base, u))[0]
                part = parts_by_loc.get(resolved) or parts_by_loc.get(urldefrag(u)[0]) if mh else None
                if part is not None:
                    state = "embedded"
                else:
                    res_art = _match_artifact(obs, art, resolved, u)
                    if res_art is not None:
                        state = "attached"
                    elif not urlsplit(resolved).scheme in ("http", "https"):
                        state = "missing"
                    if state == "missing":
                        missing.append(resolved)
            resolved = u if u.lower().startswith("data:") else urldefrag(urljoin(base, u))[0]
            val = {"rel": rel, "tag": n.tag, "attr": attr, "selector": n.selector,
                   "url": _trunc(u, 500), "resolved": _trunc(resolved, 1000), "state": state}
            refs = ref(n.open_start, n.open_end)
            if part is not None:
                val.update(sha256=ids.sha256_bytes(part.data), content_type=part.content_type)
                pa = part_artifacts.get(part.index)
                if pa is not None:
                    val["artifact_id"] = pa.artifact_id
                    refs = refs + [ArtifactRef(artifact_id=pa.artifact_id)]
                    self._link_part(pa, host_art_id, n)
                else:
                    refs = refs + [ArtifactRef(artifact_id=art.artifact_id,
                                               locator=ByteRange(start=part.byte_start, end=part.byte_end))]
            elif res_art is not None:
                val.update(sha256=res_art.sha256, artifact_id=res_art.artifact_id)
                refs = refs + [ArtifactRef(artifact_id=res_art.artifact_id)]
            add("resource_edge", ValueType.json, val, refs)
        if mh is not None:
            for p in mh.parts:
                add("embedded_resource", ValueType.json, {"index": p.index, "location": p.location, "content_type": p.content_type,
                                                          "size": len(p.data), "sha256": ids.sha256_bytes(p.data),
                                                          "is_main": p is main},
                    [ArtifactRef(artifact_id=art.artifact_id, locator=ByteRange(start=p.byte_start, end=p.byte_end))])
        add("outbound_links", ValueType.string_list, sorted(set(nav)), [])
        add("missing_resources", ValueType.string_list, sorted(set(missing)), [])
        sts.append(ExtractionStatus(observation_id=oid, artifact_id=art.artifact_id, extractor_version=EXTRACTOR_VERSION,
                                    state=ExtractionState.ok,
                                    detail=f"{capture}; elementów={len(els)}; formularzy={len(forms)}; skryptów inline={ninline}"))
        if missing:
            ms = sorted(set(missing))
            sts.append(ExtractionStatus(observation_id=oid, artifact_id=art.artifact_id, extractor_version=EXTRACTOR_VERSION,
                                        state=ExtractionState.missing_input,
                                        detail=f"brak {len(ms)} zasobów zewnętrznych w zapisie, np. " + "; ".join(u[:120] for u in ms[:3])))
        return feats, sts

    # ------------------------------------------------------------------ artefakty pochodne
    def _persist_part(self, obs: Observation, container: Artifact, p: Part) -> Artifact:
        origin = f"mhtml-part:{p.index}:{p.location or p.content_id or ''}"[:500]
        return self.store.put_derived(
            observation_id=obs.observation_id, role=ArtifactRole.resource, data=p.data, media_type=p.content_type,
            url=p.location if p.location and re.match(r"^https?://", p.location) else None,
            parent=ArtifactRef(artifact_id=container.artifact_id, locator=ByteRange(start=p.byte_start, end=p.byte_end)),
            parent_relation=ParentRelation.archive_member, origin=origin)

    def _link_part(self, pa: Artifact, host_id: str, node: Node):
        """Zasób osadzony jest 'referenced_by' dokumentu: nie zmieniamy rodzica (archiwum), relację niesie krawędź."""

    def _persist_inline(self, obs, art, host_id, node: Node, code: str, n: int, mt: str):
        self.store.put_derived(
            observation_id=obs.observation_id, role=ArtifactRole.resource, data=code.encode("utf-8"), media_type=mt,
            parent=ArtifactRef(artifact_id=host_id, locator=TextSpan(start=node.raw_start, end=max(node.raw_end, node.raw_start + 1))),
            parent_relation=ParentRelation.inline_in, origin=f"inline-script:{n}")


def _descend(n: Node):
    for c in n.children:
        yield c
        yield from _descend(c)


def _references(doc: Parsed):
    """(węzeł, atrybut, relacja, url). Relacja 'link' = nawigacja (a/area), nie zasób."""
    for n in doc.nodes:
        if n.tag == "link" and n.attrs.get("href"):
            rel = (n.attrs.get("rel") or "").lower()
            if rel in ("canonical", "alternate", "amphtml", "next", "prev", "author"):
                continue
            yield n, "href", "link-" + (rel.split()[0] if rel else "other"), n.attrs["href"]
            continue
        if n.tag in ("a", "area") and n.attrs.get("href"):
            yield n, "href", "link", n.attrs["href"]
        for (t, a), rel in _RESOURCE_ATTRS.items():
            if n.tag == t and n.attrs.get(a):
                yield n, a, rel, n.attrs[a]
        for a in ("srcset",):
            if n.tag in ("img", "source") and n.attrs.get(a):
                for cand in n.attrs[a].split(","):
                    c = cand.strip().split(" ")[0]
                    if c:
                        yield n, a, "img", c
        css = []
        if n.attrs.get("style"):
            css.append((n.attrs["style"], "style"))
        if n.tag == "style" and n.raw_text:
            css.append((n.raw_text, "text"))
        for blob, attr in css:
            for m in _CSS_URL.finditer(blob):
                u = (m.group(2) or m.group(4) or "").strip()
                if u:
                    yield n, attr, "css", u


def _match_artifact(obs: Observation, art: Artifact, resolved: str, raw: str) -> Optional[Artifact]:
    """Zasób dołączony do obserwacji (np. katalog `*_files` zapisanej strony) pasujący po URL lub ścieżce."""
    rel = unquote(urldefrag(raw)[0]).replace("\\", "/")
    rel = re.sub(r"^\./", "", rel)
    for a in obs.artifacts:
        if a.artifact_id == art.artifact_id or a.role in ROLES:
            continue
        if a.url and urldefrag(a.url)[0] == resolved:
            return a
        if not urlsplit(raw).scheme and a.origin:
            o = a.origin.replace("\\", "/")
            if o == rel or o.endswith("/" + rel):
                return a
    return None
