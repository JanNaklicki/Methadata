from methadata.devtools.realdata import BACKUP as _REAL_BACKUP
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from methadata.contracts import discover
from methadata.ingest import Store
from methadata.intake import (Fetcher, FetchPolicy, analyze_url, classify, fetch_url, replay_check, submit_bytes,
                              submit_files, submit_text, submit_url)
from methadata.intake.urlcheck import default_resolver

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 64
STATE = {"body": b"<html><body>v1</body></html>"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p, port = self.path, self.server.server_address[1]
        if p == "/ok":
            self._send(200, STATE["body"], {"Content-Type": "text/html"})
        elif p == "/redir":
            self._send(302, b"", {"Location": "/ok"})
        elif p == "/loop":
            self._send(302, b"", {"Location": "/loop"})
        elif p == "/to-internal":
            self._send(302, b"", {"Location": f"http://127.0.0.1:{port}/ok"})
        elif p == "/to-file":
            self._send(302, b"", {"Location": "file:///etc/passwd"})
        elif p == "/404":
            self._send(404, b"<html>nie ma</html>", {"Content-Type": "text/html"})
        elif p == "/big":
            self._send(200, b"x" * 100_000)
        elif p == "/slow":
            time.sleep(2)
            self._send(200, b"late")
        elif p == "/drip":
            self.send_response(200)
            self.end_headers()
            for _ in range(20):
                self.wfile.write(b"a")
                self.wfile.flush()
                time.sleep(0.2)
        else:
            self._send(404)


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def local(port, **kw):
    return FetchPolicy(allow_private=True, **kw)


def test_success_redirect_and_idempotent_version(tmp_path, server):
    STATE["body"] = b"<html><body>v1</body></html>"
    st = Store(tmp_path / "s")
    pol = local(server)
    u = f"http://127.0.0.1:{server}/redir"
    o1 = fetch_url(u, st, policy=pol)
    assert o1.fetch.fetch_status == "ok" and o1.fetch.http_status == 200
    assert [h.status for h in o1.fetch.redirects] == [302] and o1.fetch.final_url.endswith("/ok")
    assert o1.store_status == "new"
    assert o1.replay["replay_ok"] is True  # odtwarzanie osobno od statusu pobrania
    o2 = fetch_url(u, st, policy=pol)
    assert o2.store_status == "unchanged"
    assert len(st.versions(o1.observation_id)) == 1  # ponowienie nie dodaje wersji
    STATE["body"] = b"<html><body>v2</body></html>"
    o3 = fetch_url(u, st, policy=pol)
    assert o3.store_status == "new_version" and o3.version == 2
    assert (st.root / "fetches.jsonl").read_text().count("\n") == 3
    STATE["body"] = b"<html><body>v1</body></html>"


def test_http_error_keeps_body_and_excludes(tmp_path, server):
    st = Store(tmp_path / "s")
    o = fetch_url(f"http://127.0.0.1:{server}/404", st, policy=local(server))
    assert o.fetch.fetch_status == "http_error" and o.fetch.http_status == 404
    assert "excluded_from_corpus" in st.observation(o.observation_id).tags


def test_timeout_keeps_url_observation_and_previous_capture(tmp_path, server):
    st = Store(tmp_path / "s")
    o = fetch_url(f"http://127.0.0.1:{server}/slow", st, policy=local(server, timeout_s=0.5))
    assert o.fetch.fetch_status == "timeout" and o.store_status == "url_only"
    obs = st.observation(o.observation_id)
    assert obs.input_kind.value == "url_only"
    # wcześniej zapisana treść przeżywa błąd
    STATE["body"] = b"<html>keep</html>"
    u = f"http://127.0.0.1:{server}/ok"
    assert fetch_url(u, st, policy=local(server)).fetch.fetch_status == "ok"
    fail = fetch_url(u, st, policy=local(server), fetcher=Fetcher(local(server), dial=lambda *a: (_ for _ in ()).throw(ConnectionRefusedError("x"))))
    assert fail.fetch.fetch_status == "network_error" and fail.store_status == "kept_previous"
    assert st.observation(fail.observation_id).input_kind.value == "full_capture"
    assert replay_check(st, fail.observation_id)["replay_ok"]


def test_slow_drip_hits_total_deadline(tmp_path, server):
    r = Fetcher(local(server, timeout_s=1.0)).fetch(f"http://127.0.0.1:{server}/drip")
    assert r.fetch_status == "timeout" and r.duration_s < 2.5


def test_size_limit(server):
    r = Fetcher(local(server, max_bytes=1000)).fetch(f"http://127.0.0.1:{server}/big")
    assert r.fetch_status == "too_large" and r.body is None


def test_redirect_loop(server):
    r = Fetcher(local(server, max_redirects=3)).fetch(f"http://127.0.0.1:{server}/loop")
    assert r.fetch_status == "too_many_redirects" and len(r.redirects) == 4


def test_private_targets_blocked_by_default(server):
    for u in (f"http://127.0.0.1:{server}/ok", "http://localhost/", "http://10.0.0.1/", "http://169.254.169.254/",
              "http://[::1]/", "file:///etc/passwd", "ftp://example.com/", "http://user:pw@example.com/"):
        r = Fetcher().fetch(u)
        assert r.fetch_status == "blocked_target", u


def test_redirect_to_internal_blocked(server):
    def resolver(host, port):
        return ["93.184.216.34"] if host == "pub.test" else default_resolver(host, port)

    def dial(ip, port, timeout):  # "publiczny" host kieruje do lokalnego serwera testowego
        return socket.create_connection(("127.0.0.1", server), timeout=timeout)

    f = Fetcher(FetchPolicy(), resolver=resolver, dial=dial)
    ok = f.fetch("http://pub.test/ok")
    assert ok.fetch_status == "ok"
    r = f.fetch("http://pub.test/to-internal")
    assert r.fetch_status == "blocked_target" and "blocked_address" in r.error and r.body is None
    assert f.fetch("http://pub.test/to-file").fetch_status == "blocked_target"


def test_blocked_fetch_still_leaves_observation(tmp_path):
    st = Store(tmp_path / "s")
    o = fetch_url("http://127.0.0.1:1/x", st)
    assert o.fetch.fetch_status == "blocked_target" and o.store_status == "url_only"
    assert st.observation(o.observation_id).artifacts[0].url == "http://127.0.0.1:1/x"


def test_offline_url_analysis_and_text(tmp_path):
    a = analyze_url("https://xn--caepertss-9ib.example.com/products/1?gclid=abc&utm_source=ad&x=1")
    assert a["valid"] and "punycode" in a["flags"] and a["tracking_keys"] == ["gclid", "utm_source"]
    assert not analyze_url("javascript:alert(1)")["valid"]
    rep, an = submit_text("ZIDENTYFIKOWANY SCAM\n\nhttps://a.example/p?x=1\nhttps://b.example/q\n", tmp_path / "s")
    assert rep.count("new") == 2 and len(an) == 2 and rep.ignored_lines == 1
    assert submit_url("https://a.example/p?x=1", tmp_path / "s").count("unchanged") == 1


def test_file_submissions_image_html_video(tmp_path):
    st = tmp_path / "s"
    assert classify(PNG, "a") == "image" and classify(MP4, "x") == "video" and classify(b"<html></html>") == "html"
    r1 = submit_bytes(PNG + b"1", "foto.png", st)
    r2 = submit_bytes(b"<!doctype html><html><body>x</body></html>", "strona.html", st)
    r3 = submit_bytes(MP4, "nagranie.mp4", st)
    assert all(r.count("new") == 1 and not r.errors for r in (r1, r2, r3))
    kinds = {Store(st).observation(e.observation_id).input_kind.value for r in (r1, r2, r3) for e in r.entries}
    assert kinds == {"creative_only", "full_capture"}
    assert submit_bytes(PNG + b"1", "foto.png", st).count("unchanged") == 1
    f = tmp_path / "x.png"
    f.write_bytes(PNG + b"2")
    assert submit_files([f], st).count("new") == 1


REAL = _REAL_BACKUP.parent / "scam_links.dec"


@pytest.mark.skipif(not REAL.is_file(), reason="brak danych rzeczywistych")
def test_real_url_list_offline(tmp_path):
    rep = submit_files([REAL], tmp_path / "s")
    assert rep.count("new") >= 1 and not rep.errors
    assert all(set(Store(tmp_path / "s").observation(e.observation_id).tags) == {"url_observation"} for e in rep.entries)
