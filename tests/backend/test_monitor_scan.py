import json

from fastapi.testclient import TestClient

from methadata_api.app import create_app
from methadata_api.settings import Settings


def ad(i, payer):
    return {"id": f"x{i}", "page_id": f"9{i}", "page_name": f"Strona {i}", "ad_delivery_start_time": "2026-10-01",
            "beneficiary_payers": [{"payer": payer, "beneficiary": payer}]}


def test_scan_reports_new_then_only_new_records_and_groups_by_payer(tmp_path, monkeypatch):
    feed = tmp_path / "feed.jsonl"
    feed.write_text("\n".join(json.dumps(ad(i, "Fundacja Zeta")) for i in (1, 2)), encoding="utf-8")
    monkeypatch.setenv("METHADATA_MONITOR_FEED", str(feed))
    with TestClient(create_app(Settings(tmp_path / "data"))) as api:
        status = api.get("/api/v1/monitor").json()
        assert status["source"] == {"kind": "json-export", "name": "feed.jsonl", "available": True, "synthetic": False}
        assert status["state"] == {}

        first = api.post("/api/v1/monitor/scan").json()
        assert first["ok"] and (first["new"], first["duplicate"]) == (2, 0)
        assert first["payer_groups_touched"] == 1   # obie reklamy ma ten sam płatnik

        again = api.post("/api/v1/monitor/scan").json()
        assert again["ok"] and again["new"] == 0   # nic nowego od ostatniego skanu

        with feed.open("a", encoding="utf-8") as out:
            out.write("\n" + json.dumps(ad(3, "Fundacja Zeta")))
        later = api.post("/api/v1/monitor/scan").json()
        assert later["ok"] and later["new"] == 1 and later["state"]["totals"]["new"] == 3


def test_scan_without_source_is_a_reported_failure_not_a_server_error(tmp_path, monkeypatch):
    monkeypatch.setenv("METHADATA_MONITOR_FEED", str(tmp_path / "brak.jsonl"))
    with TestClient(create_app(Settings(tmp_path / "data"))) as api:
        result = api.post("/api/v1/monitor/scan")
        assert result.status_code == 200
        assert result.json()["ok"] is False and "źródła" in result.json()["error"]


def test_bundled_sample_feed_is_synthetic_and_scans(tmp_path, monkeypatch):
    monkeypatch.delenv("METHADATA_MONITOR_FEED", raising=False)
    with TestClient(create_app(Settings(tmp_path / "data"))) as api:
        assert api.get("/api/v1/monitor").json()["source"]["synthetic"] is True
        scan = api.post("/api/v1/monitor/scan").json()
        assert scan["ok"] and scan["new"] == 4 and scan["payer_groups_touched"] >= 1
