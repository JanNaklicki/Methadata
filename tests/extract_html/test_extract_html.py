import json
from pathlib import Path

import pytest

from methadata.contracts import ids
from methadata.contracts.interfaces import AdapterKind
from methadata.contracts.models import (Artifact, ArtifactRef, ArtifactRole, Bundle, InputKind, Observation,
                                        TextSpan, ByteRange)
from methadata.contracts.registry import discover
from methadata.extractors.html import HtmlExtractor

ROOT = Path(__file__).resolve().parents[3] if False else None


class Mem:
    """Magazyn testowy zgodny z ArtifactReader + DerivedStore."""
    def __init__(self):
        self.data, self.arts, self.extra = {}, {}, []

    def add(self, a, data):
        self.arts[a.artifact_id] = a
        self.data[a.artifact_id] = data

    def open_bytes(self, ref):
        return self.data[ref.artifact_id]

    def artifact(self, i):
        return self.arts[i]

    def children(self, i):
        return [a for a in self.extra if a.parent and a.parent.artifact_id == i]

    def put_derived(self, a, data):
        self.extra.append(a)
        self.add(a, data)


def make(data: bytes, role=ArtifactRole.landing_html, name="page.html", mt="text/html", base="https://ex.invalid/p", extra=()):
    mem = Mem()
    oid = ids.observation_id("synthetic://" + name, "full_capture")
    sha = ids.sha256_bytes(data)
    a = Artifact(artifact_id=ids.artifact_id(oid, role.value, sha, name), observation_id=oid, role=role,
                 local_ref="x/" + name, sha256=sha, media_type=mt, url=base, origin=name)
    arts = [a]
    mem.add(a, data)
    for nm, d in extra:
        s = ids.sha256_bytes(d)
        r = Artifact(artifact_id=ids.artifact_id(oid, "resource", s, nm), observation_id=oid, role=ArtifactRole.resource,
                     local_ref="r/" + s, sha256=s, origin=nm)
        arts.append(r)
        mem.add(r, d)
    obs = Observation(observation_id=oid, canonical_id=ids.canonical_id([(role.value, sha)]), source_ref="synthetic://" + name,
                      input_kind=InputKind.full_capture, artifacts=arts, tags=["synthetic"])
    return obs, mem, a


def by(feats, kind):
    return [f for f in feats if f.kind == kind]


def span_text(mem, f, art_text):
    r = f.evidence_refs[0]
    assert isinstance(r.locator, TextSpan)
    return art_text[r.locator.start:r.locator.end]


HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Tytuł</title>
<meta name="description" content="opis"><link rel="stylesheet" href="missing.css"></head>
<body><h1>Nagłówek &amp; coś</h1><p>Akapit <b>pierwszy</b></p><p style="display:none">ukryty</p>
<img src="logo_files/a.png" onclick="alert(1)"><script>var x = 1;</script>
<script src="https://cdn.invalid/lib.js"></script><a href="/dalej">dalej</a></body></html>"""


def test_inline_script_event_text_and_missing_resource():
    obs, mem, a = make(HTML.encode(), extra=[("logo_files/a.png", b"PNGDATA")])
    feats, sts = HtmlExtractor().extract(obs, reader=mem)
    for f in feats:
        assert f.evidence_refs is not None
    scr = by(feats, "script_inline")
    assert len(scr) == 1 and scr[0].value["code"] == "var x = 1;"
    assert span_text(mem, scr[0], HTML) == "var x = 1;"
    ev = by(feats, "event_handler")[0]
    assert ev.value["attr"] == "onclick" and "onclick" in span_text(mem, ev, HTML)
    secs = {f.value["text"]: f for f in by(feats, "text_section")}
    assert "Nagłówek & coś" in secs
    assert "Nagłówek &amp; coś" in span_text(mem, secs["Nagłówek & coś"], HTML)
    assert secs["ukryty"].value["hidden"] is True
    assert "ukryty" not in by(feats, "visible_text")[0].value
    edges = {f.value["url"]: f.value for f in by(feats, "resource_edge")}
    assert edges["logo_files/a.png"]["state"] == "attached" and edges["logo_files/a.png"]["sha256"] == ids.sha256_bytes(b"PNGDATA")
    assert edges["missing.css"]["state"] == "missing" and edges["https://cdn.invalid/lib.js"]["state"] == "missing"
    assert by(feats, "missing_resources")[0].value == sorted(["https://ex.invalid/missing.css", "https://cdn.invalid/lib.js"])
    assert {s.state.value for s in sts} == {"ok", "missing_input"}
    assert by(feats, "title")[0].value == "Tytuł"


def test_page_without_form():
    obs, mem, _ = make(b"<html><body><p>hej</p></body></html>")
    feats, sts = HtmlExtractor().extract(obs, reader=mem)
    assert by(feats, "form_count")[0].value == 0 and not by(feats, "form")
    assert [s.state.value for s in sts] == ["ok"]


def test_form():
    h = '<body><form action="https://evil.invalid/x" method="POST"><input name="u"><input type="password" name="p"></form></body>'
    obs, mem, _ = make(h.encode())
    feats, _ = HtmlExtractor().extract(obs, reader=mem)
    f = by(feats, "form")[0].value
    assert f["method"] == "post" and f["has_password"] and f["cross_origin"] and len(f["fields"]) == 2


MH = (b"From: <Saved by Blink>\r\nSnapshot-Content-Location: https://ex.invalid/\r\nMIME-Version: 1.0\r\n"
      b"Content-Type: multipart/related;\r\n\ttype=\"text/html\";\r\n\tboundary=\"BB\"\r\n\r\n--BB\r\n"
      b"Content-Type: text/html\r\nContent-Transfer-Encoding: quoted-printable\r\nContent-Location: https://ex.invalid/\r\n\r\n"
      b"<html><body><p>Za=C5=BC=C3=B3=C5=82=C4=87 =3D x</p><img src=3D\"/i.png\"><img src=3D\"/gone.png\"><script>a=3D1</script></body></html>\r\n"
      b"--BB\r\nContent-Type: image/png\r\nContent-Transfer-Encoding: base64\r\nContent-Location: https://ex.invalid/i.png\r\n\r\n"
      b"iVBORw0KGgo=\r\n--BB--\r\n")


@pytest.mark.parametrize("use_store", [False, True])
def test_mhtml(use_store):
    obs, mem, a = make(MH, name="p.mhtml", mt="multipart/related")
    ex = HtmlExtractor(store=mem if use_store else None)
    feats, sts = ex.extract(obs, reader=mem)
    edges = {f.value["url"]: f.value for f in by(feats, "resource_edge")}
    assert edges["/i.png"]["state"] == "embedded"
    assert edges["/i.png"]["sha256"] == ids.sha256_bytes(bytes.fromhex("89504e470d0a1a0a"))
    assert edges["/gone.png"]["state"] == "missing"
    assert by(feats, "script_inline")[0].value["code"] == "a=1"
    assert len(by(feats, "embedded_resource")) == 2
    assert any(s.state.value == "missing_input" for s in sts)
    if use_store:
        main = [x for x in mem.extra if x.media_type == "text/html"][0]
        sec = by(feats, "text_section")[0]
        loc = sec.evidence_refs[0]
        assert loc.artifact_id == main.artifact_id
        txt = mem.data[main.artifact_id].decode()[loc.locator.start:loc.locator.end]
        assert txt == "Zażółć = x"
        parents = {x.parent_relation.value for x in mem.extra}
        assert parents == {"archive_member", "inline_in"}
        obs2 = obs.model_copy(update={"artifacts": obs.artifacts + mem.extra})
        Bundle(observations=[Observation.model_validate(obs2.model_dump())], features=feats, statuses=sts)
    else:
        assert isinstance(by(feats, "text_section")[0].evidence_refs[0].locator, ByteRange)


def test_deterministic_and_bundle():
    obs, mem, _ = make(HTML.encode())
    f1, s1 = HtmlExtractor().extract(obs, reader=mem)
    f2, _ = HtmlExtractor().extract(obs, reader=mem)
    assert [f.feature_id for f in f1] == [f.feature_id for f in f2]
    Bundle(observations=[obs], features=f1, statuses=s1)


def test_missing_input_and_failure():
    obs, mem, a = make(b"x", role=ArtifactRole.creative_image, name="i.png", mt="image/png")
    _, sts = HtmlExtractor().extract(obs, reader=mem)
    assert sts[0].state.value == "missing_input"


def test_discover():
    found = discover(AdapterKind.extractor)
    assert "extract-html" in found and isinstance(found["extract-html"], HtmlExtractor)


REAL = Path("/Users/jnaklicki/Documents/Hackathon/Methadata/BACKUP")


@pytest.mark.skipif(not REAL.exists(), reason="brak BACKUP")
def test_real_data_spans_valid():
    files = sorted(REAL.rglob("*.html"))[:20] + sorted(REAL.rglob("*.mhtml"))
    assert files
    for p in files:
        data = p.read_bytes()
        obs, mem, a = make(data, name=p.name, mt="multipart/related" if p.suffix == ".mhtml" else "text/html")
        feats, sts = HtmlExtractor(store=mem).extract(obs, reader=mem)
        assert sts[0].state.value == "ok", (p, sts)
        for f in feats:
            for r in f.evidence_refs:
                if isinstance(r.locator, TextSpan):
                    assert r.locator.end <= len(mem.data[r.artifact_id].decode("utf-8", "replace")) + 1
        for f in by(feats, "text_section")[:200]:
            r = f.evidence_refs[0]
            if isinstance(r.locator, TextSpan):
                frag = mem.data[r.artifact_id].decode("utf-8", "replace")[r.locator.start:r.locator.end]
                assert frag.strip()
