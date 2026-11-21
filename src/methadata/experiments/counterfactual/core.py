"""E18: co podtrzymuje połączenie (kontrfakty rodzin cech).

Przy stałej konfiguracji fuzji (wagi, progi, min_support, coverage_min z profilu F06) wyłączamy rodziny cech i
przeliczamy graf K09 (`fuse_and_cluster`) na tych samych surowych PairEvidence; nie powielamy logiki fuzji ani K01.
Dla każdego scenariusza (podzbiór wyłączonych rodzin) wynik to osobny ClusterRun (run_id) i tabela krawędzi.

Przyczyny utraty krawędzi (zgodne z pojęciami K09):
  utrata_pokrycia      - po wyłączeniu nie ma porównania (status no_evidence / insufficient_coverage): spadek POKRYCIA;
  utrata_wsparcia      - wynik jest, ale mniej niż min_support źródeł go wspiera (insufficient_support);
  spadek_podobienstwa  - status ok, lecz średnia ważona spadła poniżej progu: spadek PODOBIEŃSTWA.
Klasyfikacja scenariusza: brak_zmiany | utrata_krawedzi (grupy te same, ale zniknęła krawędź) | zmiana_grupy.

API dla R03 (przełączanie rodzin w przeglądarce): `FamilySwitch` (przeliczone grupy dla dowolnego zestawu włączonych
rodzin, z cache), `view_for_material` (krawędzie bezpośrednie i ścieżka pośrednia, każde przejście z id PairEvidence),
`minimal_change` (najmniejsza kombinacja zmieniająca przynależność + przyczyna)."""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from methadata.baseline.core import _natural
from methadata.clustering.components import build_result
from methadata.clustering.fusion.core import (ST_COVERAGE, ST_NO_EVIDENCE, ST_SUPPORT, cluster_config, fuse_and_cluster,
                                              resolve_config)

VERSION = "1"
CLASSES = ("brak_zmiany", "utrata_krawedzi", "zmiana_grupy")
_EPS = 1e-6


class CounterfactualError(Exception):
    """Błędna konfiguracja lub wejście."""


def load_config(path: str | Path) -> dict:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    for k in ("profile",):
        if k not in cfg:
            raise CounterfactualError(f"brak {k!r} w konfiguracji E18")
    return cfg


# ------------------------------------------------------------------ wejście i scenariusze

@dataclass(frozen=True)
class Inputs:
    """Wejścia fuzji: obserwacje + surowe PairEvidence komparatorów składowych (+ etykiety do prezentacji)."""
    observations: Sequence[Any]
    raw: Sequence[Any]
    label: Mapping[str, str] = field(default_factory=dict)
    seed: int = 0

    def name(self, cid: str) -> str:
        return self.label.get(cid, cid)

    @classmethod
    def from_eval(cls, ev: Mapping[str, Any]) -> "Inputs":
        """Z wyniku methadata.fusion_eval.run_eval (F06)."""
        return cls(ev["bundle"].observations, ev["raw"], ev["label"], int(ev.get("seed", 0)))


def disabled_combinations(families: Iterable[str]) -> list[frozenset[str]]:
    """Wszystkie niepuste podzbiory wyłączanych rodzin (przy trzech rodzinach: 7), rosnąco wg liczności."""
    fam = sorted(families)
    return [frozenset(c) for r in range(1, len(fam) + 1) for c in combinations(fam, r)]


@dataclass(frozen=True)
class Scenario:
    disabled: frozenset[str]
    enabled: tuple[str, ...]
    run: Any                                           # ClusterRun
    groups: tuple[tuple[str, ...], ...]                # canonical_id, posortowane
    edges: Mapping[tuple[str, str], dict]              # para -> rekord krawędzi
    clusters: tuple[Any, ...]
    config: Mapping[str, Any]

    @property
    def run_id(self) -> str:
        return self.run.run_id

    @property
    def key(self) -> str:
        return "bez:" + ("+".join(sorted(self.disabled)) if self.disabled else "-")

    def group_of(self, cid: str) -> tuple[str, ...]:
        return next((g for g in self.groups if cid in g), (cid,))

    def accepted(self) -> dict[tuple[str, str], dict]:
        return {k: e for k, e in self.edges.items() if e["accepted"]}


def _edge_records(res, cfg, disabled) -> dict[tuple[str, str], dict]:
    fused_id = {p.pair: p.pair_evidence_id for p in res.pairs}
    out = {}
    for pair, pf in res.fusions.items():
        d = pf.details()
        out[pair] = {"pair": pair, "score": d["score"], "fused": d["fused"], "status": d["status"],
                     "coverage": d["coverage"], "supporting_origins": d["supporting_origins"],
                     "accepted": d["status"] == "ok" and d["score"] is not None and d["score"] >= cfg["threshold"],
                     "pair_evidence_id": fused_id[pair],
                     "components": [{"family": f["family"], "method": f["method"], "score": f["score"],
                                     "pair_evidence_id": f["pair_evidence_id"]} for f in d["families"]]}
    return out


def compute_scenario(inputs: Inputs, base_cfg: Mapping[str, Any], disabled: Iterable[str] = (), *,
                     cap_support: bool = False) -> Scenario:
    """Przelicza graf przy wyłączonych rodzinach, reszta konfiguracji bez zmian (min_support stałe; z cap_support=True
    obcięte do liczby włączonych rodzin, jak profile ablacji F06 - pokazuje wtedy spadek podobieństwa zamiast wsparcia)."""
    disabled = frozenset(disabled)
    unknown = disabled - set(base_cfg["families"])
    if unknown:
        raise CounterfactualError(f"nieznane rodziny: {sorted(unknown)}")
    enabled = tuple(sorted(set(base_cfg["families"]) - disabled))
    mats = sorted({o.canonical_id for o in inputs.observations})
    if enabled:
        cfg = {**base_cfg, "families": {f: base_cfg["families"][f] for f in enabled}}
        if cap_support:
            cfg["min_support"] = min(base_cfg["min_support"], len(enabled))
        if disabled:
            cfg["similarity_version"] = f"{base_cfg['similarity_version']}|bez:{'+'.join(sorted(disabled))}"
        res = fuse_and_cluster(inputs.observations, inputs.raw, cfg, seed=inputs.seed)
        edges = _edge_records(res, resolve_config(cfg), disabled)
        groups = tuple(sorted((tuple(sorted(c.members)) for c in res.clusters), key=lambda g: g[0]))
        return Scenario(disabled, enabled, res.run, groups, edges, res.clusters, res.config)
    # wszystkie rodziny wyłączone: brak jakiegokolwiek dowodu, każda para no_evidence, same singletony
    cfg = {**base_cfg, "similarity_version": f"{base_cfg['similarity_version']}|bez:{'+'.join(sorted(disabled))}"}
    run = build_result(inputs.observations, [], cluster_config(cfg), seed=inputs.seed).run
    edges = {p: {"pair": p, "score": None, "fused": None, "status": ST_NO_EVIDENCE, "coverage": 0.0,
                 "supporting_origins": 0, "accepted": False, "pair_evidence_id": None, "components": []}
             for p in combinations(mats, 2)}
    return Scenario(disabled, (), run, (), edges, (), cfg)


# ------------------------------------------------------------------ porównanie scenariuszy

def _reason(now: Mapping[str, Any]) -> str:
    if now["status"] in (ST_NO_EVIDENCE, ST_COVERAGE):
        return "utrata_pokrycia"
    if now["status"] == ST_SUPPORT:
        return "utrata_wsparcia"
    return "spadek_podobienstwa"


def _delta(a, b) -> Optional[float]:
    return None if a is None or b is None else round(b - a, 6)


def compare_scenarios(inputs: Inputs, base: Scenario, sc: Scenario) -> dict:
    """Porównanie scenariusza z przebiegiem bazowym: wersje ClusterRun, zmienione krawędzie z przyczyną i dowodami."""
    changes, kept_deltas = [], []
    for pair in sorted(base.edges, key=lambda p: (_natural(inputs.name(p[0])), _natural(inputs.name(p[1])))):
        b, s = base.edges[pair], sc.edges.get(pair)
        if s is None:
            continue
        if b["accepted"] and s["accepted"]:
            kept_deltas.append({"pair": sorted(_names(inputs, pair), key=_natural), "score_delta": _delta(b["score"], s["score"]),
                                "similarity_delta": _delta(b["fused"], s["fused"])})
            continue
        if not (b["accepted"] or s["accepted"]):
            continue
        kind = "nowa_krawedz" if s["accepted"] else _reason(s)
        changes.append({
            "pair": sorted(_names(inputs, pair), key=_natural), "pair_ids": list(pair), "kind": kind,
            "was": _brief(b), "now": _brief(s),
            "similarity_delta": _delta(b["fused"], s["fused"]), "coverage_delta": _delta(b["coverage"], s["coverage"]),
            "pair_evidence_before": b["pair_evidence_id"], "pair_evidence_after": s["pair_evidence_id"],
            "components_before": b["components"], "components_after": s["components"],
            "cause": _cause_text(sorted(sc.disabled), kind, b, s)})
    bg, sg = set(base.groups), set(sc.groups)
    lost = [c for c in changes if c["kind"] != "nowa_krawedz"]
    if bg == sg:
        klass = "utrata_krawedzi" if lost or any(c["kind"] == "nowa_krawedz" for c in changes) else "brak_zmiany"
    else:
        klass = "zmiana_grupy"
    counts = {k: sum(1 for c in changes if c["kind"] == k)
              for k in ("utrata_pokrycia", "utrata_wsparcia", "spadek_podobienstwa", "nowa_krawedz")}
    return {"scenario": sc.key, "disabled": sorted(sc.disabled), "enabled": list(sc.enabled),
            "run_id": sc.run_id, "baseline_run_id": base.run_id, "classification": klass,
            "groups": _gnames(inputs, sc.groups),
            "groups_lost": _gnames(inputs, sorted(bg - sg)), "groups_gained": _gnames(inputs, sorted(sg - bg)),
            "groups_unchanged": len(bg & sg), "edge_changes": changes, "edge_change_counts": counts,
            "kept_edges": kept_deltas,
            "coverage_vs_similarity": _cov_vs_sim(counts)}


def _cov_vs_sim(c: Mapping[str, int]) -> str:
    cov, sim, sup = c["utrata_pokrycia"], c["spadek_podobienstwa"], c["utrata_wsparcia"]
    if not (cov or sim or sup):
        return "brak utraconych krawędzi"
    return (f"utracone krawędzie: {cov} przez spadek pokrycia (brak porównania), {sim} przez spadek podobieństwa "
            f"(wynik poniżej progu), {sup} przez brak wymaganego wsparcia (wynik jest, ale za mało niezależnych źródeł)")


def _brief(e):
    return {"score": e["score"], "fused": e["fused"], "status": e["status"], "coverage": e["coverage"],
            "supporting_origins": e["supporting_origins"], "accepted": e["accepted"]}


def _cause_text(disabled, kind, b, s):
    off = "+".join(disabled)
    if kind == "nowa_krawedz":
        return (f"po wyłączeniu {off} średnia ważona rośnie {b['fused']} -> {s['fused']} i krawędź przekracza próg "
                f"(wsparcie {b['supporting_origins']} -> {s['supporting_origins']})")
    why = {"utrata_pokrycia": f"pokrycie {b['coverage']} -> {s['coverage']}, status {s['status']}",
           "utrata_wsparcia": f"wsparcie {b['supporting_origins']} -> {s['supporting_origins']} (wymagane min_support), "
                              f"podobieństwo {b['fused']} -> {s['fused']}",
           "spadek_podobienstwa": f"podobieństwo {b['fused']} -> {s['fused']} poniżej progu"}[kind]
    return f"po wyłączeniu {off}: {why}"


def _names(inputs, pair):
    return [inputs.name(pair[0]), inputs.name(pair[1])]


def _gnames(inputs, groups):
    return sorted((sorted((inputs.name(m) for m in g), key=_natural) for g in groups), key=lambda g: _natural(g[0]))


# ------------------------------------------------------------------ widok dla R03

def _graph(sc: Scenario) -> dict[str, dict[str, dict]]:
    g: dict[str, dict[str, dict]] = {}
    for (a, b), e in sc.accepted().items():
        g.setdefault(a, {})[b] = e
        g.setdefault(b, {})[a] = e
    return g


def _path(graph, src, dst) -> Optional[list[str]]:
    """Najkrótsza ścieżka po krawędziach zaakceptowanych (deterministyczna: sąsiedzi wg id)."""
    prev = {src: None}
    q = deque([src])
    while q:
        u = q.popleft()
        if u == dst:
            break
        for v in sorted(graph.get(u, {})):
            if v not in prev:
                prev[v] = u
                q.append(v)
    if dst not in prev:
        return None
    out = [dst]
    while prev[out[-1]] is not None:
        out.append(prev[out[-1]])
    return out[::-1]


def _hop(inputs, sc, a, b):
    e = sc.edges[tuple(sorted((a, b)))]
    return {"from": inputs.name(a), "to": inputs.name(b), "score": e["score"], "fused": e["fused"],
            "coverage": e["coverage"], "supporting_origins": e["supporting_origins"],
            "pair_evidence_id": e["pair_evidence_id"],
            "families": [{"family": c["family"], "score": c["score"], "pair_evidence_id": c["pair_evidence_id"]}
                         for c in e["components"]],
            "disabled_families": sorted(sc.disabled)}


def view_for_material(inputs: Inputs, sc: Scenario, material: str) -> dict:
    """Dane widoku R03 dla materiału (etykieta lub canonical_id): grupa, krawędzie bezpośrednie i ścieżki pośrednie.
    Każde przejście niesie id PairEvidence fuzji i PairEvidence rodzin składowych (włączonych w scenariuszu)."""
    cid = _resolve(inputs, material)
    group = sc.group_of(cid)
    graph = _graph(sc)
    direct, indirect = [], []
    for other in group:
        if other == cid:
            continue
        p = _path(graph, cid, other)
        if p is None:   # nie powinno wystąpić w składowej spójnej
            continue
        hops = [_hop(inputs, sc, x, y) for x, y in zip(p, p[1:])]
        row = {"member": inputs.name(other), "member_id": other, "hops": hops, "via": [inputs.name(x) for x in p[1:-1]]}
        (direct if len(hops) == 1 else indirect).append(row)
    return {"material": inputs.name(cid), "scenario": sc.key, "disabled": sorted(sc.disabled), "run_id": sc.run_id,
            "group": sorted((inputs.name(m) for m in group), key=_natural),
            "direct": direct, "indirect": indirect,
            "singleton": len(group) == 1}


def _resolve(inputs, material):
    if material in inputs.label.values():
        return next(c for c, n in sorted(inputs.label.items()) if n == material)
    return material


# ------------------------------------------------------------------ najmniejsza kombinacja zmieniająca przynależność

def _membership_changed(base: Scenario, sc: Scenario, cid: Optional[str]) -> bool:
    if cid is None:
        return set(base.groups) != set(sc.groups)
    return set(base.group_of(cid)) != set(sc.group_of(cid))


def _cause_for(inputs, base: Scenario, sc: Scenario, cid: str) -> dict:
    """Dlaczego przynależność cid się zmieniła: utraceni członkowie z przerwanymi przejściami ścieżki bazowej
    i członkowie nowi z przejściami ścieżki w scenariuszu."""
    bg, sg = set(base.group_of(cid)), set(sc.group_of(cid))
    bgraph, sgraph = _graph(base), _graph(sc)
    separated = []
    for m in sorted(bg - sg):
        p = _path(bgraph, cid, m)
        hops = []
        for x, y in zip(p, p[1:]):
            pair = tuple(sorted((x, y)))
            b, s = base.edges[pair], sc.edges[pair]
            lost = not s["accepted"]
            hops.append({"pair": _names(inputs, (x, y)), "lost": lost,
                         "reason": _reason(s) if lost else "zachowana",
                         "pair_evidence_before": b["pair_evidence_id"], "pair_evidence_after": s["pair_evidence_id"],
                         "similarity": [b["fused"], s["fused"]], "coverage": [b["coverage"], s["coverage"]],
                         "supporting_origins": [b["supporting_origins"], s["supporting_origins"]]})
        separated.append({"member": inputs.name(m), "baseline_path": [inputs.name(x) for x in p], "hops": hops})
    joined = []
    for m in sorted(sg - bg):
        p = _path(sgraph, cid, m)
        joined.append({"member": inputs.name(m), "path": [inputs.name(x) for x in p],
                       "hops": [_hop(inputs, sc, x, y) for x, y in zip(p, p[1:])]})
    return {"separated_members": separated, "joined_members": joined}


def minimal_change(inputs: Inputs, base: Scenario, scenarios: Sequence[Scenario],
                   material: Optional[str] = None) -> dict:
    """Najmniejsza (liczność) kombinacja wyłączonych rodzin, po której zmienia się grupa materiału
    (material=None: dowolna grupa). Zwraca też zestawy minimalne w sensie inkluzji i przyczynę zmiany."""
    cid = _resolve(inputs, material) if material is not None else None
    changing = [s for s in scenarios if s.disabled and _membership_changed(base, s, cid)]
    changing.sort(key=lambda s: (len(s.disabled), sorted(s.disabled)))
    out = {"material": inputs.name(cid) if cid else None, "baseline_run_id": base.run_id,
           "baseline_group": sorted((inputs.name(m) for m in base.group_of(cid)), key=_natural) if cid else None,
           "changed": bool(changing), "minimal_size": len(changing[0].disabled) if changing else None,
           "minimal": [], "inclusion_minimal": [], "changing_scenarios": [s.key for s in changing]}
    if not changing:
        return out

    def row(s):
        r = {"disabled": sorted(s.disabled), "run_id": s.run_id,
             "group_after": sorted((inputs.name(m) for m in s.group_of(cid)), key=_natural) if cid else None}
        if cid:
            r["cause"] = _cause_for(inputs, base, s, cid)
        else:
            cmp_ = compare_scenarios(inputs, base, s)
            r["groups_lost"], r["groups_gained"] = cmp_["groups_lost"], cmp_["groups_gained"]
            r["edge_changes"] = cmp_["edge_changes"]
        return r

    k = len(changing[0].disabled)
    out["minimal"] = [row(s) for s in changing if len(s.disabled) == k]
    out["inclusion_minimal"] = [row(s) for s in changing
                                if not any(o.disabled < s.disabled for o in changing)]
    return out


# ------------------------------------------------------------------ obiekt dla R03

class FamilySwitch:
    """Przeliczanie grup dla dowolnego zestawu włączonych rodzin (z cache) przy stałej konfiguracji fuzji."""

    def __init__(self, inputs: Inputs, base_cfg: Mapping[str, Any], *, cap_support: bool = False):
        self.inputs = inputs
        self.cap_support = cap_support
        self.cfg = resolve_config(base_cfg)
        self.families = tuple(sorted(self.cfg["families"]))
        self._cache: dict[frozenset[str], Scenario] = {}
        self.baseline = self.scenario(())

    def scenario(self, disabled: Iterable[str] = ()) -> Scenario:
        key = frozenset(disabled)
        if key not in self._cache:
            self._cache[key] = compute_scenario(self.inputs, self.cfg, key, cap_support=self.cap_support)
        return self._cache[key]

    def with_enabled(self, enabled: Iterable[str]) -> Scenario:
        return self.scenario(set(self.families) - set(enabled))

    def groups(self, enabled: Iterable[str]) -> list[list[str]]:
        """Przeliczone grupy (etykiety) dla włączonych rodzin."""
        return _gnames(self.inputs, self.with_enabled(enabled).groups)

    def all_scenarios(self) -> list[Scenario]:
        return [self.scenario(d) for d in disabled_combinations(self.families)]

    def compare(self, disabled: Iterable[str]) -> dict:
        return compare_scenarios(self.inputs, self.baseline, self.scenario(disabled))

    def view(self, enabled: Iterable[str], material: str) -> dict:
        return view_for_material(self.inputs, self.with_enabled(enabled), material)

    def minimal_change(self, material: Optional[str] = None) -> dict:
        return minimal_change(self.inputs, self.baseline, self.all_scenarios(), material)


# ------------------------------------------------------------------ pomiar na korpusie

def run_counterfactual(store_dir, baseline_cfg: Mapping[str, Any], eval_cfg: Mapping[str, Any],
                       cf_cfg: Mapping[str, Any], *, adapters=None, eval_result: Optional[Mapping[str, Any]] = None) -> dict:
    """Wejścia i profil bazowy z F06 (run_eval / _profile_config), potem wszystkie kombinacje wyłączeń rodzin."""
    from methadata.fusion_eval.core import _profile_config, run_eval
    ev = eval_result or run_eval(store_dir, baseline_cfg, eval_cfg, adapters=adapters)
    prof = eval_cfg["profiles"][cf_cfg["profile"]]
    fams = cf_cfg.get("families") or prof["families"]
    if sorted(fams) != sorted(prof["families"]):
        raise CounterfactualError("families w konfiguracji E18 muszą być równe rodzinom profilu F06")
    base_cfg = _profile_config(eval_cfg, cf_cfg["profile"], list(fams), prof.get("min_support", 2), ev["dataset_version"])
    inputs = Inputs.from_eval(ev)
    sw = FamilySwitch(inputs, base_cfg)
    base = sw.baseline
    comparisons = [compare_scenarios(inputs, base, s) for s in sw.all_scenarios()]
    swc = FamilySwitch(inputs, base_cfg, cap_support=True)
    capped = [compare_scenarios(inputs, swc.baseline, s) for s in swc.all_scenarios()]
    materials = sorted((v for v in inputs.label.values() if not v.startswith("url-")), key=_natural)
    targets = cf_cfg.get("targets") or materials
    mins = {t: sw.minimal_change(t) for t in targets}
    views = {t: {"baseline": sw.view(sw.families, t),
                 "minimal": [sw.scenario(m["disabled"]) and view_for_material(inputs, sw.scenario(m["disabled"]), t)
                             for m in mins[t]["minimal"]]} for t in targets}
    report = {
        "version": VERSION, "dataset_version": ev["dataset_version"], "config": dict(cf_cfg),
        "fusion_config": {"profile": cf_cfg["profile"], "families": {k: {x: v[x] for x in ("method_id", "weight", "origin", "support_min")}
                                                                      for k, v in sw.cfg["families"].items()},
                          "threshold": sw.cfg["threshold"], "min_support": sw.cfg["min_support"],
                          "coverage_min": sw.cfg["coverage_min"]},
        "baseline": {"run_id": base.run_id, "groups": _gnames(inputs, base.groups),
                     "accepted_edges": len(base.accepted())},
        "scenarios": comparisons,
        "scenarios_capped_min_support": {
            "note": "wariant pomocniczy: min_support obcięte do liczby włączonych rodzin (jak ablacja F06); rozdziela spadek "
                    "podobieństwa od utraty wsparcia",
            "baseline_run_id": swc.baseline.run_id, "scenarios": capped,
            "summary": {c["scenario"]: c["classification"] for c in capped}},
        "summary": {c["scenario"]: c["classification"] for c in comparisons},
        "minimal_change": {"any_group": sw.minimal_change(None), "materials": mins},
        "r03_views": views,
        "sample_note": "Cały korpus (14 materiałów, rodziny p2/p7/p15, p8/p11, p3/p4) to znana próbka kontrolna; "
                       "wynik nie dowodzi uogólnienia.",
    }
    return {"report": report, "markdown": render_markdown(report), "switch": sw, "eval": ev}


def render_markdown(rep: Mapping[str, Any]) -> str:
    L = [f"# E18: co podtrzymuje połączenie (dane {rep['dataset_version']})", "",
         f"Profil bazowy: {rep['fusion_config']['profile']} (rodziny {', '.join(rep['fusion_config']['families'])}, "
         f"próg {rep['fusion_config']['threshold']}, min_support {rep['fusion_config']['min_support']}, "
         f"coverage_min {rep['fusion_config']['coverage_min']}). ClusterRun bazowy {rep['baseline']['run_id']}: "
         f"grupy {_fg(rep['baseline']['groups'])}.", "", rep["sample_note"], "",
         "## Scenariusze", "",
         "| wyłączone | ClusterRun | klasyfikacja | grupy | utracone/nowe krawędzie | pokrycie vs podobieństwo |",
         "|---|---|---|---|---|---|"]
    for c in rep["scenarios"]:
        L.append(f"| {'+'.join(c['disabled'])} | {c['run_id']} | {c['classification']} | {_fg(c['groups']) or '-'} | "
                 f"{len(c['edge_changes'])} | {c['coverage_vs_similarity']} |")
    cp = rep["scenarios_capped_min_support"]
    L += ["", "## Wariant pomocniczy: min_support obcięte do liczby włączonych rodzin", ""]
    for c in cp["scenarios"]:
        L.append(f"- bez {'+'.join(c['disabled'])}: {c['classification']}; grupy {_fg(c['groups']) or '-'}; {c['coverage_vs_similarity']}")
    L += ["", "## Zmienione krawędzie", ""]
    for c in rep["scenarios"]:
        L.append(f"### bez {'+'.join(c['disabled'])} ({c['classification']}; {c['run_id']} vs {c['baseline_run_id']})")
        if not c["edge_changes"]:
            L.append("- bez zmian krawędzi zaakceptowanych")
        for e in c["edge_changes"]:
            L.append(f"- {e['pair'][0]}-{e['pair'][1]}: {e['kind']}; {e['cause']}; PairEvidence {e['pair_evidence_before']} -> "
                     f"{e['pair_evidence_after']}")
        L.append("")
    L += ["## Najmniejsza kombinacja zmieniająca przynależność", ""]
    a = rep["minimal_change"]["any_group"]
    L.append(f"Dowolna grupa: " + (f"{[m['disabled'] for m in a['minimal']]} (rozmiar {a['minimal_size']})" if a["changed"] else "brak zmiany"))
    for t, m in rep["minimal_change"]["materials"].items():
        if not m["changed"]:
            L.append(f"- {t}: żadna kombinacja nie zmienia grupy {m['baseline_group']}")
            continue
        for r in m["minimal"]:
            lost = [f"{h['pair'][0]}-{h['pair'][1]} ({h['reason']})" for s in r["cause"]["separated_members"]
                    for h in s["hops"] if h["lost"]]
            L.append(f"- {t}: wyłączyć {'+'.join(r['disabled'])} -> grupa {r['group_after']} (była {m['baseline_group']}); "
                     f"przerwane: {', '.join(lost) or '-'}; nowe: {[j['member'] for j in r['cause']['joined_members']] or '-'}")
    return "\n".join(L) + "\n"


def _fg(groups):
    return "[" + "; ".join("{" + ",".join(g) + "}" for g in groups) + "]" if groups else ""


def write_outputs(res: Mapping[str, Any], out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "counterfactual.json").write_text(json.dumps(res["report"], indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    (out / "counterfactual.md").write_text(res["markdown"], encoding="utf-8")
    return out
