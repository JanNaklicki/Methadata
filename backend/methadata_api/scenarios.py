"""Projekcja porównań E18 dla pary z zapisanej analizy jury."""
from __future__ import annotations

from functools import lru_cache
from threading import Lock
from typing import Any

from methadata.clustering.components import METHOD_ID as COMPONENTS_METHOD
from methadata.clustering.fusion.core import METHOD_ID as FUSION_METHOD, ST_NO_EVIDENCE
from methadata.experiments.counterfactual.core import FamilySwitch, Inputs, Scenario, compare_scenarios, view_for_material

from .errors import ApiError, validation
from .runs import RunManager
from .storage import read_json


def _not_available() -> ApiError:
    return ApiError(404, "scenario_not_available", "Porównanie rodzin dowodów jest niedostępne dla tej pary.")


def _metrics(edge: dict | None) -> dict:
    if edge is None:
        return {"score": None, "fused": None, "status": ST_NO_EVIDENCE, "coverage": 0.0,
                "supporting_origins": 0, "accepted": False}
    # Wewnętrzne identyfikatory surowych dowodów i ich artefakty zostają w migawce.
    return {key: edge[key] for key in ("score", "fused", "status", "coverage", "supporting_origins", "accepted")}


def _variant(switch: FamilySwitch, scenario: Scenario, target: str, pair: tuple[str, str]) -> dict:
    view = view_for_material(switch.inputs, scenario, target)
    return {
        "engine_run_id": scenario.run_id,
        "pair": _metrics(scenario.edges.get(pair)),
        "group": view["group_ids"],
        "assignment_status": view["assignment_status"],
        "indirect_paths": [
            {"member_id": row["member_id"], "hops": [
                {"from_id": hop["from_id"], "to_id": hop["to_id"], "score": hop["score"],
                 "families": [component["family"] for component in hop["components"]]}
                for hop in row["hops"]
            ]}
            for row in view["indirect"]
        ],
    }


class RunScenarios:
    def __init__(self, runs: RunManager):
        self.runs = runs
        self._snapshot = lru_cache(maxsize=4)(self._load)
        # FastAPI obsługuje żądania w wątkach. Jedna migawka i jedno przeliczenie na maskę.
        self._lock = Lock()

    def _load(self, run_id: str, observation_id: str):
        bundle = self.runs.bundle(run_id)
        record = read_json(self.runs.run_dir(run_id) / "run.json")
        if not bundle or not record:
            raise _not_available()
        run = next((r for r in bundle.runs if r.run_id == run_id and r.method_id == COMPONENTS_METHOD), None)
        target = next((o.canonical_id for o in bundle.observations if o.observation_id == observation_id), None)
        pairs = {p.pair_evidence_id: p.pair for p in bundle.pairs
                 if p.run_id == run_id and p.method_id == FUSION_METHOD}
        if run is None or not pairs or not run.config.get("fusion"):
            raise _not_available()
        raw = [p for p in bundle.pairs if p.run_id != run_id]
        switch = FamilySwitch(Inputs(bundle.observations, raw, seed=run.seed),
                              {**run.config["fusion"], "dataset_version": run.dataset_version})
        if switch.baseline.run_id != record.get("engine_run_id"):
            raise _not_available()
        return switch, target, pairs

    def compare(self, state: dict, evidence_id: str, disabled: list[str], material_id: str | None) -> dict[str, Any]:
        if state.get("profile") != "jury":
            raise _not_available()
        with self._lock:
            switch, submitted, pairs = self._snapshot(state["run_id"], state["observation_id"])
            pair = pairs.get(evidence_id)
            if pair is None:
                raise _not_available()
            if material_id is not None and material_id not in pair:
                raise validation("Wybierz materiał z tej pary.", "material_id", "Podaj identyfikator jednej ze stron pary.")
            target = material_id if material_id is not None else submitted if submitted in pair else pair[0]
            if len(disabled) != len(set(disabled)) or not set(disabled) <= set(switch.families):
                raise validation("Podaj dostępne rodziny śladów bez powtórzeń.", "disabled",
                                 f"Dostępne rodziny: {', '.join(switch.families)}.")
            baseline = switch.baseline
            scenario = switch.scenario(target, disabled)
            minimal = switch.minimal_change(target)["minimal"]

        comparison = compare_scenarios(switch.inputs, baseline, scenario)
        return {
            "run_id": state["run_id"],
            "evidence_id": evidence_id,
            "dataset_version": baseline.run.dataset_version,
            "target_id": target,
            "pair_ids": list(pair),
            "families": list(switch.families),
            "disabled": sorted(scenario.disabled),
            "threshold": switch.cfg["threshold"],
            "coverage_min": switch.cfg["coverage_min"],
            "min_support": switch.cfg["min_support"],
            "baseline": _variant(switch, baseline, target, pair),
            "scenario": _variant(switch, scenario, target, pair),
            "edge_changes": [
                {"pair_ids": change["pair_ids"], "kind": change["kind"],
                 "baseline": _metrics(change["was"]), "scenario": _metrics(change["now"])}
                for change in comparison["edge_changes"]
            ],
            "minimal_masks": [row["disabled"] for row in minimal],
        }
