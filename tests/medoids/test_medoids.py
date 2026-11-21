import json
from pathlib import Path

import pytest

pytest.importorskip("sklearn.cluster")
from methadata.clustering.density.__main__ import _control
from methadata.clustering.medoids import build_centroid_result, build_result, sweep_k
from methadata.clustering.medoids.__main__ import compare
from methadata.contracts import AdapterKind, Bundle, ClusterRun, discover

ROOT = Path(__file__).resolve().parents[2]
CTRL = json.loads((ROOT / "configs/medoids/comparison.json").read_text(encoding="utf-8"))["control"]
CFG = {"dataset_version": CTRL["dataset_version"], "similarity_version": "synthetic-sim-v1", "max_distance": 0.5}


@pytest.fixture
def control():
    return _control(CTRL)


def test_entry_point_and_contract(control):
    observations, pairs, labels = control
    adapter = discover(AdapterKind.clusterer)["medoids-kmedoids-v1"]
    result = build_result(observations, pairs, {**CFG, "k": 2})
    assert adapter.cluster(observations, pairs, {**CFG, "k": 2}) == result.run
    assert ClusterRun.model_validate(result.run.model_dump(mode="json")) == result.run
    Bundle(observations=observations, pairs=pairs, runs=[result.run]).validate_references()
    assert result.run == build_result(observations[::-1], pairs[::-1], {**CFG, "k": 2}).run


def test_medoids_are_real_materials_with_evidence(control):
    observations, pairs, labels = control
    r = build_result(observations, pairs, {**CFG, "k": 2})
    assert {labels[m] for m in r.medoids} <= {"A", "B", "C", "D", "E", "X"}
    assert labels[r.medoids[0]] in {"A", "B", "D", "C", "E"}
    by_id = {p.pair_evidence_id: p for p in pairs}
    for e in r.evidence:
        if e.link:
            assert by_id[e.link.pair_evidence_id].score == e.link.weight
            assert e.material_id in (e.link.source, e.link.target)
    groups = {labels[e.material_id]: labels.get(e.representative_id) for e in r.evidence}
    assert groups["M"] is None  # niezaobserwowany: brak danych
    assert {a.status.value for a in r.run.assignments if labels[a.material_id] == "M"} == {"insufficient_data"}


def test_forced_assignment_rejected_at_k2_and_singleton_at_k3(control):
    observations, pairs, labels = control
    r2 = build_result(observations, pairs, {**CFG, "k": 2})
    st = {labels[a.material_id]: a.status.value for a in r2.run.assignments}
    assert st["X"] == "noise" and st["A"] == st["B"] == "grouped"
    assert [labels[e.material_id] for e in r2.evidence if e.forced] == ["X"]
    r3 = build_result(observations, pairs, {**CFG, "k": 3})
    assert {labels[a.material_id]: a.status.value for a in r3.run.assignments}["X"] == "singleton"


def test_sweep_k_cost_monotonic_and_forced_counts(control):
    observations, pairs, _ = control
    rows = sweep_k(observations, pairs, CFG, [1, 2, 3, 4])
    costs = [r["cost"] for r in rows]
    assert costs == sorted(costs, reverse=True)
    assert rows[0]["forced_assignments"] > rows[2]["forced_assignments"] == 0


def test_centroids_match_medoid_families_and_report(control):
    report = compare(CTRL)
    k2 = report["ks"]["2"]
    assert k2["medoids"]["groups"] == k2["centroids"]["groups"] == [["A", "B"], ["C", "D", "E"]]
    assert k2["pair_agreement_medoids_vs_centroids"] == 1.0
    assert k2["medoids"]["wrong_forced_assignments"] == []
    k1 = report["ks"]["1"]["medoids"]
    assert k1["wrong_forced_without_threshold"] and not k1["wrong_forced_assignments"]
    assert report["ks"]["2"]["medoids"]["wrong_forced_without_threshold"] == ["X"]
    assert report["ks"]["3"]["medoids"]["wrong_forced_without_threshold"] == []
    assert sorted(report["ks"]["2"]["medoids"]["representatives"]) in (["A", "D"], ["B", "D"], ["A", "C"],
        ["A", "E"], ["B", "C"], ["B", "E"])


def test_vectors_must_be_normalised_and_versioned(control):
    observations, _, labels = control
    ids = {n: c for c, n in labels.items()}
    vec = {ids["A"]: [3, 0], ids["B"]: [0.1, 0]}
    r = build_centroid_result(observations, vec, {"dataset_version": "d", "vector_version": "v", "k": 1})
    assert [a.status.value for a in r.run.assignments].count("grouped") == 2  # skala nie wpływa po normalizacji
    with pytest.raises(ValueError):
        build_centroid_result(observations, vec, {"dataset_version": "d", "k": 1})
    with pytest.raises(ValueError):
        build_centroid_result(observations, {ids["A"]: [0, 0]}, {"dataset_version": "d", "vector_version": "v"})


def test_invalid_k(control):
    observations, pairs, _ = control
    with pytest.raises(ValueError):
        build_result(observations, pairs, {**CFG, "k": 0})
