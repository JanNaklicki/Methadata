import json
from itertools import combinations

import pytest

from methadata.contracts import Artifact, ArtifactRef, ArtifactRole, Bundle, InputKind, Observation, PairEvidence, Relation, ids
from methadata.experiments.counterfactual import CounterfactualError, FamilySwitch, Inputs, run_counterfactual
from methadata.experiments.counterfactual.core import write_outputs

FAMILIES = ("asset", "dom", "text")
CONFIG = {
    "threshold": 0.6, "coverage_min": 0.5, "min_support": 2,
    "families": {f: {"method_id": f"sample-{f}", "weight": 1.0, "support_min": 0.3} for f in FAMILIES},
}


def observation(name):
    url = f"https://{name.lower()}.example.invalid"
    source = f"synthetic://{name}"
    oid = ids.observation_id(source, "url_only")
    sha = ids.sha256_bytes(name.encode())
    artifact = Artifact(artifact_id=ids.artifact_id(oid, "input_url", sha), observation_id=oid,
                        role=ArtifactRole.input_url, sha256=sha, url=url)
    return Observation(observation_id=oid, canonical_id=ids.canonical_id([], url), source_ref=source,
                       input_kind=InputKind.url_only, artifacts=[artifact], tags=["synthetic"])


def compare(a, b, family, score):
    pair = tuple(sorted((a.canonical_id, b.canonical_id)))
    refs = {o.canonical_id: [ArtifactRef(artifact_id=o.artifacts[0].artifact_id)] for o in (a, b)}
    return PairEvidence(run_id="e18-control-input", pair=pair, method_id=f"sample-{family}",
                        relation=Relation.shared_template, score=score, evidence_refs=refs if score is not None else {},
                        explanation=f"Kontrolny wynik rodziny {family}")


def sample_inputs():
    obs = {name: observation(name) for name in "ABCDE"}
    scores = {
        "AB": (0.95, 0.67, 0.37),
        "AD": (None, 0.9, 0.9),
        "BC": (0.8, 0.8, 0.8),
        "BD": (0.8, 0.8, 0.8),
    }
    raw = [compare(obs[a], obs[b], f, score) for (a, b), values in scores.items()
           for f, score in zip(FAMILIES, values)]
    return Inputs(list(obs.values()), raw, {o.canonical_id: name for name, o in obs.items()}, seed=19)


def control_report():
    inputs = sample_inputs()
    eval_config = {**CONFIG, "profiles": {"kontrola": {"families": list(FAMILIES), "min_support": 2}}}
    ev = {"bundle": Bundle(observations=list(inputs.observations)), "raw": inputs.raw, "label": inputs.label,
          "seed": inputs.seed, "dataset_version": "e18-control-v1"}
    return run_counterfactual("unused", {}, eval_config, {"profile": "kontrola", "targets": ["A", "E"]}, eval_result=ev)


def test_targeted_ablation_and_minimum_ties():
    result = control_report()
    switch = result["switch"]
    ids_by_name = {v: k for k, v in switch.inputs.label.items()}
    a = ids_by_name["A"]
    scenarios = switch.all_scenarios(a)
    assert [sorted(s.disabled) for s in scenarios] == [
        list(mask) for n in range(1, 4) for mask in combinations(FAMILIES, n)
    ]
    assert len({s.run_id for s in scenarios} | {switch.baseline.run_id}) == 8
    assert switch.with_enabled(a, FAMILIES) is switch.baseline
    assert switch.scenario(a, ["asset", "dom"]) is switch.scenario("A", ["dom", "asset"])
    assert switch.scenario("B", ["asset", "dom"]).run_id != switch.scenario(a, ["asset", "dom"]).run_id

    for scenario in scenarios:
        cf = scenario.run.config["counterfactual"]
        assert cf == {"material_id": a, "disabled_families": sorted(scenario.disabled),
                      "baseline_run_id": switch.baseline.run_id}
        assert scenario.run.dataset_version == "e18-control-v1"
        assert scenario.run.seed == switch.baseline.run.seed
        assert {k: v for k, v in scenario.run.config["fusion"].items() if k != "similarity_version"} == {
            k: v for k, v in switch.baseline.run.config["fusion"].items() if k != "similarity_version"
        }
        for pair, edge in switch.baseline.edges.items():
            if a not in pair:
                assert {k: v for k, v in edge.items() if k != "pair_evidence_id"} == {
                    k: v for k, v in scenario.edges[pair].items() if k != "pair_evidence_id"
                }
        assert all(a in row["pair_ids"] for row in switch.compare(a, scenario.disabled)["edge_changes"])

    assert switch.compare(a, ["asset"])["classification"] == "utrata_krawedzi"
    assert switch.compare(a, ["asset", "dom"])["classification"] == "zmiana_grupy"
    assert switch.compare("E", ["asset"])["classification"] == "brak_zmiany"
    minimal = switch.minimal_change(a)
    assert minimal["minimal_size"] == 2
    assert [row["disabled"] for row in minimal["minimal"]] == [list(c) for c in combinations(FAMILIES, 2)]
    assert all(row["group_after"] == ["A"] for row in minimal["minimal"])
    assert not switch.minimal_change("E")["changed"]
    assert switch.view(a, FAMILIES)["assignment_status"] == "insufficient_data"
    assert switch.view("B", [])["assignment_status"] == "grouped"

    reversed_inputs = Inputs(tuple(reversed(switch.inputs.observations)), tuple(reversed(switch.inputs.raw)),
                             switch.inputs.label, switch.inputs.seed)
    reordered = FamilySwitch(reversed_inputs, switch.cfg)
    assert reordered.minimal_change(a) == minimal
    assert reordered.view(a, ["asset"]) == switch.view(a, ["asset"])


def test_reasons_path_and_resolvable_evidence(tmp_path):
    result = control_report()
    switch = result["switch"]
    rep = result["report"]
    a = switch.view("A")["material_id"]
    target = rep["targets"][a]
    direct = {h["member"] for h in target["baseline_view"]["direct"]}
    assert direct == {"B", "D"}
    (only_indirect,) = target["baseline_view"]["indirect"]
    assert only_indirect["member"] == "C" and only_indirect["via"] == ["B"]
    baseline_changes = {tuple(s["disabled"]): s for s in target["scenarios"]}
    row = baseline_changes[("asset",)]["edge_changes"][0]
    assert row["pair"] == ["A", "B"] and row["kind"] == "spadek_podobienstwa"
    assert row["similarity_delta"] < 0 and row["coverage_delta"] == pytest.approx(-1 / 3)
    assert row["now"]["status"] == "ok" and row["now"]["fused"] == 0.52
    row = next(r for r in baseline_changes[("dom",)]["edge_changes"] if r["pair"] == ["A", "D"])
    assert row["kind"] == "utrata_pokrycia" and row["similarity_delta"] == 0
    assert row["now"]["status"] == "insufficient_coverage" and row["now"]["score"] is None

    for view in [target["baseline_view"], *(s["view"] for s in target["scenarios"])]:
        for path in view["direct"] + view["indirect"]:
            for hop in path["hops"]:
                evidence = rep["pair_evidence"][hop["pair_evidence_id"]]
                assert evidence["run_id"] == view["run_id"]
                assert set(evidence["pair"]) == {hop["from_id"], hop["to_id"]}
                assert hop["components"]
                for f in hop["components"]:
                    source = rep["pair_evidence"][f["pair_evidence_id"]]
                    assert source["pair"] == evidence["pair"] and source["method_id"] == f["method"]
                    assert set(source["evidence_refs"]) == set(source["pair"])
                    if a in evidence["pair"]:
                        assert f["family"] not in view["disabled"]

    out = write_outputs(result, tmp_path)
    written = json.loads((out / "counterfactual.json").read_text())
    assert written == rep
    Bundle(observations=list(switch.inputs.observations), pairs=list(rep["pair_evidence"].values()),
           runs=list(rep["cluster_runs"].values()))
    assert "Utrata pokrycia" in (out / "counterfactual.md").read_text()


def test_new_edge_after_weak_signal_is_removed_and_support_gate_is_fixed():
    a, b = observation("A"), observation("B")
    names = {a.canonical_id: "A", b.canonical_id: "B"}
    raw = [compare(a, b, f, score) for f, score in zip(FAMILIES, [0.1, 0.8, 0.8])]
    cfg = {**CONFIG, "coverage_min": 0.3, "similarity_version": "e18-control", "dataset_version": "e18-control-v1"}
    switch = FamilySwitch(Inputs([a, b], raw, names), cfg)
    (gain,) = switch.compare("A", ["asset"])["edge_changes"]
    assert gain["kind"] == "nowa_krawedz" and gain["similarity_delta"] > 0 and gain["coverage_delta"] < 0
    assert switch.compare("A", ["asset"])["classification"] == "zmiana_grupy"
    # Zebrane dowody dają 0.8, ale przy jednym źródle porównanie nie spełnia warunku wsparcia.
    edge = next(iter(switch.scenario("A", ["asset", "dom"]).edges.values()))
    assert edge["fused"] == 0.8 and edge["status"] == "insufficient_support" and not edge["accepted"]
    (comparison,) = switch.compare("A", ["asset", "dom"])["comparison_changes"]
    assert comparison["kind"] == "zmiana_porownania" and comparison["similarity_delta"] > 0

    base_raw = [compare(a, b, f, 0.8) for f in FAMILIES]
    supported = FamilySwitch(Inputs([a, b], base_raw, names), cfg)
    (loss,) = supported.compare("A", ["asset", "dom"])["edge_changes"]
    assert loss["kind"] == "utrata_wsparcia" and loss["similarity_delta"] == 0


def test_invalid_controls():
    switch = control_report()["switch"]
    with pytest.raises(CounterfactualError, match="nieznany materiał"):
        switch.scenario("Z", ["asset"])
    with pytest.raises(CounterfactualError, match="nieznane rodziny"):
        switch.with_enabled("A", ["unconfigured"])
    with pytest.raises(CounterfactualError, match="nieznane rodziny"):
        switch.scenario("A", ["unconfigured"])
