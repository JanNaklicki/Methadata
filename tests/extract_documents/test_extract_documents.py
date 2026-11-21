import json
from pathlib import Path

import pytest

from methadata.contracts import ids
from methadata.contracts.interfaces import AdapterKind
from methadata.contracts.models import (Artifact, ArtifactRole, ExtractionState, InputKind, Observation)
from methadata.contracts.registry import discover
from methadata.extractors.documents import DocumentsExtractor

REAL = Path("/Users/jnaklicki/Documents/Hackathon/Methadata/BACKUP")


class Mem:
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

    def put_derived(self, *, observation_id, role, data, media_type=None, url=None, parent, parent_relation, origin):
        sha = ids.sha256_bytes(data)
        a = Artifact(artifact_id=ids.artifact_id(observation_id, role.value, sha, f"{origin}<{parent.artifact_id}"),
                     observation_id=observation_id, role=role, local_ref=f"derived/{sha}", sha256=sha, size=len(data),
                     media_type=media_type, parent=parent, parent_relation=parent_relation, origin=origin)
        self.extra.append(a)
        self.add(a, data)
        return a


def make(files, declared=None, role=ArtifactRole.document, load=True):
    mem = Mem()
    oid = ids.observation_id("synthetic://doc", "full_capture")
    arts = []
    for name, d in files.items():
        sha = ids.sha256_bytes(d)
        a = Artifact(artifact_id=ids.artifact_id(oid, role.value, sha, name), observation_id=oid, role=role,
                     local_ref="x/" + name, sha256=(declared or {}).get(name, sha), origin=name)
        arts.append(a)
        if load:
            mem.add(a, d)
    obs = Observation(observation_id=oid, canonical_id=ids.canonical_id([], "synthetic://doc"),
                      source_ref="synthetic://doc", input_kind=InputKind.full_capture, artifacts=arts, tags=["synthetic"])
    return obs, mem


def by(feats, kind):
    return [f for f in feats if f.kind == kind]


def run(files, **kw):
    obs, mem = make(files, **kw)
    return DocumentsExtractor(mem).extract(obs, reader=mem) + (mem,)


def test_discover():
    found = discover(AdapterKind.extractor)
    assert isinstance(found["extract-documents"], DocumentsExtractor)


def test_empty_store():
    obs, mem = make({"archive_info.json": b"{}"}, load=False)
    feats, sts = DocumentsExtractor().extract(obs, reader=mem)
    assert not feats and sts[0].state is ExtractionState.missing_input


def test_no_document_artifacts():
    obs, mem = make({"a.png": b"x"}, role=ArtifactRole.creative_image)
    feats, sts = DocumentsExtractor().extract(obs, reader=mem)
    assert not feats and sts[0].state is ExtractionState.missing_input


def test_download_info_masks_url_and_time():
    info = {"url": "https://x.invalid/?gclid=SECRET123&a=1", "timestamp_utc": "2026-10-08T09:09:43.474912",
            "title": "T", "user_agent": "UA"}
    feats, sts, _ = run({"archive_info.json": json.dumps(info).encode()})
    assert by(feats, "download_time")[0].value["time"] == "2026-10-08T09:09:43.474912Z"
    assert by(feats, "download_title")[0].value["title"] == "T"
    assert "SECRET123" not in json.dumps([f.model_dump() for f in feats])
    assert by(feats, "download_url")[0].evidence_refs[0].locator.kind == "byte_range"


def test_cookies_and_storage_have_no_values():
    cookies = [{"name": "sid_x", "value": "TOPSECRETVALUE", "domain": ".a.invalid", "path": "/", "secure": True,
                "httpOnly": True, "sameSite": "Lax", "expires": 1900000000}]
    ls = {"token_key": "LSSECRET", "k2": "v"}
    feats, sts, _ = run({"cookies.json": json.dumps(cookies).encode(), "localStorage.json": json.dumps(ls).encode(),
                         "sessionStorage.json": b"{}"})
    dump = json.dumps([f.model_dump(mode="json") for f in feats])
    assert "TOPSECRETVALUE" not in dump and "LSSECRET" not in dump
    c = by(feats, "cookie")[0].value
    assert c["name"] == "sid_x" and c["secure"] and c["http_only"] and c["same_site"] == "Lax" and not c["session"]
    keys = {f.value["area"]: f.value["keys"] for f in by(feats, "storage_keys")}
    assert keys == {"localStorage": ["k2", "token_key"], "sessionStorage": []}
    assert all(s.state is ExtractionState.ok for s in sts)


def test_empty_storage_ok():
    feats, sts, _ = run({"cookies.json": b"[]"})
    assert by(feats, "cookie_count")[0].value == 0 and sts[0].state is ExtractionState.ok


def test_txt_time_span():
    t = "Bad gateway\n2026-10-08 09:09:40 UTC\nrest"
    feats, sts, _ = run({"page_text.txt": t.encode()})
    f = by(feats, "download_time")[0]
    r = f.evidence_refs[0].locator
    assert t[r.start:r.end] == "2026-10-08 09:09:40 UTC" and f.value["time"] == "2026-10-08T09:09:40Z"


def test_generic_json_title():
    feats, _, _ = run({"metadata.json": b'{"title": "Abc", "links": []}'})
    assert by(feats, "download_title")[0].value["title"] == "Abc"
    assert "/links" in by(feats, "json_shape")[0].value["key_paths"]


def test_manifest_ok_and_mismatch():
    a, b = b"AAA", b"BBB"
    man = {"a.txt": ids.sha256_bytes(a), "b.txt": ids.sha256_bytes(b), "gone.txt": "0" * 64}
    feats, sts, _ = run({"a.txt": a, "b.txt": b, "sha256.json": json.dumps(man).encode()})
    chk = by(feats, "manifest_check")[0].value
    assert chk["verdict"] == "partial" and chk["not_in_observation"] == ["gone.txt"]
    man["b.txt"] = ids.sha256_bytes(b"other")
    feats, sts, _ = run({"a.txt": a, "b.txt": b, "sha256.json": json.dumps(man).encode()})
    chk = by(feats, "manifest_check")[0].value
    assert chk["verdict"] == "mismatch" and chk["mismatched"][0]["name"] == "b.txt"
    assert by(feats, "manifest_consistent")[0].value is False
    assert any(s.state is ExtractionState.failed and "rozbieżność" in s.detail for s in sts)


def test_sidecar_sha256_file():
    z = b"ZIPDATA"
    side = (ids.sha256_bytes(z) + "\n").encode()
    feats, sts, _ = run({"x.zip": z, "x.zip.sha256": side})
    assert by(feats, "manifest_check")[0].value["verdict"] == "ok"


def test_corrupt_pdf():
    pytest.importorskip("pypdf")
    feats, sts, _ = run({"page.pdf": b"%PDF-1.4\n garbage no xref"})
    assert not feats and sts[0].state is ExtractionState.failed
    feats, sts, _ = run({"page.pdf": b"not a pdf"})
    assert sts[0].state is ExtractionState.failed


def test_pdf_synthetic():
    pytest.importorskip("pypdf")
    from pypdf import PdfWriter
    import io
    w = PdfWriter()
    w.add_blank_page(200, 200)
    w.add_metadata({"/Title": "Tytul", "/CreationDate": "D:20261008090940Z"})
    buf = io.BytesIO()
    w.write(buf)
    feats, sts, mem = run({"page.pdf": buf.getvalue()})
    assert by(feats, "pdf_info")[0].value["pages"] == 1
    assert by(feats, "pdf_title")[0].value == "Tytul"
    assert by(feats, "pdf_created")[0].value["time"] == "2026-10-08T09:09:40Z"
    assert sts[0].state is ExtractionState.ok


def _p1():
    d = next(iter((REAL / "p1").glob("archive_*/")), None) if REAL.exists() else None
    return d


@pytest.mark.skipif(_p1() is None, reason="brak BACKUP")
def test_real_p1():
    pytest.importorskip("pypdf")
    d = _p1()
    files = {str(p.relative_to(d)): p.read_bytes() for p in d.rglob("*") if p.is_file()}
    feats, sts, _ = run(files)
    assert not [s for s in sts if s.state is ExtractionState.failed], sts
    assert by(feats, "download_time") and by(feats, "pdf_info") and by(feats, "cookie_count")[0].value == 0
    chk = by(feats, "manifest_check")[0].value
    assert chk["mismatched"] == [] and chk["verdict"] in ("ok", "partial") and chk["matched"]
    assert by(feats, "manifest_consistent")[0].value is True
    assert any("502" in f.value["title"] for f in by(feats, "download_title"))


def _text_pdf(text="Hello Methadata"):
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>", None,
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
    objs[3] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
    return out


def test_pdf_text_persisted_via_real_store(tmp_path):
    pytest.importorskip("pypdf")
    from methadata.contracts.models import ParentRelation
    from methadata.ingest import Store
    st = Store(tmp_path / "s")
    data = _text_pdf()
    sha, size, _ = st.put_bytes(data)
    oid = ids.observation_id("synthetic://pdf", "full_capture")
    a = Artifact(artifact_id=ids.artifact_id(oid, "document", sha, "page.pdf"), observation_id=oid,
                 role=ArtifactRole.document, local_ref=st.blob_ref(sha), sha256=sha, size=size,
                 media_type="application/pdf", origin="page.pdf")
    obs = Observation(observation_id=oid, canonical_id=ids.canonical_id([("document", sha)]),
                      source_ref="synthetic://pdf", input_kind=InputKind.full_capture, artifacts=[a])
    st.save_observation(obs, "imp1")
    feats, sts = DocumentsExtractor().extract(obs, reader=st, optional={"store": st})
    assert sts[0].state is ExtractionState.ok
    assert "Hello Methadata" in by(feats, "pdf_page_text")[0].value["text"]
    kids = st.list_derived(a.artifact_id)
    assert len(kids) == 1 and kids[0].parent_relation is ParentRelation.derived_from
    assert st.open_bytes(kids[0].artifact_id).decode().strip() == "Hello Methadata"
