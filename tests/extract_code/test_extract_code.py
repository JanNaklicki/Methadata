import glob
import struct
import tempfile
import zlib
from pathlib import Path

import pytest

from methadata.contracts import ids
from methadata.contracts.models import (Artifact, ArtifactRole, ExtractionState, InputKind, Observation, ParentRelation,
                                        TextSpan)
from methadata.contracts.registry import discover
from methadata.extractors.code import CodeExtractor, js_similarity
from methadata.extractors.code import cssscan, jsscan

BACKUP = Path("/Users/jnaklicki/Documents/Hackathon/Methadata/BACKUP")
JQ = Path.home() / "Library/Application Support/Google/Chrome/Default/Extensions"


class Mem:
    def __init__(self):
        self.data, self.arts = {}, {}
        self._st = None

    def add(self, a, data):
        self.arts[a.artifact_id], self.data[a.artifact_id] = a, data

    def open_bytes(self, ref):
        return self.data[ref.artifact_id]

    def artifact(self, i):
        return self.arts[i]

    def children(self, i):
        return []

    def put_derived(self, **kw):
        from methadata.ingest import Store
        if self._st is None:
            self._st = Store(tempfile.mkdtemp())
        a = self._st.put_derived(**kw)
        self.add(a, (self._st.root / a.local_ref).read_bytes())
        return a


def make(files, role=ArtifactRole.resource):
    """files: [(name, bytes, media_type, role|None)]"""
    mem, arts = Mem(), []
    oid = ids.observation_id("synthetic://x02", "full_capture")
    for name, data, mt, r in files:
        r = r or role
        sha = ids.sha256_bytes(data)
        a = Artifact(artifact_id=ids.artifact_id(oid, r.value, sha, name), observation_id=oid, role=r, local_ref="x/" + name,
                     sha256=sha, media_type=mt, url="https://ex.invalid/a/" + name, origin=name)
        arts.append(a)
        mem.add(a, data)
    obs = Observation(observation_id=oid, canonical_id=ids.canonical_id([(a.role.value, a.sha256) for a in arts], url="synthetic://x02"),
                      source_ref="synthetic://x02", input_kind=InputKind.full_capture, artifacts=arts, tags=["synthetic"])
    return obs, mem, arts


def run(files, store=False):
    obs, mem, arts = make(files)
    ex = CodeExtractor(store=mem if store else None)
    feats, sts = ex.extract(obs, reader=mem)
    return feats, sts, mem, arts


def val(feats, family, kind):
    return [f.value for f in feats if f.family == family and f.kind == kind]


PRETTY = """
// licznik
import { a } from './mod.js';
import './side.js';
const x = require('lib/x');
function track(userId, count) {
  var total = count + 1;
  if (!userId) { return false; }
  document.addEventListener('DOMContentLoaded', function () { total++; });
  window.onresize = function () {};
  $(window).on('scroll touchstart', handler);
  fbq('init', '123456789012345');
  fetch("https://cdn.tracker.example/p.js?id=GTM-ABC123");
  return total;
}
"""
FORMATTED_DIFF = """import {a} from './mod.js'; import './side.js'; const x=require('lib/x');
/* inny komentarz */
function track(uid,n){var t=n+1;if(!uid){return false}document.addEventListener('DOMContentLoaded',function(){t++});window.onresize=function(){};$(window).on('scroll touchstart',handler);fbq('init','123456789012345');fetch("https://cdn.tracker.example/p.js?id=GTM-ABC123");return t}"""
MINIFIED_RENAMED = """import{a}from"./mod.js";import"./side.js";const x=require("lib/x");function track(e,t){var n=t+1;if(!e)return!1;document.addEventListener("DOMContentLoaded",function(){n++}),window.onresize=function(){},$(window).on("scroll touchstart",handler),fbq("init","123456789012345"),fetch("https://cdn.tracker.example/p.js?id=GTM-ABC123");return n}"""


def test_js_features():
    A = jsscan.analyze(PRETTY)
    kinds = {(i["kind"], i["spec"]) for i in A.imports}
    assert {("import", "./mod.js"), ("import", "./side.js"), ("require", "lib/x")} <= kinds
    assert {e["name"] for e in A.events} == {"DOMContentLoaded", "resize", "scroll", "touchstart"}
    lits = {(l["kind"], l["value"]) for l in A.literals}
    assert ("url", "https://cdn.tracker.example/p.js?id=GTM-ABC123") in lits
    assert ("tracking_id", "123456789012345") in lits
    assert A.patterns["fetch"] == 1


def test_js_formatting_and_minification_same_signature():
    a, b, c = (jsscan.analyze(s) for s in (PRETTY, FORMATTED_DIFF, MINIFIED_RENAMED))
    assert a.norm_sha256 == b.norm_sha256 == c.norm_sha256
    assert js_similarity(a.shingles, c.shingles) == 1.0
    other = jsscan.analyze("function z(a){ return a.map(function(q){return q*2}).join('-'); }")
    assert other.norm_sha256 != a.norm_sha256 and js_similarity(a.shingles, other.shingles) < 0.3


def test_extractor_js_formatting_pair_and_evidence():
    feats, sts, mem, arts = run([("a.js", PRETTY.encode(), "application/javascript", None),
                                 ("b.js", MINIFIED_RENAMED.encode(), "text/javascript", None)])
    assert all(s.state is ExtractionState.ok for s in sts)
    sigs = {f.evidence_refs[0].artifact_id: f.value for f in feats if f.kind == "normalized_sha256"}
    assert len(sigs) == 2 and len(set(sigs.values())) == 1
    ev = next(f for f in feats if f.kind == "event" and f.value["name"] == "scroll")
    ref = ev.evidence_refs[0]
    assert ref.artifact_id == arts[0].artifact_id and isinstance(ref.locator, TextSpan)
    assert PRETTY[ref.locator.start:ref.locator.end] == "'scroll touchstart'"
    assert all(f.extractor_version.startswith("x02-code/") for f in feats)


def test_js_parse_error_keeps_original_and_status():
    bad = b"function f( { var s = 'niezamkniety;\n"
    feats, sts, mem, arts = run([("bad.js", bad, "application/javascript", None)])
    assert [s.state for s in sts] == [ExtractionState.failed]
    assert "błąd parsera" in sts[0].detail and sts[0].artifact_id == arts[0].artifact_id
    assert val(feats, "js", "source_sha256") == [ids.sha256_bytes(bad)]
    assert not val(feats, "js", "signature")
    assert mem.open_bytes(type("R", (), {"artifact_id": arts[0].artifact_id})) == bad


def test_js_never_executed():
    # kod zawiera efekty uboczne, które byłyby widoczne przy wykonaniu
    src = "require('fs').writeFileSync('/tmp/METHADATA_X02_PWNED','1'); eval('x'); process.exit(3);"
    feats, sts, *_ = run([("evil.js", src.encode(), "application/javascript", None)])
    assert not Path("/tmp/METHADATA_X02_PWNED").exists()
    assert val(feats, "js", "api_patterns")[0]["eval"] == 1


@pytest.mark.skipif(not (JQ / "gighmmpiobklfepjocnamgkkbiglidom/6.54.0_0/jquery-3.5.1.min.js").exists(), reason="brak jQuery")
def test_popular_library_jquery_versions():
    a = (JQ / "gighmmpiobklfepjocnamgkkbiglidom/6.54.0_0/jquery-3.5.1.min.js").read_bytes()
    b = (JQ / "gplomcegacfeolmacnhpopbindboipfl/6.42.28_0/js/jquery-3.5.1.min.js").read_bytes()
    c = (JQ / "mmjbdbjnoablegbkcklggeknkfcjkjia/1.1.5_0/js/jquery-3.3.1.min.js").read_bytes()
    feats, sts, *_ = run([("a.js", a, "application/javascript", None), ("b.js", b, "application/javascript", None),
                          ("c.js", c, "application/javascript", None)])
    assert all(s.state is ExtractionState.ok for s in sts)
    sig = [f.value for f in feats if f.kind == "signature"]
    assert sig[0]["normalized_sha256"] == sig[1]["normalized_sha256"]
    assert js_similarity(sig[0]["shingles"], sig[2]["shingles"]) > 0.7   # 3.5.1 vs 3.3.1
    assert "DOMContentLoaded" in val(feats, "js", "event_names")[0]


CSS_A = """/* nagłówek */
@font-face { font-family: "Roboto"; src: url(fonts/r.woff2) format("woff2"), url('fonts/r.woff') format('woff');
  font-weight: 400 700; font-display: swap; }
.btn, .btn:hover { color: #FFF; margin: 0px 0.5em; background: url("img/bg.png") }
@media (max-width: 600px) { .btn { padding: 0 } }
body { font-family: "Open Sans", Arial, sans-serif }
@import url("other.css");
"""
CSS_B = ("@import url(other.css);@font-face{font-family:Roboto;src:url(fonts/r.woff2)format(woff2),url(fonts/r.woff)format(woff);"
         "font-weight:400 700;font-display:swap}.btn,.btn:hover{color:#fff;margin:0 .5em;background:url(img/bg.png)}"
         "@media (max-width:600px){.btn{padding:0}}body{font-family:\"Open Sans\",Arial,sans-serif}")


def test_css_features_and_format_invariance():
    A = cssscan.analyze(CSS_A)
    assert A.font_faces[0]["family"] == "Roboto" and [s["url"] for s in A.font_faces[0]["src"]] == ["fonts/r.woff2", "fonts/r.woff"]
    assert {u["url"]: u["kind"] for u in A.urls} == {"fonts/r.woff2": "font", "fonts/r.woff": "font",
                                                      "img/bg.png": "image", "other.css": "import"}
    assert "Open Sans" in A.font_families and "btn" in A.classes
    assert A.media == ["(max-width: 600px)"]
    # @import przeniesiony w minifikacji na początek: porównujemy układ reguł bez niego
    B = cssscan.analyze(CSS_B)
    assert A.selectors_sha256 == B.selectors_sha256
    assert cssscan.analyze(CSS_A.replace("@import url(\"other.css\");\n", "")).layout_sha256 == \
        cssscan.analyze(CSS_B.replace("@import url(other.css);", "")).layout_sha256


def test_css_extractor_resolves_urls_and_errors():
    feats, sts, mem, arts = run([("s.css", CSS_A.encode(), "text/css", None)])
    u = [f.value for f in feats if f.kind == "url" and f.value["kind"] == "font"]
    assert u[0]["resolved"] == "https://ex.invalid/a/fonts/r.woff2"
    ff = next(f for f in feats if f.kind == "font_face")
    assert CSS_A[ff.evidence_refs[0].locator.start:].startswith("@font-face")
    feats, sts, *_ = run([("bad.css", b".a { color: red; ", "text/css", None)])
    assert sts[0].state is ExtractionState.failed and val(feats, "css", "source_sha256")


SVG = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" viewBox="0 0 10 10">
<style>.a{font-family:'Lato',sans-serif}</style><metadata>x</metadata>
<g id="g1" inkscape:label="L"><path d="M0 0L10.0000 10" class="a"/><text font-family="Georgia">T</text>
<image xlink:href="https://cdn.invalid/i.png"/><use href="#g1"/></g>
<script>document.addEventListener('click', function(){});</script></svg>"""
SVG_REFORMAT = SVG.replace("10.0000", "10").replace(' id="g1"', ' id="zmienione"').replace("<metadata>x</metadata>", "")


def test_svg_features():
    feats, sts, mem, arts = run([("i.svg", SVG.encode(), "image/svg+xml", None)])
    assert all(s.state is ExtractionState.ok for s in sts)
    assert set(val(feats, "svg", "font_families")[0]) == {"Lato", "sans-serif", "Georgia"}
    hrefs = {h["url"]: h["kind"] for h in val(feats, "svg", "href")}
    assert hrefs == {"https://cdn.invalid/i.png": "external", "#g1": "internal"}
    assert val(feats, "svg", "flags")[0]["has_script"] and "click" in val(feats, "js", "event_names")[0]
    f2, *_ = run([("j.svg", SVG_REFORMAT.encode(), "image/svg+xml", None)])
    assert val(feats, "svg", "geometry_sha256") == val(f2, "svg", "geometry_sha256")
    span = next(f for f in feats if f.family == "js" and f.kind == "event").evidence_refs[0].locator
    assert SVG[span.start:span.end] == "'click'"


def test_svg_entities_and_garbage_rejected():
    feats, sts, *_ = run([("e.svg", b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaa">]><svg xmlns="http://www.w3.org/2000/svg">&a;</svg>', "image/svg+xml", None)])
    assert sts[0].state is ExtractionState.failed
    feats, sts, *_ = run([("g.svg", b"<svg><g></svg>", "image/svg+xml", None)])
    assert sts[0].state is ExtractionState.failed


def _ttf(family: str) -> bytes:
    name = b"".join(family.encode("utf-16-be") for _ in [0])
    rec = struct.pack(">HHHHHH", 3, 1, 0x409, 1, len(name), 0)
    nt = struct.pack(">HHH", 0, 1, 18) + rec + name
    head = bytearray(54)
    struct.pack_into(">H", head, 18, 1000)
    maxp = struct.pack(">IH", 0x00005000, 42)
    return nt, bytes(head), maxp


def build_sfnt(family: str) -> bytes:
    nt, head, maxp = _ttf(family)
    tabs = [(b"head", head), (b"maxp", maxp), (b"name", nt)]
    off = 12 + 16 * len(tabs)
    out = struct.pack(">IHHHH", 0x00010000, len(tabs), 0, 0, 0)
    body = b""
    for tag, d in tabs:
        out += struct.pack(">4sIII", tag, 0, off + len(body), len(d))
        body += d
    return out + body


def build_woff(family: str) -> bytes:
    nt, head, maxp = _ttf(family)
    tabs = [(b"head", head), (b"maxp", maxp), (b"name", nt)]
    off = 44 + 20 * len(tabs)
    hdr_t, body = b"", b""
    for tag, d in tabs:
        c = zlib.compress(d)
        if len(c) >= len(d):
            c = d
        hdr_t += struct.pack(">4sIIII", tag, off + len(body), len(c), len(d), 0)
        body += c
    total = off + len(body)
    return struct.pack(">4sIIHHIHHIIIII", b"wOFF", 0x00010000, total, len(tabs), 0, 0, 1, 0, 0, 0, 0, 0, 0) + hdr_t + body


def test_fonts():
    for blob, cont in ((build_sfnt("Brand Sans"), "ttf"), (build_woff("Brand Sans"), "woff")):
        feats, sts, *_ = run([("f.bin", blob, "font/x", None)])
        info = val(feats, "font", "info")[0]
        assert info["container"] == cont and info["names"]["family"] == "Brand Sans"
        assert info["units_per_em"] == 1000 and info["num_glyphs"] == 42
    feats, sts, *_ = run([("g.woff2", b"wOF2" + b"\x00" * 10, "font/woff2", None)])
    assert sts[0].state is ExtractionState.failed and val(feats, "font", "source_sha256")


HTML = """<html><head><style>.hero{background:url(/img/h.jpg)}</style>
<script>window.addEventListener('load', function(){ var u='https://t.example/x.js'; });</script>
<script type="application/ld+json">{"a": 1}</script><script src="ext.js"></script></head><body></body></html>"""


def test_html_inline_with_store_and_evidence():
    feats, sts, mem, arts = run([("p.html", HTML.encode(), "text/html", ArtifactRole.landing_html)], store=True)
    assert all(s.state is ExtractionState.ok for s in sts)
    ev = next(f for f in feats if f.kind == "event")
    assert ev.evidence_refs[0].artifact_id == arts[0].artifact_id
    s = ev.evidence_refs[0].locator
    assert HTML[s.start:s.end] == "'load'"
    sig = next(f for f in feats if f.kind == "signature")
    nid = sig.value["normalized_artifact_id"]
    assert nid and mem.arts[nid].parent_relation is ParentRelation.derived_from
    assert any(a.parent_relation is ParentRelation.inline_in and a.media_type == "text/css" for a in mem.arts.values())
    css_url = next(f for f in feats if f.family == "css" and f.kind == "url")
    assert css_url.value["resolved"] == "https://ex.invalid/img/h.jpg"
    assert "t.example" in val(feats, "js", "domains")[0]


def test_html_inline_skips_derived_inline_children():
    # X01 zapisuje inline skrypty jako artefakty pochodne: nie analizujemy ich drugi raz
    obs, mem, arts = make([("p.html", HTML.encode(), "text/html", ArtifactRole.landing_html)])
    ex = CodeExtractor(store=mem)
    f1, _ = ex.extract(obs, reader=mem)
    child = mem.put_derived(observation_id=obs.observation_id, role=ArtifactRole.resource, data=b"var a=1;",
                            media_type="application/javascript", parent=type(obs.artifacts[0]).model_fields and
                            __import__("methadata.contracts.models", fromlist=["ArtifactRef"]).ArtifactRef(artifact_id=arts[0].artifact_id),
                            parent_relation=ParentRelation.inline_in, origin="inline-script:0")
    obs2 = obs.model_copy(update={"artifacts": obs.artifacts + [child]})
    f2, _ = CodeExtractor().extract(obs2, reader=mem)
    assert {f.kind for f in f2 if f.kind == "source_sha256"} and not any(
        f.evidence_refs and f.evidence_refs[0].artifact_id == child.artifact_id for f in f2)


def test_missing_input_status():
    obs, mem, arts = make([("a.png", b"\x89PNG", "image/png", ArtifactRole.creative_image)])
    feats, sts = CodeExtractor().extract(obs, reader=mem)
    assert not feats and sts[0].state is ExtractionState.missing_input


def test_discover_registers_adapter():
    assert "extract-code" in discover()


@pytest.mark.skipif(not BACKUP.exists(), reason="brak danych rzeczywistych")
def test_real_backup_css_svg():
    files = sorted(glob.glob(str(BACKUP / "p3" / "*_files" / "*.css")) + glob.glob(str(BACKUP / "p3" / "*_files" / "*.svg")))[:6]
    if not files:
        pytest.skip("brak plików CSS/SVG")
    ff = [(Path(f).name, Path(f).read_bytes(), None, None) for f in files]
    feats, sts, *_ = run(ff)
    assert sts and all(s.state in (ExtractionState.ok,) for s in sts), [s.detail for s in sts if s.state is not ExtractionState.ok]
    assert {f.family for f in feats} >= {"css", "svg"}
