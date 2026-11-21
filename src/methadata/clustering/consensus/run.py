"""Konsensus na rzeczywistym korpusie: dowody (assets, leksykalne, fuzja F06/K09) -> wiele ClusterRun -> rdzenie."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from methadata.contracts import Bundle, discover

from .core import build_result, generate_runs


def run_consensus(store_dir: str | Path, baseline_cfg: Mapping[str, Any], fusion_cfg: Mapping[str, Any],
                  cons_cfg: Mapping[str, Any], *, adapters: Mapping[str, Any] | None = None) -> dict:
    from methadata.clustering.fusion.run import run_fusion
    fres = run_fusion(store_dir, baseline_cfg, fusion_cfg, adapters=adapters)
    bundle = fres["bundle"]
    dv = fres["report"]["dataset_version"]
    sets: dict[str, list] = {}
    for p in bundle.pairs:
        sets.setdefault(p.method_id, []).append(p)
    ens = cons_cfg["ensemble"]
    sets = {k: sets[k] for k in ens["evidence"] if k in sets}
    adapters = adapters if adapters is not None else discover(strict=False)
    specs = [{**s, "evidence": e} for e in sets for s in ens["specs"]]
    seed = baseline_cfg.get("run", {}).get("seed", 0)
    runs = generate_runs(bundle.observations, sets, specs, adapters, dataset_version=dv, seed=seed)
    ccfg = {k: v for k, v in cons_cfg.items() if k not in ("name", "ensemble")}
    res = build_result(bundle.observations, runs, [p for v in sets.values() for p in v], {**ccfg, "dataset_version": dv},
                       seed=seed)
    label = {}
    import re
    pat = baseline_cfg.get("corpus", {}).get("label_pattern", r"dir:(.+)")
    for o in bundle.observations:
        m = re.fullmatch(pat, o.source_ref)
        label[o.canonical_id] = m.group(1) if m else o.source_ref
    report = {
        "dataset_version": dv, "run_id": res.run.run_id, "config": res.config,
        "input_runs": res.run.config["input_runs"],
        "cores": [{"members": [label[m] for m in c.members], "min_agreement": c.min_agreement,
                   "min_evaluable_runs": c.min_evaluable_runs, "weakest": [label[c.weakest.source], label[c.weakest.target]]}
                  for c in res.cores],
        "pairs_top": [{"a": label[p[0]], "b": label[p[1]], "together": pc.together, "evaluable_runs": pc.evaluable_runs,
                       "agreement": pc.agreement, "families_together": list(pc.families_together),
                       "edge": pc.is_edge, "decisive_runs": list(pc.decisive_runs)}
                      for p, pc in sorted(res.pairs.items(), key=lambda kv: (-(kv[1].agreement or 0), kv[0]))[:40]],
        "family_shares": res.family_shares,
        "ablations": [{"family": a.family, "runs_removed": a.runs_removed, "core_pair_retention": a.core_pair_retention,
                       "dominant": a.dominant, "groups_without": [[label[m] for m in g] for g in a.groups_without]}
                      for a in res.ablations],
        "single_vote": res.single_vote, "warnings": list(res.warnings)}
    out = Bundle(observations=bundle.observations, features=bundle.features, statuses=bundle.statuses,
                 pairs=sorted([*bundle.pairs, *res.pair_evidence], key=lambda p: p.pair_evidence_id),
                 runs=[*runs, res.run])
    return {"report": report, "bundle": out, "result": res, "runs": runs}


def write_outputs(res: Mapping[str, Any], out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "consensus_report.json").write_text(json.dumps(res["report"], indent=1, ensure_ascii=False, sort_keys=True),
                                               encoding="utf-8")
    (out / "bundle.json").write_text(res["bundle"].model_dump_json(), encoding="utf-8")
    return out
