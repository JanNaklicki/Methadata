import copy
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HTTP = ROOT / "contracts" / "http"
spec_ = importlib.util.spec_from_file_location("check_conformance", HTTP / "check_conformance.py")
cc = importlib.util.module_from_spec(spec_)
spec_.loader.exec_module(cc)


def ex(name):
    return json.loads((HTTP / "examples" / name).read_text(encoding="utf-8"))


def test_spec_and_all_examples_conform():
    assert cc.check_static() == []


def test_operations_documented():
    spec = cc.load_spec()
    ops = {(method.upper(), path) for path, item in spec["paths"].items()
           for method in item if method in {"get", "post", "patch"}}
    assert ops == {
        ("GET", "/api/v1/health"),
        ("POST", "/api/v1/observations"),
        ("GET", "/api/v1/demo"),
        ("POST", "/api/v1/runs"),
        ("GET", "/api/v1/runs/{run_id}"),
        ("GET", "/api/v1/runs/{run_id}/evidence/{evidence_id}"),
        ("GET", "/api/v1/runs/{run_id}/evidence/{evidence_id}/scenarios"),
        ("GET", "/api/v1/monitor"),
        ("POST", "/api/v1/monitor/scan"),
        ("GET", "/api/v1/collection"),
        ("GET", "/api/v1/collection/uploads"),
        ("PATCH", "/api/v1/collection/uploads/{upload_id}"),
        ("PATCH", "/api/v1/collection/materials/{material_id}"),
        ("GET", "/api/v1/collection/uploads/{upload_id}/download"),
        ("GET", "/api/v1/collection/uploads/{upload_id}/members/{member_id}/download"),
        ("POST", "/api/v1/imports"),
        ("GET", "/api/v1/imports/{import_id}"),
        ("POST", "/api/v1/imports/{import_id}/retry"),
    }



def test_required_example_scenarios_present():
    names = {f.name for f in (HTTP / "examples").glob("*.json")}
    for n in ("run_succeeded_complete.json", "run_succeeded_no_evidence.json", "run_queued.json", "run_running.json", "run_failed.json",
              "error_validation.json", "error_payload_too_large.json", "error_run_not_found.json", "evidence_full.json"):
        assert n in names
    assert ex("run_succeeded_no_evidence.json")["result"]["summary"]["outcome"] == "insufficient_evidence"
    assert ex("run_succeeded_complete.json")["result"]["summary"]["outcome"] == "grouped"


def _schema(name):
    spec = cc.load_spec()
    return spec, spec["components"]["schemas"][name]


@pytest.mark.parametrize("mutate,schema", [
    (lambda d: d["result"]["materials"][0].update(local_ref="obs-1/page.html"), "Run"),
    (lambda d: d.update(status="done"), "Run"),
    (lambda d: d["result"]["cluster_run"]["assignments"][0].update(status="bogus"), "Run"),
])
def test_invalid_responses_rejected(mutate, schema):
    spec, sch = _schema(schema)
    d = copy.deepcopy(ex("run_succeeded_complete.json"))
    mutate(d)
    assert cc.check_body(spec, sch, d, "x")


@pytest.mark.parametrize("bad", ["/Users/ala/x.png", "C:\\tmp\\x", "<script>alert(1)</script>", "<img src=x onerror=alert(1)>", "Set-Cookie: sid=abc", "JSESSIONID=1"])
def test_leaks_detected(bad):
    assert cc.leaks({"a": [bad]})
    spec, sch = _schema("Evidence")
    d = copy.deepcopy(ex("evidence_full.json"))
    d["sides"][0]["items"][1]["text_excerpts"][0]["text"] = bad
    assert cc.check_body(spec, sch, d, "x")


def test_forbidden_keys_detected():
    assert cc.leaks({"artifact": {"local_ref": "x"}})


def test_cluster_run_view_inconsistency_detected():
    d = copy.deepcopy(ex("run_succeeded_complete.json"))["result"]
    d["pair_evidence"].pop()
    assert cc.check_cluster_run_view(d)


def test_live_conformance_against_mock_backend():
    """Checker działa na żywym serwerze: mock odtwarza przykłady."""
    e = ex
    routes = {
        ("GET", "/api/v1/health"): (200, e("health_ok.json")),
        ("GET", "/api/v1/demo"): (200, e("demo.json")),
        ("POST", "/api/v1/runs"): (202, e("run_accepted.json")),
        ("GET", "/api/v1/runs/run-demo-0001"): (200, e("run_succeeded_complete.json")),
        ("GET", "/api/v1/runs/run-nie-istnieje"): (404, e("error_run_not_found.json")),
    }
    for p in e("run_succeeded_complete.json")["result"]["pair_evidence"]:
        routes[("GET", p["evidence_url"])] = (200, e("evidence_full.json"))

    class H(BaseHTTPRequestHandler):
        def _go(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"null")
            key = (self.command, self.path)
            if key == ("POST", "/api/v1/runs") and body["observation_id"] != e("demo.json")["observation_id"]:
                st, out = 404, e("error_observation_not_found.json")
            elif key == ("POST", "/api/v1/observations"):
                st, out = 422, e("error_validation.json")
            else:
                st, out = routes[key]
            raw = json.dumps(out).encode()
            self.send_response(st); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers()
            self.wfile.write(raw)
        do_GET = do_POST = _go
        def log_message(self, *a): pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert cc.check_live(f"http://127.0.0.1:{srv.server_port}", timeout_s=5) == []
    finally:
        srv.shutdown()
