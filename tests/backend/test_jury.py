"""Kontrola izolacji profili i zapisu wejść E18 bez prywatnego korpusu."""
import json
import shutil

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from methadata.experiments.counterfactual.core import FamilySwitch, Inputs
from methadata.runner import load_run
from methadata_api.app import create_app
from methadata_api.prepare_jury import prepare
from methadata_api.settings import DEFAULT_JURY_CONFIG, DEMO_DIR, Settings

from test_api import wait


def test_jury_snapshot_can_be_replayed(tmp_path):
    backup = tmp_path / "backup"
    for label in ("p2", "p7", "p15"):
        capture = backup / label
        shutil.copytree(DEMO_DIR / "ad-a", capture)
        html = capture / "index.html"
        html.write_text(html.read_text(encoding="utf-8") + f"<!-- {label} -->", encoding="utf-8")
    config_path = tmp_path / "jury.json"
    config = json.loads(DEFAULT_JURY_CONFIG.read_text(encoding="utf-8"))
    config.update(index=["p2", "p7"], eval_config=str(DEFAULT_JURY_CONFIG.parent / config["eval_config"]))
    config_path.write_text(json.dumps(config), encoding="utf-8")
    jury_dir = tmp_path / "jury"
    package = prepare(jury_dir, config_path=config_path, backup=backup)
    settings = Settings(data_dir=tmp_path / "service", default_profile="jury", jury_data_dir=jury_dir,
                        jury_config=config_path)
    with TestClient(create_app(settings)) as c:
        demo_id = c.get("/api/v1/demo").json()["observation_id"]
        assert c.post("/api/v1/runs", json={"observation_id": demo_id, "profile": "demo"}).status_code == 202
        assert c.post("/api/v1/runs", json={"observation_id": demo_id}).status_code == 404
        obs = c.post("/api/v1/observations", files={"file": (package.name, package.read_bytes(), "application/zip")})
        assert obs.status_code == 201
        oid = obs.json()["observation_id"]
        assert c.post("/api/v1/runs", json={"observation_id": oid, "profile": "demo"}).status_code == 404
        accepted = c.post("/api/v1/runs", json={"observation_id": oid})
        assert accepted.status_code == 202
        run_id = accepted.json()["run_id"]
        state = wait(c, run_id)
        assert state["status"] == "succeeded"
        result = state["result"]
        assert len(result["clusters"]) == 1
        assert "Hipoteza kampanii" in result["clusters"][0]["summary"]
        assert all(m["available_evidence"] == ["asset", "dom", "text"] for m in result["materials"])

        record, bundle = load_run(settings.runs_dir / run_id)
        assert record["target_observation_id"] == oid
        raw = [p for p in bundle.pairs if p.run_id != run_id]
        public = [p for p in bundle.pairs if p.run_id == run_id]
        assert len(raw) == 9
        assert len(public) == len(result["pair_evidence"]) == 3
        assert c.get(f"/api/v1/runs/{run_id}/evidence/{raw[0].pair_evidence_id}").status_code == 404

        cr = bundle.runs[0]
        labels = {m["material_id"]: m["label"] for m in result["materials"]}
        engine = FamilySwitch(Inputs(bundle.observations, raw, labels, cr.seed),
                              {**cr.config["fusion"], "dataset_version": cr.dataset_version})
        assert engine.baseline.run_id == record["engine_run_id"]
        assert engine.baseline.run.assignments == cr.assignments
