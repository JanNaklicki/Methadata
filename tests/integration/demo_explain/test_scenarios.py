"""Publiczne scenariusze na zamrożonej, syntetycznej kontroli grafu."""
import json
from itertools import combinations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from methadata.clustering.fusion.core import fuse_and_cluster
from methadata.contracts import Artifact, ArtifactRef, ArtifactRole, Bundle, InputKind, Observation, PairEvidence, ids
from methadata.experiments.counterfactual import core as counterfactual
from methadata.runner import save_run
from methadata_api.app import create_app
from methadata_api.engine import rekey
from methadata_api.settings import Settings
from methadata_api.storage import atomic_write_json


FAMILIES = ("asset", "dom", "text")
RUN_ID = "run-0000000000000001"
CONFIG = {
    "dataset_version": "r03-control-v1",
    "similarity_version": "r03-control-v1",
    "threshold": 0.5,
    "coverage_min": 0.5,
    "min_support": 2,
    "families": {name: {"method_id": f"sample-{name}", "weight": 1.0, "support_min": 0.5} for name in FAMILIES},
}


def observation(name):
    url = f"https://{name.lower()}.example.invalid"
    oid = ids.observation_id(url, "url_only")
    sha = ids.sha256_bytes(url.encode())
    artifact = Artifact(artifact_id=ids.artifact_id(oid, "input_url", sha), observation_id=oid,
                        role=ArtifactRole.input_url, sha256=sha, url=url)
    return Observation(observation_id=oid, canonical_id=ids.canonical_id([], url), source_ref=url,
                       input_kind=InputKind.url_only, artifacts=[artifact])


@pytest.fixture
def frozen_run(tmp_path):
    obs = {name: observation(name) for name in "ABCD"}
    scores = {"AB": (0.8, 0.8, 0.8), "AC": (0.1, 0.1, 0.1), "AD": (None, 0.9, 0.9), "BC": (0.8, 0.8, 0.8)}
    raw = []
    for names, values in scores.items():
        sides = [obs[name] for name in names]
        pair = tuple(sorted(o.canonical_id for o in sides))
        refs = {o.canonical_id: [ArtifactRef(artifact_id=o.artifacts[0].artifact_id)] for o in sides}
        for family, score in zip(FAMILIES, values):
            raw.append(PairEvidence(run_id="run-r03-control-raw", pair=pair, method_id=f"sample-{family}",
                                    relation="shared_template", score=score, evidence_refs=refs if score is not None else {},
                                    explanation="Syntetyczny dowód kontrolny."))
    observations = list(obs.values())
    fused = fuse_and_cluster(observations, raw, CONFIG)
    bundle = rekey(Bundle(observations=observations, pairs=list(fused.pairs), runs=[fused.run]), RUN_ID)
    by_pair = {p.pair: p.pair_evidence_id for p in bundle.pairs}
    bundle.pairs.extend(raw)

    # Ani pliki źródłowe, ani konfiguracja profilu jury nie są potrzebne do odtworzenia.
    settings = Settings(data_dir=tmp_path)
    path = settings.runs_dir / RUN_ID
    save_run({"record": {"engine_run_id": fused.run.run_id}, "bundle": bundle}, path)
    atomic_write_json(path / "state.json", {
        "run_id": RUN_ID, "observation_id": obs["A"].observation_id, "status": "succeeded", "profile": "jury",
    })
    return settings, obs, by_pair, raw, fused


def scenario_url(evidence_id):
    return f"/api/v1/runs/{RUN_ID}/evidence/{evidence_id}/scenarios"


def test_scenarios_follow_real_edges_using_only_the_saved_bundle(frozen_run, monkeypatch):
    settings, obs, by_pair, raw, fused = frozen_run
    a, b, c, d = (o.canonical_id for o in obs.values())
    path = scenario_url(by_pair[tuple(sorted((a, c)))])
    checked = []
    run_dir = settings.runs_dir / RUN_ID
    disk_before = {file.name: file.read_bytes() for file in run_dir.iterdir()}

    with TestClient(create_app(settings)) as client:
        baseline = client.get(path)
        assert baseline.status_code == 200
        base = baseline.json()
        assert base["dataset_version"] == CONFIG["dataset_version"]
        assert base["target_id"] == a
        assert base["families"] == list(FAMILIES)
        assert base["baseline"] == base["scenario"]
        assert base["baseline"]["engine_run_id"] == fused.run.run_id
        assert not base["baseline"]["pair"]["accepted"]
        assert set(base["baseline"]["group"]) == {a, b, c, d}
        assert base["edge_changes"] == []
        assert base["minimal_masks"] == [["dom"], ["text"]]
        hops = [{"from_id": a, "to_id": b, "score": 0.8, "families": list(FAMILIES)},
                {"from_id": b, "to_id": c, "score": 0.8, "families": list(FAMILIES)}]
        assert base["baseline"]["indirect_paths"] == [{"member_id": c, "hops": hops}]

        # Pierwsze żądanie oblicza minimalne maski. Kolejne dla tego celu korzystają z tego samego cache.
        with monkeypatch.context() as patch:
            def unexpected_fusion(*args, **kwargs):
                pytest.fail("Scenariusz został ponownie przeliczony pomimo zamrożonego cache.")

            patch.setattr(counterfactual, "fuse_and_cluster", unexpected_fusion)
            for size in range(4):
                for mask in combinations(FAMILIES, size):
                    query = [("disabled", name) for name in mask]
                    result = client.get(path, params=query)
                    assert result.status_code == 200
                    comparison = result.json()
                    assert comparison["baseline"] == base["baseline"]
                    assert comparison["disabled"] == list(mask)
                    assert all(a in change["pair_ids"] for change in comparison["edge_changes"])
                    assert all(p.pair_evidence_id not in result.text for p in raw)
                    checked.append((query, comparison))
            masked = client.get(path, params={"disabled": "dom"}).json()
            assert set(masked["scenario"]["group"]) == {a, b, c}
            assert masked["scenario"]["indirect_paths"] == [
                {"member_id": c, "hops": [{**hops[0], "families": ["asset", "text"]}, hops[1]]}
            ]
            (loss,) = [edge for edge in masked["edge_changes"] if d in edge["pair_ids"]]
            assert loss["kind"] == "utrata_pokrycia"
            assert loss["scenario"]["status"] == "insufficient_coverage"
            other_pair = client.get(scenario_url(by_pair[tuple(sorted((a, b)))]), params={"disabled": "dom"}).json()
            assert other_pair["scenario"]["engine_run_id"] == masked["scenario"]["engine_run_id"]
            assert other_pair["edge_changes"] == masked["edge_changes"]

        index_pair = tuple(sorted((b, c)))
        index_path = scenario_url(by_pair[index_pair])
        assert client.get(index_path).json()["target_id"] == index_pair[0]
        alternate = client.get(index_path, params={"material_id": index_pair[1], "disabled": "dom"}).json()
        assert alternate["target_id"] == index_pair[1]
        assert alternate["edge_changes"]
        assert all(index_pair[1] in edge["pair_ids"] for edge in alternate["edge_changes"])

        for params, field in [({"disabled": "unknown"}, "disabled"),
                              ({"disabled": ["asset", "asset"]}, "disabled"),
                              ({"material_id": d}, "material_id")]:
            invalid = client.get(path, params=params)
            assert invalid.status_code == 422
            assert invalid.json()["error"]["details"][0]["field"] == field
        assert client.get(scenario_url(raw[0].pair_evidence_id)).json()["error"]["code"] == "scenario_not_available"
        assert client.get(path.replace(RUN_ID, "run-0000000000000002")).json()["error"]["code"] == "run_not_found"

    with TestClient(create_app(settings)) as restarted:
        for query, comparison in checked:
            assert restarted.get(path, params=list(reversed(query))).json() == comparison
    assert {file.name: file.read_bytes() for file in run_dir.iterdir()} == disk_before


def test_scenarios_require_succeeded_run_and_matching_record(frozen_run):
    settings, obs, by_pair, _, _ = frozen_run
    a, c = (obs[name].canonical_id for name in "AC")
    path = scenario_url(by_pair[tuple(sorted((a, c)))])
    run_dir = settings.runs_dir / RUN_ID
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))

    with TestClient(create_app(settings)) as client:
        atomic_write_json(state_path, {**state, "status": "running"})
        pending = client.get(path)
        assert pending.status_code == 409
        assert pending.json()["error"]["retryable"]
        atomic_write_json(state_path, {**state, "profile": "demo"})
        assert client.get(path).status_code == 404
        atomic_write_json(state_path, state)
        atomic_write_json(run_dir / "run.json", {"engine_run_id": "run-foreign-engine"})
        assert client.get(path).status_code == 404
