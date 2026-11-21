import json
from pathlib import Path

import pytest

from methadata.clustering.density import ALGORITHMS, build_result
from methadata.clustering.density.__main__ import _control, _summarize
from methadata.contracts import AdapterKind, Bundle, ClusterRun, discover

ROOT = Path(__file__).resolve().parents[2]
COMPARE = json.loads((ROOT / "configs/density/comparison.json").read_text(encoding="utf-8"))
CFG = {"dataset_version": "density-control-v1", "similarity_version": "synthetic-sim-v1"}


@pytest.fixture
def control():
    pytest.importorskip("sklearn.cluster")
    return _control(COMPARE["control"])


def test_entry_points_discovered_with_optional_backend():
    adapters = discover(AdapterKind.clusterer)
    assert {f"density-{algorithm}-v1" for algorithm in ALGORITHMS} <= adapters.keys()


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_cluster_run_contract_and_pair_references(control, algorithm):
    observations, pairs, labels = control
    config = {**CFG, **COMPARE["profiles"][algorithm]["base"]}
    result = build_result(observations, pairs, config, algorithm=algorithm)
    adapter = discover(AdapterKind.clusterer)[f"density-{algorithm}-v1"]
    assert adapter.cluster(observations, pairs, config) == result.run
    assert ClusterRun.model_validate(result.run.model_dump(mode="json")) == result.run
    Bundle(observations=observations, pairs=pairs, runs=[result.run]).validate_references()
    assert result.run == build_result(observations[::-1], pairs[::-1], config, algorithm=algorithm).run
    assert result.run.dataset_version == CFG["dataset_version"]
    assert result.run.config["similarity_version"] == CFG["similarity_version"]
    assert result.run.config["metric"] == "precomputed"
    assert result.run.config["method_id"] == "synthetic-sim-v1"
    assert result.run.edges
    evidence_by_id = {p.pair_evidence_id: p for p in pairs}
    for row in result.evidence:
        for ref in (row.core_neighbor, row.predecessor):
            if ref is not None:
                assert row.material_id in (ref.source, ref.target)
                assert evidence_by_id[ref.pair_evidence_id].score == ref.weight
    summary = _summarize(result.run, labels)
    assert summary["groups"] == [["A", "B"], ["C", "D", "E"]]
    assert summary["materials_by_status"]["noise"] == ["X"]
    assert summary["materials_by_status"]["insufficient_data"] == ["M"]
    assert summary["noise_share"] == pytest.approx(1 / 6)


def test_dbscan_distinguishes_core_border_and_noise(control):
    observations, pairs, labels = _control({
        **COMPARE["control"], "comparisons": {"AB": 0.98, "CD": 0.8, "DE": 0.8, "CE": 0.4},
    })
    result = build_result(observations, pairs, {**CFG, "eps": 0.25, "min_samples": 3})
    evidence = {labels[row.material_id]: row for row in result.evidence}
    assert evidence["D"].reason == "core"
    assert evidence["D"].neighbors_within_eps == 3
    assert evidence["D"].core_distance == pytest.approx(0.2)
    assert evidence["C"].reason == evidence["E"].reason == "border"
    assert evidence["C"].neighbors_within_eps == 2
    assert evidence["A"].reason == "density_noise"
    assert evidence["M"].reason == "no_comparisons"
    assert len(result.run.edges) == 2
    assert {frozenset((labels[e.source], labels[e.target])) for e in result.run.edges} == {
        frozenset("CD"), frozenset("DE"),
    }


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_missing_comparisons_versus_observed_zero(control, algorithm):
    observations, pairs, labels = control
    missing_pair = frozenset("AX")
    pairs = [p for p in pairs if frozenset(labels[m] for m in p.pair) != missing_pair]
    result = build_result(observations, pairs, CFG, algorithm=algorithm)
    excluded = [row for row in result.evidence if row.reason == "incomplete_matrix"]
    expected_removed, other = sorted(m for m, label in labels.items() if label in missing_pair)
    assert [(row.material_id, row.missing_against) for row in excluded] == [(expected_removed, (other,))]
    assert _summarize(result.run, labels)["status_counts"]["insufficient_data"] == 2


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_all_missing_and_oversized_min_samples(control, algorithm):
    observations, pairs, labels = control
    missing = build_result(observations, [p for p in pairs if p.score is None], CFG, algorithm=algorithm)
    assert _summarize(missing.run, labels)["noise_share"] is None
    assert all(a.status.value == "insufficient_data" for a in missing.run.assignments)
    small = build_result(observations, pairs, {**CFG, "min_samples": 10}, algorithm=algorithm)
    assert _summarize(small.run, labels)["noise_share"] == 1.0
    assert all(row.core_distance is None for row in small.evidence)


def test_dbscan_singletons_supported(control):
    observations, pairs, labels = control
    single = build_result(observations, pairs, {**CFG, "min_samples": 1, "eps": 0.5})
    summary = _summarize(single.run, labels)
    assert summary["groups"] == [["A", "B"], ["C", "D", "E"]]
    assert summary["materials_by_status"]["singleton"] == ["X"]


def test_pair_selection_is_stable_and_uses_one_method(control):
    observations, pairs, labels = control
    original = next(p for p in pairs if {labels[m] for m in p.pair} == {"A", "B"})
    worse = original.model_copy(update={"score": 0.2, "pair_evidence_id": "a-worse"})
    equal = original.model_copy(update={"pair_evidence_id": "a-equal"})
    unknown = original.model_copy(update={"method_id": "other", "pair_evidence_id": "other-pair"})
    with pytest.raises(ValueError, match="kilka metod"):
        build_result(observations, [*pairs, unknown], CFG)
    result = build_result(
        observations, [*pairs, worse, equal, unknown], {**CFG, "method_id": original.method_id},
    )
    assert "a-equal" in {e.pair_evidence_id for e in result.run.edges}
    assert not {"a-worse", "other-pair"} & {e.pair_evidence_id for e in result.run.edges}


@pytest.mark.parametrize(("algorithm", "changes", "message"), [
    ("dbscan", {"dataset_version": ""}, "dataset_version"),
    ("dbscan", {"similarity_version": None}, "similarity_version"),
    ("dbscan", {"eps": 0}, "eps"),
    ("dbscan", {"eps": float("nan")}, "eps"),
    ("hdbscan", {"min_cluster_size": True}, "min_cluster_size"),
    ("optics", {"min_samples": 1}, "min_samples"),
    ("optics", {"xi": 1.0}, "xi"),
])
def test_config_validation_precedes_backend(algorithm, changes, message):
    with pytest.raises(ValueError, match=message):
        build_result([], [], {**CFG, **changes}, algorithm=algorithm)
