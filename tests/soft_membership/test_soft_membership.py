import json
from pathlib import Path

import pytest

pytest.importorskip("sklearn.mixture")
from methadata.clustering.density.__main__ import _control
from methadata.clustering.soft_membership import build_result, pair_agreement, stability
from methadata.contracts import AdapterKind, Bundle, ClusterRun, discover

ROOT = Path(__file__).resolve().parents[2]
CTRL = json.loads((ROOT / "configs/medoids/comparison.json").read_text(encoding="utf-8"))["control"]
CFG = {"dataset_version": "ds-1", "vector_version": "vec-1", "components": 2, "min_samples_per_component": 2}


@pytest.fixture
def data():
    observations, _, labels = _control(CTRL)
    ids = {n: c for c, n in labels.items()}
    names = sorted(ids)
    # dwie rodziny kierunkowe; "X" leży między nimi (materiał graniczny)
    base = {"A": [1, 0.05], "B": [1, -0.05], "C": [0.05, 1], "D": [-0.05, 1], "E": [0.0, 1.0], "X": [1, 1]}
    vec = {ids[n]: base[n] for n in names if n in base}
    return observations, vec, ids


@pytest.mark.parametrize("algorithm", ["fcm", "gmm"])
def test_memberships_sum_to_one_and_border_material(data, algorithm):
    observations, vec, ids = data
    r = build_result(observations, vec, {**CFG, "algorithm": algorithm}, seed=1)
    assert r.status_note == "ok"
    for row in r.memberships.values():
        assert abs(sum(row.values()) - 1.0) < 1e-6
    ev = {e.material_id: e for e in r.evidence}
    if algorithm == "fcm":  # GMM na tak małej próbie jest nadmiernie pewny: margines X = 1 (przypadek zawodności)
        assert ev[ids["X"]].margin < ev[ids["A"]].margin
    assert ev[ids["A"]].top_cluster_id != ev[ids["C"]].top_cluster_id
    assert ev[ids["A"]].top_cluster_id == ev[ids["B"]].top_cluster_id


def test_border_gets_multiple_clusters_and_contract(data):
    observations, vec, ids = data
    r = build_result(observations, vec, {**CFG, "present_threshold": 0.2}, seed=1)
    a = {x.material_id: x for x in r.run.assignments}
    assert len(a[ids["X"]].cluster_ids) == 2 and len(a[ids["A"]].cluster_ids) == 1
    assert ClusterRun.model_validate(r.run.model_dump(mode="json")) == r.run
    assert r.run.config["measure"] == "degree_of_membership_model_output" and r.run.config["presentation_rule"]
    assert r.run == build_result(observations[::-1], dict(reversed(list(vec.items()))), {**CFG, "present_threshold": 0.2}, seed=1).run


def test_too_few_samples_explicit_status(data):
    observations, vec, ids = data
    small = {k: vec[k] for k in list(vec)[:3]}
    r = build_result(observations, small, {**CFG, "components": 3})
    assert r.status_note == "too_few_samples"
    assert {a.status.value for a in r.run.assignments} == {"insufficient_data"}
    assert {e.reason for e in r.evidence if e.material_id in small} == {"too_few_samples"}
    assert build_result(observations, {}, CFG).status_note == "no_vectors"


def test_entry_point_and_validation(data):
    observations, vec, _ = data
    adapter = discover(AdapterKind.clusterer)["soft-membership-v1"]
    run = adapter.cluster(observations, [], {**CFG, "vectors": vec}, seed=2)
    assert run == build_result(observations, vec, CFG, seed=2).run
    Bundle(observations=observations, pairs=[], runs=[run]).validate_references()
    with pytest.raises(ValueError):
        adapter.cluster(observations, [], CFG)
    for bad in ({"vector_version": None}, {"components": 0}, {"fuzzifier": 1.0}, {"algorithm": "x"}):
        with pytest.raises(ValueError):
            build_result(observations, vec, {**CFG, **bad})


def test_stability_vs_components_and_size():
    import numpy as np
    names = [f"M{i:02d}" for i in range(30)]
    observations, _, _ = _control({"dataset_version": "ds-big", "materials": names, "unobserved": [], "comparisons": {}})
    rng = np.random.RandomState(0)
    ids = sorted(m.canonical_id for m in observations)
    # zbiór większy niż liczba składników: 3 skupiska po tylu materiałów, ile jest obserwacji
    centers = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    vec = {m: list(np.array(centers[i % 3]) + 0.05 * rng.randn(3)) for i, m in enumerate(ids)}
    rows = stability(observations, vec, {**CFG, "components": 3, "min_samples_per_component": 1},
                     components=[2, 3], fractions=[1.0, 0.7], repeats=2, seed=3)
    by = {(r["components"], r["fraction"]): r for r in rows}
    assert by[(3, 1.0)]["pair_agreement_mean"] == 1.0
    assert by[(3, 0.7)]["pair_agreement_mean"] > 0.9
    assert by[(2, 1.0)]["pair_agreement_mean"] < 1.0  # zmiana liczby składników zmienia podział
    tiny = stability(observations, vec, {**CFG, "components": 3}, fractions=[0.1], repeats=1)
    assert tiny[0]["status"] == ["too_few_samples"] and tiny[0]["pair_agreement_mean"] is None


def test_pair_agreement():
    assert pair_agreement({"a": "x", "b": "x", "c": "y"}, {"a": "p", "b": "p", "c": "q"}) == 1.0
    assert pair_agreement({"a": "x"}, {"a": "x"}) is None
