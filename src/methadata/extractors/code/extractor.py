"""Ekstraktor X02: statyczne cechy JS, CSS, SVG i fontów (zewnętrznych i inline). Kod nigdy nie jest wykonywany.

Rodziny cech: `js`, `css`, `svg`, `font`. Każda cecha niesie `evidence_refs` (artefakt + TextSpan w dekodowanym tekście).
Błąd parsera: oryginał pozostaje nietknięty, cecha `*_source_sha256` pozostaje, a status ma state=failed.
Artefakty pochodne (znormalizowany JS, inline <style>) trafiają wyłącznie przez `DerivedStore.put_derived`.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import urljoin, urlsplit

from methadata.contracts import ids
from methadata.contracts.interfaces import AdapterKind, AdapterSpec, ArtifactReader
from methadata.contracts.models import (Artifact, ArtifactRef, ArtifactRole, ExtractionState, ExtractionStatus, Feature,
                                        Observation, ParentRelation, TextSpan, ValueType)
from methadata.extractors.html.extractor import _decode
from methadata.extractors.html.mhtml import looks_like_mhtml, parse_mhtml
from methadata.extractors.html.parse import parse_html
from methadata.ingest.store import DerivedStore  # noqa: F401  (kanoniczny interfejs)

from . import cssscan, fontscan, jsscan, svgscan

VERSION = "1.0.0"
EXTRACTOR_VERSION = f"x02-code/{VERSION}"
MAX_BYTES = 8 * 1024 * 1024
HTML_ROLES = (ArtifactRole.landing_html, ArtifactRole.landing_dom)
_JS_TYPES = ("", "text/javascript", "application/javascript", "module", "text/ecmascript", "application/ecmascript",
             "application/x-javascript", "text/jsx", "text/babel")
_EXT = {"js": "js", "mjs": "js", "cjs": "js", "css": "css", "svg": "svg", "woff": "font", "woff2": "font", "ttf": "font",
        "otf": "font", "ttc": "font"}
_MT = (("javascript", "js"), ("ecmascript", "js"), ("text/css", "css"), ("svg", "svg"), ("font", "font"))


def classify(art: Artifact, raw: bytes) -> Optional[str]:
    mt = (art.media_type or "").lower().split(";")[0].strip()
    for k, v in _MT:
        if k in mt:
            return v
    for name in (art.origin, art.url, art.local_ref):
        if name:
            ext = re.split(r"[?#]", name)[0].rsplit(".", 1)[-1].lower()
            if ext in _EXT:
                return _EXT[ext]
    if fontscan.sniff(raw):
        return "font"
    head = raw[:2048].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if head.startswith((b"<svg", b"<?xml")) and b"<svg" in raw[:4096].lower():
        return "svg"
    return None


def host_of(u: str) -> Optional[str]:
    try:
        h = urlsplit(u if "//" in u else "//" + u).hostname
    except ValueError:
        return None
    return h.lower() if h else None


class CodeExtractor:
    spec = AdapterSpec(id="extract-code", kind=AdapterKind.extractor, version=VERSION,
                       description="JS/CSS/SVG/fonty (zewnętrzne i inline): importy, literały, zdarzenia, sygnatury, reguły, fonty, zależności")

    def __init__(self, store: Optional[DerivedStore] = None, persist_normalized: bool = True):
        self.store = store
        self.persist_normalized = persist_normalized

    # ------------------------------------------------------------------ API
    def extract(self, observation: Observation, *, reader: ArtifactReader, optional: Mapping[str, Any] | None = None):
        feats: list[Feature] = []
        sts: list[ExtractionStatus] = []
        handled = 0
        for art in observation.artifacts:
            if art.parent_relation is ParentRelation.inline_in:
                continue  # skrypty/style inline analizujemy z dokumentu nadrzędnego (jeden dowód, bez duplikatów)
            if art.role in (ArtifactRole.landing_screenshot, ArtifactRole.creative_image, ArtifactRole.video,
                            ArtifactRole.audio, ArtifactRole.har, ArtifactRole.input_url, ArtifactRole.archive):
                continue
            try:
                raw = reader.open_bytes(ArtifactRef(artifact_id=art.artifact_id))
            except Exception as e:
                if art.role in HTML_ROLES or art.role is ArtifactRole.resource:
                    sts.append(self._st(observation, art, ExtractionState.missing_input, f"nie można odczytać bajtów: {e}"))
                continue
            if art.role in HTML_ROLES:
                kind = "html"
            else:
                kind = classify(art, raw)
            if kind is None:
                continue
            handled += 1
            ctx = _Ctx(self, observation, art)
            try:
                if len(raw) > MAX_BYTES:
                    ctx.status(ExtractionState.unsupported, f"{kind}: plik {len(raw)} B przekracza limit {MAX_BYTES} B")
                    ctx.add(kind if kind != "html" else "js", "source_sha256", ValueType.string, ids.sha256_bytes(raw), [])
                else:
                    getattr(ctx, "do_" + kind)(raw)
            except Exception as e:  # nieoczekiwany błąd: status, bez przerywania pozostałych artefaktów
                ctx.status(ExtractionState.failed, f"{kind}: {type(e).__name__}: {e}")
            feats += ctx.feats
            sts += ctx.sts
        if handled == 0:
            sts.append(ExtractionStatus(observation_id=observation.observation_id, extractor_version=EXTRACTOR_VERSION,
                                        state=ExtractionState.missing_input, detail="brak artefaktu JS/CSS/SVG/fontu ani HTML"))
        return feats, sts

    def _st(self, obs, art, state, detail):
        return ExtractionStatus(observation_id=obs.observation_id, artifact_id=art.artifact_id,
                                extractor_version=EXTRACTOR_VERSION, state=state, detail=detail)


class _Ctx:
    """Kontekst jednego artefaktu: zbiera cechy i statusy."""

    def __init__(self, ex: CodeExtractor, obs: Observation, art: Artifact):
        self.ex, self.obs, self.art = ex, obs, art
        self.feats: list[Feature] = []
        self.sts: list[ExtractionStatus] = []
        self._seen: set[str] = set()
        self.base = art.url or ""

    # -- narzędzia
    def status(self, state: ExtractionState, detail: str):
        self.sts.append(self.ex._st(self.obs, self.art, state, detail))

    def ref(self, span: Optional[tuple[int, int]], artifact_id: Optional[str] = None) -> list[ArtifactRef]:
        aid = artifact_id or self.art.artifact_id
        if span is None:
            return [ArtifactRef(artifact_id=aid)]
        a, b = span
        return [ArtifactRef(artifact_id=aid, locator=TextSpan(start=a, end=max(b, a + 1)))]

    def add(self, family: str, kind: str, vt: ValueType, value: Any, refs: Sequence[ArtifactRef], scope: Any = None):
        fid = ids.feature_id(self.obs.observation_id, family, kind, EXTRACTOR_VERSION, [self.art.artifact_id, scope, value])
        if fid in self._seen:
            return
        self._seen.add(fid)
        self.feats.append(Feature(feature_id=fid, observation_id=self.obs.observation_id, family=family, kind=kind,
                                  value_type=vt, value=value, extractor_version=EXTRACTOR_VERSION, evidence_refs=list(refs)))

    def resolve(self, u: str) -> str:
        if not u or u.lower().startswith(("data:", "javascript:", "blob:", "#")):
            return u
        try:
            return urljoin(self.base, u) if self.base else u
        except ValueError:
            return u

    # -- JS
    def do_js(self, raw: bytes, *, text: Optional[str] = None, base: int = 0, host_id: Optional[str] = None,
              scope: Any = None, node=None):
        if text is None:
            text, _ = _decode(raw, None)
        sha = ids.sha256_bytes(text.encode("utf-8"))
        hid = host_id or self.art.artifact_id
        whole = self.ref((base, base + len(text)), hid)
        self.add("js", "source_sha256", ValueType.string, sha, whole, scope)
        try:
            A = jsscan.analyze(text, base)
        except jsscan.JsParseError as e:
            self.status(ExtractionState.failed, f"js: błąd parsera ({e}); oryginał zachowany, sha256={sha[:12]}")
            return
        norm_art = self._persist_norm(A, hid, base, len(text), scope)
        self.add("js", "signature", ValueType.json, {"norm_version": jsscan.NORM_VERSION, "normalized_sha256": A.norm_sha256,
                                                      "tokens": len(A.norm), "shingles": A.shingles,
                                                      "normalized_artifact_id": norm_art}, whole, scope)
        self.add("js", "normalized_sha256", ValueType.string, A.norm_sha256, whole, scope)
        self.add("js", "metrics", ValueType.json, A.metrics, whole, scope)
        if A.patterns:
            self.add("js", "api_patterns", ValueType.json, A.patterns, whole, scope)
        specs, doms, evs = set(), set(), set()
        for im in A.imports:
            self.add("js", "import", ValueType.json, {"kind": im["kind"], "spec": im["spec"][:1000],
                                                      "resolved": self.resolve(im["spec"])[:1000]},
                     self.ref(im["span"], hid), scope)
            specs.add(im["spec"][:1000])
            h = host_of(im["spec"]) if re.match(r"^(?:https?:)?//", im["spec"]) else None
            if h:
                doms.add(h)
        for lit in A.literals:
            v = {k: lit[k] for k in lit if k != "span"}
            self.add("js", "literal", ValueType.json, v, self.ref(lit["span"], hid), scope)
            if lit["kind"] in ("url", "domain"):
                h = host_of(lit["value"])
                if h:
                    doms.add(h)
        for ev in A.events:
            self.add("js", "event", ValueType.json, {"name": ev["name"], "via": ev["via"]}, self.ref(ev["span"], hid), scope)
            evs.add(ev["name"])
        self.add("js", "import_specs", ValueType.string_list, sorted(specs), whole, scope)
        self.add("js", "domains", ValueType.string_list, sorted(doms), whole, scope)
        self.add("js", "event_names", ValueType.string_list, sorted(evs), whole, scope)
        self.status(ExtractionState.ok, f"js: tokenów={len(A.norm)}; importów={len(A.imports)}; zdarzeń={len(evs)}; "
                                        f"literałów={len(A.literals)}" + (f"; inline#{scope}" if scope is not None else ""))

    def _persist_norm(self, A, host_id: str, base: int, length: int, scope) -> Optional[str]:
        if self.ex.store is None or not self.ex.persist_normalized:
            return None
        loc = TextSpan(start=base, end=base + max(length, 1))
        a = self.ex.store.put_derived(
            observation_id=self.obs.observation_id, role=ArtifactRole.resource, data=" ".join(A.norm).encode("utf-8"),
            media_type="text/plain", parent=ArtifactRef(artifact_id=host_id, locator=loc),
            parent_relation=ParentRelation.derived_from, origin=f"x02-normalized-js:{jsscan.NORM_VERSION}:{scope if scope is not None else 0}")
        return a.artifact_id

    # -- CSS
    def do_css(self, raw: bytes, *, text: Optional[str] = None, base: int = 0, host_id: Optional[str] = None,
               scope: Any = None, family: str = "css"):
        if text is None:
            text, _ = _decode(raw, None)
        sha = ids.sha256_bytes(text.encode("utf-8"))
        hid = host_id or self.art.artifact_id
        whole = self.ref((base, base + len(text)), hid)
        self.add(family, "source_sha256", ValueType.string, sha, whole, scope)
        try:
            A = cssscan.analyze(text, base)
        except cssscan.CssParseError as e:
            self.status(ExtractionState.failed, f"css: błąd parsera ({e}); oryginał zachowany, sha256={sha[:12]}")
            return
        self.add(family, "structure", ValueType.json, A.structure, whole, scope)
        self.add(family, "layout_sha256", ValueType.string, A.layout_sha256, whole, scope)
        self.add(family, "selectors_sha256", ValueType.string, A.selectors_sha256, whole, scope)
        self.add(family, "classes", ValueType.string_list, A.classes, whole, scope)
        self.add(family, "ids", ValueType.string_list, A.ids, whole, scope)
        self.add(family, "font_families", ValueType.string_list, A.font_families, whole, scope)
        self.add(family, "colors", ValueType.string_list, A.colors, whole, scope)
        self.add(family, "media_queries", ValueType.string_list, A.media, whole, scope)
        for ff in A.font_faces:
            v = {k: ff[k] for k in ff if k != "span"}
            v["src"] = [dict(s, **({"resolved": self.resolve(s["url"])} if "url" in s else {})) for s in ff["src"]]
            self.add(family, "font_face", ValueType.json, v, self.ref(ff["span"], hid), scope)
        for u in A.urls:
            v = {k: u[k] for k in u if k != "span"}
            v["resolved"] = self.resolve(u["url"])[:1000]
            self.add(family, "url", ValueType.json, v, self.ref(u["span"], hid), scope)
        self.status(ExtractionState.ok, f"{family}: reguł={A.structure['rules']}; deklaracji={A.structure['declarations']}; "
                                        f"@font-face={len(A.font_faces)}; url={len(A.urls)}" + (f"; inline#{scope}" if scope is not None else ""))

    # -- SVG
    def do_svg(self, raw: bytes):
        text, _ = _decode(raw, None)
        sha = ids.sha256_bytes(raw)
        whole = self.ref((0, len(text)))
        self.add("svg", "source_sha256", ValueType.string, sha, whole)
        try:
            A = svgscan.analyze(text)
        except svgscan.SvgParseError as e:
            self.status(ExtractionState.failed, f"svg: błąd parsera ({e}); oryginał zachowany, sha256={sha[:12]}")
            return
        self.add("svg", "structure", ValueType.json, A.structure, whole)
        self.add("svg", "geometry_sha256", ValueType.string, A.geometry_sha256, whole)
        self.add("svg", "flags", ValueType.json, A.flags, whole)
        self.add("svg", "ids", ValueType.string_list, A.ids, whole)
        self.add("svg", "font_families", ValueType.string_list, A.fonts, whole)
        for h in A.hrefs:
            v = {k: h[k] for k in h if k != "span"}
            v["resolved"] = self.resolve(h["url"])[:1000] if h["kind"] == "external" else h["url"][:200]
            self.add("svg", "href", ValueType.json, v, self.ref(h["span"]))
        for i, st in enumerate(A.styles):
            self.do_css(b"", text=st["css"], base=st["start"], scope=f"svg-style:{i}", family="css")
        for i, sc in enumerate(A.scripts):
            self.do_js(b"", text=sc["code"], base=sc["start"], scope=f"svg-script:{i}")
        self.status(ExtractionState.ok, f"svg: elementów={A.structure['elements']}; skryptów={len(A.scripts)}; href={len(A.hrefs)}")

    # -- font
    def do_font(self, raw: bytes):
        sha = ids.sha256_bytes(raw)
        self.add("font", "source_sha256", ValueType.string, sha, self.ref(None))
        try:
            info = fontscan.parse(raw)
        except fontscan.FontParseError as e:
            self.status(ExtractionState.failed, f"font: błąd parsera ({e}); oryginał zachowany, sha256={sha[:12]}")
            return
        info["norm_version"] = fontscan.NORM_VERSION
        self.add("font", "info", ValueType.json, info, self.ref(None))
        fam = info.get("names", {}).get("family")
        if fam:
            self.add("font", "family", ValueType.string, fam, self.ref(None))
        self.status(ExtractionState.ok, f"font: {info['container']}; tablic={info.get('num_tables')}; rodzina={fam}")

    # -- HTML (skrypty i style inline)
    def do_html(self, raw: bytes):
        art = self.art
        text, _ = _decode(raw, None)
        host_id, base_off, chunk_ref = art.artifact_id, 0, None
        if looks_like_mhtml(raw, art.media_type, art.origin or art.local_ref):
            mh = parse_mhtml(raw)
            main = mh.main_part()
            if main is None:
                self.status(ExtractionState.missing_input, "MHTML bez części text/html")
                return
            text, _ = _decode(main.data, main.charset)
            self.base = main.location or mh.snapshot_location or self.base
            # offsety dotyczą tekstu części głównej; dowodem jest artefakt (kontener) bez zakresu znaków
            chunk_ref = True
        doc = parse_html(text)
        for n in doc.nodes:
            if n.tag == "base" and n.attrs.get("href"):
                self.base = urljoin(self.base, n.attrs["href"])
                break
        nj = nc = 0
        for n in doc.nodes:
            if n.tag == "script" and not n.attrs.get("src") and n.raw_start is not None and n.raw_text.strip():
                t = (n.attrs.get("type") or "").lower().strip()
                if t not in _JS_TYPES:
                    continue
                self._inline(n, "js", nj, host_id, chunk_ref)
                nj += 1
            elif n.tag == "style" and n.raw_start is not None and n.raw_text.strip():
                self._inline(n, "css", nc, host_id, chunk_ref)
                nc += 1
        if nj + nc == 0:
            self.status(ExtractionState.ok, "html: brak skryptów ani stylów inline")

    def _inline(self, n, kind: str, idx: int, host_id: str, container: Optional[bool]):
        code = n.raw_text
        scope = f"{kind}-inline:{idx}"
        if container:  # offsety względem części MHTML: nie wskazują w bajty kontenera
            base_arg = 0
        else:
            base_arg = n.raw_start
        if kind == "css":
            self._persist_style(n, code, host_id, idx, container)
            self.do_css(b"", text=code, base=base_arg, scope=scope)
        else:
            self.do_js(b"", text=code, base=base_arg, scope=scope)

    def _persist_style(self, n, code: str, host_id: str, idx: int, container: Optional[bool]):
        if self.ex.store is None or container:
            return
        self.ex.store.put_derived(
            observation_id=self.obs.observation_id, role=ArtifactRole.resource, data=code.encode("utf-8"),
            media_type="text/css", parent=ArtifactRef(artifact_id=host_id, locator=TextSpan(start=n.raw_start, end=max(n.raw_end, n.raw_start + 1))),
            parent_relation=ParentRelation.inline_in, origin=f"inline-style:{idx}")
