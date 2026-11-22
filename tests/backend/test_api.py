"""Testy integracyjne backendu W02 (FastAPI, TestClient): demo, CLI, restart, współbieżność, błędy."""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from methadata_api.app import create_app  # noqa: E402
from methadata_api.settings import Settings  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def client(tmp, **kw):
    return TestClient(create_app(Settings(data_dir=tmp), **kw))


def wait(c, run_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        b = c.get(f"/api/v1/runs/{run_id}").json()
        if b["status"] in ("succeeded", "failed"):
            return b
        time.sleep(0.1)
    raise AssertionError("timeout")


def start(c, oid):
    r = c.post("/api/v1/runs", json={"observation_id": oid})
    assert r.status_code == 202
    return r.json()["run_id"]


def groups(body):
    return sorted(sorted(c["material_ids"]) for c in body["result"]["clusters"])


def test_demo_and_cli_parity(tmp_path):
    with client(tmp_path / "d") as c:
        assert c.get("/api/v1/health").json()["status"] == "ok"
        demo = c.get("/api/v1/demo").json()
        assert c.get("/api/v1/demo").json() == demo  # seed idempotentny
        body = wait(c, start(c, demo["observation_id"]))
        assert body["status"] == "succeeded"
        assert len(groups(body)) == 1 and len(groups(body)[0]) == 2
        res = body["result"]
        ids = [e["pair_evidence_id"] for e in res["cluster_run"]["edges"]]
        assert ids
        for i in ids:
            ev = c.get(f"/api/v1/runs/{body['run_id']}/evidence/{i}")
            assert ev.status_code == 200
            artifacts = [item for side in ev.json()["sides"] for item in side["items"]]
            assert artifacts
            for artifact in artifacts:
                digest = artifact["sha256"]
                assert len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)
        # /runs liczy od nowa: drugi przebieg ma inny run_id i te same grupy
        b2 = wait(c, start(c, demo["observation_id"]))
        assert b2["run_id"] != body["run_id"] and groups(b2) == groups(body)
        assert not any(i in str(b2["result"]) for i in [str(tmp_path)])
        # CLI na tym samym magazynie
        out = tmp_path / "cli"
        p = subprocess.run([sys.executable, "-m", "methadata.baseline", "run", "--store", str(tmp_path / "d" / "store"),
                            "--config", str(ROOT / "configs/baseline/baseline.json"), "--out", str(out), "--no-verify"],
                           capture_output=True, text=True, cwd=ROOT)
        assert p.returncode == 0, p.stderr
        rep = json.loads((out / "baseline_report.json").read_text(encoding="utf-8"))
        assert [len(g) for g in rep["groups"]] == [2] == [len(g) for g in groups(body)]
        bundle = json.loads((out / "bundle.json").read_text(encoding="utf-8"))
        # Aplikacja liczy podobieństwo ważoną fuzją (grafiki, tekst, DOM), a CLI baseline tylko po grafikach:
        # wagi krawędzi się różnią, ale obie metody muszą wskazać ten sam skład grupy.
        assert [r["edges"] for r in bundle["runs"]]
        labels = {m["material_id"]: m["label"] for m in res["materials"]}
        web = sorted(sorted(labels[i].split()[1] for i in g) for g in groups(body))   # "Reklama A (…)" -> "A"
        assert web == sorted(sorted(label[-1].upper() for label in g) for g in rep["groups"])   # "dir:ad-a" -> "A"
        assert web == [["A", "C"]]


def test_restart_reads_result_and_marks_interrupted(tmp_path):
    d = tmp_path / "d"
    with client(d) as c:
        demo = c.get("/api/v1/demo").json()
        rid = wait(c, start(c, demo["observation_id"]))["run_id"]
        first = c.get(f"/api/v1/runs/{rid}").json()
    with client(d) as c2:  # restart
        assert c2.get(f"/api/v1/runs/{rid}").json()["result"] == first["result"]
    # przerwany przebieg
    rdir = d / "runs" / "run-0123456789abcdef"
    rdir.mkdir(parents=True)
    (rdir / "state.json").write_text(json.dumps({"run_id": "run-0123456789abcdef", "observation_id": demo["observation_id"],
        "profile": "demo", "status": "running", "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        "instance": "old"}))
    with client(d) as c3:
        b = c3.get("/api/v1/runs/run-0123456789abcdef").json()
        assert b["status"] == "failed" and b["error"]["retryable"] is True


def test_parallel_runs_disjoint(tmp_path):
    with client(tmp_path / "d") as c:
        demo = c.get("/api/v1/demo").json()
        ids = []
        ts = [threading.Thread(target=lambda: ids.append(start(c, demo["observation_id"]))) for _ in range(2)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        a, b = (wait(c, i) for i in ids)
        assert a["run_id"] != b["run_id"] and a["status"] == b["status"] == "succeeded"
        eva = {e["pair_evidence_id"] for e in a["result"]["cluster_run"]["edges"]}
        evb = {e["pair_evidence_id"] for e in b["result"]["cluster_run"]["edges"]}
        assert eva.isdisjoint(evb)
        assert c.get(f"/api/v1/runs/{b['run_id']}/evidence/{next(iter(eva))}").status_code == 404


def test_errors_and_failed_engine(tmp_path):
    def boom(*a, **k):
        raise RuntimeError("/Users/secret/path")
    with client(tmp_path / "d", engine=boom) as c:
        demo = c.get("/api/v1/demo").json()
        assert c.post("/api/v1/runs", json={"observation_id": "obs-nie-ma"}).status_code == 404
        assert c.post("/api/v1/runs", json={}).status_code == 422
        assert c.post("/api/v1/observations", json={"url": "ftp://x"}).status_code == 422
        assert c.get("/api/v1/runs/run-nie-istnieje").status_code == 404
        b = wait(c, start(c, demo["observation_id"]))
        assert b["status"] == "failed" and b["error"]["retryable"] and "secret" not in json.dumps(b)
        assert c.get(f"/api/v1/runs/{b['run_id']}/evidence/x").status_code == 409


def test_url_observation_offline(tmp_path):
    import socket
    real = socket.socket.connect
    socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(OSError("no network"))  # brak sieci
    try:
        with client(tmp_path / "d") as c:
            r = c.post("/api/v1/observations", json={"url": "https://example.invalid/a"})
            assert r.status_code in (200, 201)
            assert r.json()["analysis"]["mode"] == "offline"
    finally:
        socket.socket.connect = real
