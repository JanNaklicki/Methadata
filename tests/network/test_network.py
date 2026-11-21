import base64
import json
import os
from pathlib import Path

import pytest

from methadata.contracts import Artifact, Bundle, InputKind, Observation, discover, ids
from methadata.extractors.network import MemoryDerivedStore, NetworkExtractor
from methadata.extractors.network.extractor import parse_redirect_chains


class Reader(MemoryDerivedStore):
    def __init__(self):
        super().__init__()
        self.files = {}

    def open_bytes(self, ref):
        a = self.artifacts.get(ref.artifact_id)
        return self.blobs[a.sha256]


def make(files: dict[str, tuple[str, bytes]]):
    """files: nazwa -> (rola, bajty)."""
    r = Reader()
    oid = ids.observation_id("synthetic://net", "full_capture")
    arts = []
    for name, (role, data) in files.items():
        sha = ids.sha256_bytes(data)
        a = Artifact(artifact_id=ids.artifact_id(oid, role, sha, name), observation_id=oid, role=role,
                     local_ref=f"x/{name}", sha256=sha, origin=name)
        r.artifacts[a.artifact_id] = a
        r.blobs[sha] = data
        arts.append(a)
    arts = arts or [Artifact(artifact_id="art-x", observation_id=oid, role="input_url", url="https://example.invalid/",
                             sha256="0" * 64)]
    o = Observation(observation_id=oid, canonical_id="can-t", source_ref="synthetic://net",
                    input_kind=InputKind.full_capture if files else InputKind.url_only, artifacts=arts,
                    tags=["synthetic"])
    return o, r


def entry(url, status=200, t="2026-01-01T00:00:00.000Z", text=None, enc=None, loc=None, ini=None, ip="192.0.2.1"):
    c = {"size": 0, "mimeType": "text/html"}
    if text is not None:
        c["text"] = text
    if enc:
        c["encoding"] = enc
    return {"startedDateTime": t, "time": 12.5, "serverIPAddress": ip, "_resourceType": "document",
            "_initiator": ini or {"type": "other"},
            "request": {"method": "GET", "url": url, "headers": [{"name": "Cookie", "value": "sid=SECRET"}]},
            "response": {"status": status, "headers": [{"name": "Set-Cookie", "value": "sid=SECRET"},
                                                       {"name": "Server", "value": "nginx"}],
                         "content": c, "redirectURL": loc or ""},
            "timings": {"dns": -1, "wait": 3.0, "receive": 1.0}}


def har(*entries):
    return json.dumps({"log": {"version": "1.2", "entries": list(entries)}}).encode()


def run(files, **kw):
    o, r = make(files)
    return o, r, NetworkExtractor(**kw).extract(o, reader=r)


def by(feats, kind):
    return [f for f in feats if f.kind == kind]


def test_requests_content_and_derived():
    html = "<html>ok</html>"
    b64 = base64.b64encode(b"\x89PNG\x00\x01").decode()
    h = har(entry("https://a.example.invalid/?gclid=SECRET&x=1", text=html),
            entry("https://a.example.invalid/i.png", text=b64, enc="base64"),
            entry("https://a.example.invalid/none"))
    o, r, (feats, st) = run({"network.har": ("har", h)})
    reqs = by(feats, "har_request")
    assert [f.value["body_state"] for f in reqs] == ["text", "base64", "none"]
    assert reqs[1].value["body_sha256"] == ids.sha256_bytes(b"\x89PNG\x00\x01")
    assert reqs[0].value["host"] == "a.example.invalid" and reqs[0].value["query_keys"] == ["gclid", "x"]
    assert "SECRET" not in json.dumps([f.model_dump() for f in feats])
    assert st[0].state.value == "ok" and "1 wpisów bez treści" in st[0].detail
    # pochodne: rodzic = wpis HAR, relacja har_response
    d = [a for a in r.artifacts.values() if a.parent_relation]
    assert len(d) == 2 and all(a.parent.locator.kind == "har_entry" and a.parent_relation.value == "har_response"
                               for a in d)
    assert r.open_bytes(d[0].parent.__class__(artifact_id=d[0].artifact_id)) == html.encode()
    assert reqs[0].value["body_artifact_id"] == d[0].artifact_id
    # cały zestaw przechodzi walidację odwołań kontraktu
    Bundle(observations=[o.model_copy(update={"artifacts": o.artifacts + d})], features=feats, statuses=st)


def test_missing_har_and_corrupt():
    o, r, (feats, st) = run({})
    assert not feats and st[0].state.value == "missing_input"
    o, r, (feats, st) = run({"network.har": ("har", b"{nie json")})
    assert not feats and any(s.state.value == "failed" for s in st)
    o, r, (feats, st) = run({"network.har": ("har", b'{"log": {"entries": 5}}')})
    assert any(s.state.value == "failed" for s in st)


def test_parallel_requests_stay_two_edges():
    ini = {"type": "script", "url": "https://a.example.invalid/main.js?v=1"}
    h = har(entry("https://a.example.invalid/api", ini=ini), entry("https://a.example.invalid/api", ini=ini))
    _, _, (feats, _) = run({"network.har": ("har", h)})
    edges = by(feats, "har_initiator_edge")
    assert len(edges) == 2 and len({f.feature_id for f in edges}) == 2
    assert [f.evidence_refs[0].locator.index for f in edges] == [0, 1]
    assert len(by(feats, "har_request")) == 2


def test_stack_initiator():
    e = entry("https://a.example.invalid/x", ini={"type": "script", "stack": {"callFrames": [
        {"url": ""}, {"url": "https://a.example.invalid/s.js"}]}})
    _, _, (feats, _) = run({"network.har": ("har", har(e))})
    assert by(feats, "har_initiator_edge")[0].value["from"] == "https://a.example.invalid/s.js"


def test_redirect_chain_requires_evidence():
    h = har(entry("https://a.example.invalid/1", 302, "2026-01-01T00:00:01Z", loc="/2"),
            entry("https://a.example.invalid/2", 301, "2026-01-01T00:00:02Z", loc="https://b.example.invalid/3"),
            entry("https://b.example.invalid/3", 200, "2026-01-01T00:00:03Z"))
    _, _, (feats, _) = run({"network.har": ("har", h)})
    ch = by(feats, "redirect_chain")
    assert len(ch) == 1 and ch[0].value["complete"] is True
    assert [x["index"] for x in ch[0].value["hops"]] == [0, 1, 2]
    assert [r.locator.index for r in ch[0].evidence_refs] == [0, 1, 2]


def test_no_chain_without_3xx_even_if_sequential():
    h = har(entry("https://a.example.invalid/1"), entry("https://a.example.invalid/2"))
    _, _, (feats, _) = run({"network.har": ("har", h)})
    assert not by(feats, "redirect_chain")


def test_redirect_target_not_observed():
    h = har(entry("https://a.example.invalid/1", 302, loc="https://z.example.invalid/"))
    ch = parse_redirect_chains(json.loads(har(entry("https://a.example.invalid/1", 302,
                                                    loc="https://z.example.invalid/")))["log"]["entries"])
    assert ch[0][-1]["observed"] is False
    _, _, (feats, _) = run({"network.har": ("har", h)})
    assert by(feats, "redirect_chain")[0].value["complete"] is False


def test_parallel_identical_redirects_two_chains():
    h = har(entry("https://a.example.invalid/r", 302, "2026-01-01T00:00:01Z", loc="/t"),
            entry("https://a.example.invalid/r", 302, "2026-01-01T00:00:01Z", loc="/t"),
            entry("https://a.example.invalid/t", 200, "2026-01-01T00:00:02Z"),
            entry("https://a.example.invalid/t", 200, "2026-01-01T00:00:02Z"))
    _, _, (feats, _) = run({"network.har": ("har", h)})
    ch = by(feats, "redirect_chain")
    assert len(ch) == 2 and all(c.value["complete"] for c in ch)
    assert {tuple(h["index"] for h in c.value["hops"]) for c in ch} == {(0, 2), (1, 3)}


def test_session_values_masked_in_redirects():
    h = har(entry("https://a.example.invalid/?utm_visit=abc-123&k=v", 302, loc="https://b.example.invalid/?gclid=ZZZ"))
    _, _, (feats, _) = run({"network.har": ("har", h)})
    dump = json.dumps([f.model_dump() for f in feats])
    assert "abc-123" not in dump and "ZZZ" not in dump and "k=v" in dump


DNS = {"hostname": "Ex.invalid", "ip": "192.0.2.1", "all_addresses": ["192.0.2.1", "192.0.2.2"],
       "ssl": {"subject": [[["commonName", "ex.invalid"]]],
               "issuer": [[["countryName", "US"]], [["organizationName", "Let's Encrypt"]], [["commonName", "YE1"]]],
               "version": 3, "serialNumber": "AB", "notBefore": "Oct  7 18:53:02 2026 GMT",
               "notAfter": "Jan  5 18:53:01 2027 GMT",
               "subjectAltName": [["DNS", "*.ex.invalid"], ["DNS", "ex.invalid"]]}}


def test_dns_tls():
    _, _, (feats, st) = run({"dns_ssl.json": ("other", json.dumps(DNS).encode())})
    assert by(feats, "dns_host")[0].value == "ex.invalid"
    assert by(feats, "dns_ips")[0].value == ["192.0.2.1", "192.0.2.2"]
    c = by(feats, "tls_cert")[0].value
    assert c["issuer_org"] == "Let's Encrypt" and c["issuer_cn"] == "YE1" and c["san"] == ["*.ex.invalid", "ex.invalid"]
    assert c["not_after"].endswith("2027 GMT") and c["serial"] == "AB"
    assert any(s.state.value == "missing_input" and "HAR" in s.detail for s in st)


def test_dns_without_ssl_and_corrupt():
    d = {k: v for k, v in DNS.items() if k != "ssl"}
    _, _, (feats, st) = run({"dns_ssl.json": ("other", json.dumps(d).encode())})
    assert not by(feats, "tls_cert") and any("brak danych TLS" in (s.detail or "") for s in st)
    _, _, (feats, st) = run({"dns_ssl.json": ("other", b"[1,")})
    assert any(s.state.value == "failed" for s in st)


def test_no_store_notes_unsaved():
    h = har(entry("https://a.example.invalid/", text="x"))
    o, r = make({"network.har": ("har", h)})
    r2 = type("R", (), {"open_bytes": r.open_bytes})()
    feats, st = NetworkExtractor().extract(o, reader=r2)
    assert "nieutrwalonych" in st[0].detail and by(feats, "har_request")[0].value["body_artifact_id"] is None


def test_deterministic_and_discover():
    h = har(entry("https://a.example.invalid/", text="x"))
    _, _, (f1, _) = run({"network.har": ("har", h)})
    _, _, (f2, _) = run({"network.har": ("har", h)})
    assert [f.feature_id for f in f1] == [f.feature_id for f in f2]
    assert "network-extractor" in discover()


BACKUP = Path(os.environ.get("METHADATA_BACKUP", "/Users/jnaklicki/Documents/Hackathon/Methadata/BACKUP"))


@pytest.mark.skipif(not BACKUP.is_dir(), reason="brak katalogu BACKUP")
def test_real_backup():
    hars = sorted(BACKUP.glob("p*/*.har"))
    assert hars
    for p in hars:
        r = Reader()
        files = {"network.har": ("har", p.read_bytes())}
        dns = list(p.parent.glob("archive_*/dns_ssl.json"))
        if dns:
            files["dns_ssl.json"] = ("other", dns[0].read_bytes())
        o, r2 = make(files)
        feats, st = NetworkExtractor().extract(o, reader=r2)
        n = len(json.loads(p.read_bytes())["log"]["entries"])
        assert len(by(feats, "har_request")) == n, p
        assert all(s.state.value != "failed" for s in st), (p, st)
        for f in feats:
            assert "gclid=" not in json.dumps(f.value) or "gclid=***" in json.dumps(f.value)
        if dns:
            assert by(feats, "tls_cert") and by(feats, "dns_host")
