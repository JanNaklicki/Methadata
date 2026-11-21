"""E18: wyłączanie śladów jednego materiału nad zamrożonymi dowodami F06/K09."""
from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from methadata.baseline.core import _natural
from methadata.clustering.fusion.core import ST_COVERAGE, ST_NO_EVIDENCE, ST_SUPPORT, FusionResult, fuse_and_cluster, resolve_config
from methadata.contracts import ClusterRun, Observation, PairEvidence

VERSION = "1"
CLASSES = ("brak_zmiany", "utrata_krawedzi", "nowa_krawedz", "zmiana_grupy")


class CounterfactualError(ValueError):
    """Błędna konfiguracja lub wejście."""


def load_config(path: str | Path) -> dict:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(cfg, dict) or not cfg.get("profile"):
        raise CounterfactualError("brak profilu F06 w konfiguracji E18")
    return cfg


@dataclass(frozen=True)
class Inputs:
    observations: Sequence[Observation]
    raw: Sequence[PairEvidence]
    label: Mapping[str, str] = field(default_factory=dict)
    seed: int = 0

    def name(self, cid: str) -> str:
        return self.label.get(cid, cid)

    @classmethod
    def from_eval(cls, ev: Mapping[str, Any]) -> Inputs:
        return cls(ev["bundle"].observations, ev["raw"], ev["label"], ev["seed"])


def disabled_combinations(families: Iterable[str]) -> list[frozenset[str]]:
    names = sorted(set(families))
    return [frozenset(c) for n in range(1, len(names) + 1) for c in combinations(names, n)]


@dataclass(frozen=True)
class Scenario:
    material_id: str | None
    disabled: frozenset[str]
    enabled: tuple[str, ...]
    result: FusionResult
    edges: Mapping[tuple[str, str], dict]

    @property
    def run(self) -> ClusterRun:
        return self.result.run

    @property
    def run_id(self) -> str:
        return self.run.run_id

    @property
    def key(self) -> str:
        return "bez:" + "+".join(sorted(self.disabled)) if self.disabled else "baseline"

    @property
    def groups(self) -> tuple[tuple[str, ...], ...]:
        return tuple(c.members for c in self.result.clusters)

    def group_of(self, cid: str) -> tuple[str, ...]:
        return next((g for g in self.groups if cid in g), (cid,))

    def accepted(self) -> dict[tuple[str, str], dict]:
        return {k: e for k, e in self.edges.items() if e["accepted"]}


def _edge_records(result: FusionResult) -> dict[tuple[str, str], dict]:
    accepted = {(e.source, e.target) for e in result.run.edges}
    records = {}
    for p in result.pairs:
        pf = result.fusions[p.pair]
        records[p.pair] = {
            "score": pf.score, "fused": pf.fused, "status": pf.status, "coverage": pf.coverage,
            "supporting_origins": pf.supporting_origins, "accepted": p.pair in accepted,
            "pair_evidence_id": p.pair_evidence_id,
            "components": [
                {"family": f.family, "method": f.method_id, "score": f.score, "pair_evidence_id": f.pair_evidence_id}
                for f in pf.families if f.score is not None
            ],
        }
    return records


_EMPTY_PAIR = {"score": None, "fused": None, "status": ST_NO_EVIDENCE, "coverage": 0.0,
               "supporting_origins": 0, "accepted": False, "pair_evidence_id": None, "components": []}


def _names(inputs: Inputs, group: Iterable[str]) -> list[str]:
    return sorted((inputs.name(cid) for cid in group), key=_natural)


def _groups(inputs: Inputs, groups: Iterable[Iterable[str]]) -> list[list[str]]:
    return sorted((_names(inputs, group) for group in groups), key=lambda group: _natural(group[0]))


def _ordered_groups(scenario: Scenario) -> list[dict]:
    return [{"cluster_id": c.cluster_id, "member_ids": list(c.members)} for c in scenario.result.clusters]


def _loss_reason(after: Mapping[str, Any]) -> str:
    if after["status"] in (ST_NO_EVIDENCE, ST_COVERAGE):
        return "utrata_pokrycia"
    if after["status"] == ST_SUPPORT:
        return "utrata_wsparcia"
    return "spadek_podobienstwa"


def _delta(before: float | None, after: float | None) -> float | None:
    return None if before is None or after is None else after - before


def _run_version(run: ClusterRun) -> dict:
    return {"run_id": run.run_id, "dataset_version": run.dataset_version, "method_id": run.method_id,
            "method_version": run.method_version, "similarity_version": run.config["similarity_version"]}


def compare_scenarios(inputs: Inputs, base: Scenario, scenario: Scenario) -> dict:
    """Zmiany krawędzi bazowych lub scenariusza, wraz z pokryciem i średnią przed bramkami."""
    changes, comparison_changes = [], []
    for pair in sorted(base.edges.keys() | scenario.edges.keys()):
        before = base.edges.get(pair, _EMPTY_PAIR)
        after = scenario.edges.get(pair, _EMPTY_PAIR)
        if all(before[k] == after[k] for k in before if k != "pair_evidence_id"):
            continue
        if before["accepted"] and not after["accepted"]:
            kind = _loss_reason(after)
        elif after["accepted"] and not before["accepted"]:
            kind = "nowa_krawedz"
        elif before["accepted"]:
            kind = "zmiana_parametrow"
        else:
            kind = "zmiana_porownania"
        destination = comparison_changes if kind == "zmiana_porownania" else changes
        destination.append({
            "pair": _names(inputs, pair), "pair_ids": list(pair), "kind": kind,
            "was": before, "now": after,
            "similarity_delta": _delta(before["fused"], after["fused"]),
            "coverage_delta": _delta(before["coverage"], after["coverage"]),
        })
    counts = {key: sum(change["kind"] == key for change in changes) for key in
              ("utrata_pokrycia", "utrata_wsparcia", "spadek_podobienstwa", "nowa_krawedz", "zmiana_parametrow")}
    bg, sg = set(base.groups), set(scenario.groups)
    if bg != sg:
        classification = "zmiana_grupy"
    elif any(counts[k] for k in ("utrata_pokrycia", "utrata_wsparcia", "spadek_podobienstwa")):
        classification = "utrata_krawedzi"
    elif counts["nowa_krawedz"]:
        classification = "nowa_krawedz"
    else:
        classification = "brak_zmiany"
    return {
        "scenario": scenario.key, "material_id": scenario.material_id,
        "disabled": sorted(scenario.disabled), "enabled": list(scenario.enabled),
        "run_id": scenario.run_id, "baseline_run_id": base.run_id,
        "run_version": _run_version(scenario.run), "baseline_run_version": _run_version(base.run),
        "classification": classification, "groups": _groups(inputs, scenario.groups),
        "group_ids": _ordered_groups(scenario),
        "groups_lost": _groups(inputs, bg - sg), "groups_gained": _groups(inputs, sg - bg),
        "edge_changes": changes, "edge_change_counts": counts, "comparison_changes": comparison_changes,
    }


def _paths(scenario: Scenario, cid: str) -> dict[str, str | None]:
    graph: dict[str, list[str]] = {}
    for a, b in scenario.accepted():
        graph.setdefault(a, []).append(b)
        graph.setdefault(b, []).append(a)
    parent: dict[str, str | None] = {cid: None}
    pending = deque([cid])
    while pending:
        current = pending.popleft()
        for nxt in sorted(graph.get(current, ())):
            if nxt not in parent:
                parent[nxt] = current
                pending.append(nxt)
    return parent


def _path(parent: Mapping[str, str | None], member: str) -> list[str]:
    path = [member]
    while (previous := parent[path[-1]]) is not None:
        path.append(previous)
    return path[::-1]


def _hop(inputs: Inputs, scenario: Scenario, a: str, b: str) -> dict:
    edge = scenario.edges[tuple(sorted((a, b)))]
    return {"from": inputs.name(a), "to": inputs.name(b), "from_id": a, "to_id": b, **edge}


def _resolve(inputs: Inputs, material: str) -> str:
    material_ids = {o.canonical_id for o in inputs.observations}
    if material in material_ids:
        return material
    matches = [cid for cid in sorted(material_ids) if inputs.name(cid) == material]
    if len(matches) != 1:
        raise CounterfactualError(f"niejednoznaczny lub nieznany materiał {material!r}")
    return matches[0]


def view_for_material(inputs: Inputs, scenario: Scenario, material: str) -> dict:
    """Bezpośrednie krawędzie i najkrótsze ścieżki do pozostałych członków grupy."""
    cid = _resolve(inputs, material)
    group = scenario.group_of(cid)
    parent = _paths(scenario, cid)
    direct, indirect = [], []
    for member in group:
        if member == cid:
            continue
        path = _path(parent, member)
        hops = [_hop(inputs, scenario, a, b) for a, b in zip(path, path[1:])]
        row = {"member": inputs.name(member), "member_id": member, "path_ids": path, "hops": hops,
               "via": [inputs.name(node) for node in path[1:-1]]}
        (direct if len(hops) == 1 else indirect).append(row)
    assignment = next(a for a in scenario.run.assignments if a.material_id == cid)
    return {
        "material": inputs.name(cid), "material_id": cid, "scenario": scenario.key,
        "disabled": sorted(scenario.disabled), "run_id": scenario.run_id, "group_ids": list(group),
        "group": _names(inputs, group), "direct": direct, "indirect": indirect,
        "assignment_status": assignment.status.value, "cluster_ids": assignment.cluster_ids,
    }


def minimal_change(inputs: Inputs, base: Scenario, scenarios: Sequence[Scenario], material: str) -> dict:
    """Wszystkie zestawy o najmniejszej liczności zmieniające skład grupy materiału."""
    cid = _resolve(inputs, material)
    before = base.group_of(cid)
    changing = sorted((s for s in scenarios if s.material_id == cid and s.group_of(cid) != before),
                      key=lambda s: (len(s.disabled), sorted(s.disabled)))
    size = len(changing[0].disabled) if changing else None
    rows = []
    for scenario in changing:
        if len(scenario.disabled) != size:
            break
        after = scenario.group_of(cid)
        rows.append({
            "scenario": scenario.key, "disabled": sorted(scenario.disabled), "run_id": scenario.run_id,
            "group_after": _names(inputs, after), "group_after_ids": list(after),
            "separated_member_ids": sorted(set(before) - set(after)),
            "joined_member_ids": sorted(set(after) - set(before)),
        })
    return {"material": inputs.name(cid), "material_id": cid, "baseline_run_id": base.run_id,
            "baseline_group": _names(inputs, before), "baseline_group_ids": list(before),
            "changed": size is not None, "minimal_size": size, "minimal": rows}


class FamilySwitch:
    """Przeliczanie po filtrowaniu dowodów, z cache dla pary materiał–zestaw rodzin."""

    def __init__(self, inputs: Inputs, base_cfg: Mapping[str, Any]):
        self.inputs = inputs
        self.cfg = resolve_config(base_cfg)
        self.families = tuple(self.cfg["families"])
        self._cache: dict[tuple[str, frozenset[str]], Scenario] = {}
        result = fuse_and_cluster(inputs.observations, inputs.raw, self.cfg, seed=inputs.seed)
        self.baseline = Scenario(None, frozenset(), self.families, result, _edge_records(result))

    def scenario(self, material: str, disabled: Iterable[str] = ()) -> Scenario:
        cid = _resolve(self.inputs, material)
        mask = frozenset(disabled)
        if unknown := mask - set(self.families):
            raise CounterfactualError(f"nieznane rodziny: {sorted(unknown)}")
        if not mask:
            return self.baseline
        key = (cid, mask)
        if key not in self._cache:
            methods = {self.cfg["families"][name]["method_id"]: self.cfg["families"][name]["relation"] for name in mask}
            raw = [
                p for p in self.inputs.raw
                if cid not in p.pair or p.method_id not in methods or methods[p.method_id] not in (None, p.relation.value)
            ]
            encoded_mask = json.dumps([cid, sorted(mask)], ensure_ascii=False).encode()
            version = f"{self.cfg['similarity_version']}|e18-{hashlib.sha256(encoded_mask).hexdigest()[:12]}"
            result = fuse_and_cluster(self.inputs.observations, raw, {**self.cfg, "similarity_version": version},
                                      seed=self.inputs.seed)
            result.run.config["counterfactual"] = {"material_id": cid, "disabled_families": sorted(mask),
                                                   "baseline_run_id": self.baseline.run_id}
            enabled = tuple(f for f in self.families if f not in mask)
            self._cache[key] = Scenario(cid, mask, enabled, result, _edge_records(result))
        return self._cache[key]

    def with_enabled(self, material: str, enabled: Iterable[str]) -> Scenario:
        enabled_set = set(enabled)
        if unknown := enabled_set - set(self.families):
            raise CounterfactualError(f"nieznane rodziny: {sorted(unknown)}")
        return self.scenario(material, set(self.families) - enabled_set)

    def all_scenarios(self, material: str) -> list[Scenario]:
        return [self.scenario(material, mask) for mask in disabled_combinations(self.families)]

    def compare(self, material: str, disabled: Iterable[str]) -> dict:
        return compare_scenarios(self.inputs, self.baseline, self.scenario(material, disabled))

    def view(self, material: str, disabled: Iterable[str] = ()) -> dict:
        return view_for_material(self.inputs, self.scenario(material, disabled), material)

    def minimal_change(self, material: str) -> dict:
        return minimal_change(self.inputs, self.baseline, self.all_scenarios(material), material)

    def evidence_index(self, scenarios: Iterable[Scenario]) -> dict[str, dict]:
        """PairEvidence fuzji oraz rekordy źródłowe wskazane przez jej składowe."""
        records = {}
        source_ids = set()
        for scenario in scenarios:
            for pair in scenario.result.pairs:
                records[pair.pair_evidence_id] = pair.model_dump(mode="json")
            source_ids.update(f["pair_evidence_id"] for e in scenario.edges.values() for f in e["components"])
        records.update({p.pair_evidence_id: p.model_dump(mode="json")
                        for p in self.inputs.raw if p.pair_evidence_id in source_ids})
        return records


def run_counterfactual(store_dir: str | Path, baseline_cfg: Mapping[str, Any], eval_cfg: Mapping[str, Any],
                       cf_cfg: Mapping[str, Any], *, adapters=None, eval_result: Mapping[str, Any] | None = None) -> dict:
    """Odczyt zamrożonych dowodów F06 oraz komplet scenariuszy dla każdego celu."""
    from methadata.fusion_eval.core import _profile_config, run_eval

    profile = cf_cfg["profile"]
    if profile not in eval_cfg["profiles"]:
        raise CounterfactualError(f"nieznany profil F06 {profile!r}")
    prof = eval_cfg["profiles"][profile]
    families = prof["families"]
    if "families" in cf_cfg and sorted(cf_cfg["families"]) != sorted(families):
        raise CounterfactualError("rodziny E18 muszą być zgodne z profilem F06")
    ev = run_eval(store_dir, baseline_cfg, eval_cfg, adapters=adapters) if eval_result is None else eval_result
    cfg = _profile_config(eval_cfg, profile, families, prof.get("min_support", 2), ev["dataset_version"])
    inputs = Inputs.from_eval(ev)
    switch = FamilySwitch(inputs, cfg)
    base = switch.baseline
    targets = cf_cfg.get("targets") or sorted(
        {o.canonical_id for o in inputs.observations}, key=lambda cid: (_natural(inputs.name(cid)), cid)
    )
    target_results = {}
    all_scenarios = [base]
    for target in targets:
        cid = _resolve(inputs, target)
        scenarios = switch.all_scenarios(cid)
        all_scenarios.extend(scenarios)
        target_results[cid] = {
            "material": inputs.name(cid), "baseline_view": switch.view(cid),
            "minimal_change": minimal_change(inputs, base, scenarios, cid),
            "scenarios": [
                {**compare_scenarios(inputs, base, s), "view": view_for_material(inputs, s, cid)}
                for s in scenarios
            ],
        }
    report = {
        "version": VERSION, "dataset_version": ev["dataset_version"], "config": dict(cf_cfg),
        "fusion_config": switch.cfg,
        "baseline": {"run_id": base.run_id, "run_version": _run_version(base.run),
                     "groups": _groups(inputs, base.groups), "group_ids": _ordered_groups(base),
                     "accepted_edges": len(base.run.edges)},
        "targets": target_results,
        "cluster_runs": {s.run_id: s.run.model_dump(mode="json") for s in all_scenarios},
        "pair_evidence": switch.evidence_index(all_scenarios),
    }
    return {"report": report, "markdown": render_markdown(report), "switch": switch, "eval": ev}


def render_markdown(report: Mapping[str, Any]) -> str:
    cfg, base = report["fusion_config"], report["baseline"]
    lines = [
        f"# E18 · Co podtrzymuje połączenie", "",
        f"Dane `{report['dataset_version']}`. Profil `{report['config']['profile']}`, próg {cfg['threshold']}, "
        f"minimalne pokrycie {cfg['coverage_min']}, minimalne wsparcie {cfg['min_support']}.",
        f"Przebieg bazowy `{base['run_id']}`, grupy {base['groups']}.", "",
    ]
    for target in report["targets"].values():
        change = target["minimal_change"]
        smallest = [row["disabled"] for row in change["minimal"]]
        lines.extend([
            f"## {target['material']}", "",
            f"Grupa bazowa {change['baseline_group']}. Najmniejsze wyłączenia zmieniające przynależność "
            + (f"{smallest}." if smallest else "nie wystąpiły."), "",
            "| Wyłączone | ClusterRun | Wynik | Utrata pokrycia | Utrata wsparcia | Spadek podobieństwa | Nowe | Zmienione |",
            "|---|---|---|---:|---:|---:|---:|---:|",
        ])
        for scenario in target["scenarios"]:
            counts = " | ".join(map(str, scenario["edge_change_counts"].values()))
            lines.append(f"| {' + '.join(scenario['disabled'])} | `{scenario['run_id']}` | "
                         f"{scenario['classification']} | {counts} |")
        lines.append("")
    return "\n".join(lines)


def write_outputs(result: Mapping[str, Any], out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "counterfactual.json").write_text(
        json.dumps(result["report"], indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    (out / "counterfactual.md").write_text(result["markdown"], encoding="utf-8")
    return out
