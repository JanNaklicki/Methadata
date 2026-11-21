"""K14: scenariusz na korpusie (Store F02): odłożenie materiału (p15), dołączenie przyrostowe, porównanie z pełnym
przeliczeniem, odrzucenie po jednym materiale (leave-one-out) i test kolejności dodawania.
Dowody wejściowe liczy F06 (collect_corpus_evidence) na całym korpusie, więc statystyki (IDF) są wspólne dla obu dróg."""
from __future__ import annotations

import re
from itertools import permutations
from pathlib import Path
from typing import Any, Mapping, Optional

from methadata.baseline.core import _natural, corpus_fingerprint, select_corpus
from methadata.contracts import discover
from methadata.fusion_eval.core import EvalError, _profile_config, collect_corpus_evidence, load_eval_config
from methadata.ingest import Store

from .core import PairBank, attach, build_index, compare_with_full, order_experiment, resolve_config


def load_config(path: str | Path) -> dict:
    import json
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("profile", "holdout", "sample_size", "min_fraction"):
        if key not in cfg:
            raise EvalError(f"brak {key!r} w konfiguracji przyrostu")
    return cfg


def run_scenario(store_dir: str | Path, baseline_cfg: Mapping[str, Any], eval_cfg: Mapping[str, Any],
                 inc_cfg: Mapping[str, Any], *, adapters: Optional[Mapping[str, Any]] = None) -> dict:
    store = Store(store_dir)
    cc = baseline_cfg.get("corpus", {})
    pat = cc.get("label_pattern", r"dir:(.+)")
    corpus, excluded, urls = select_corpus(store, cc)
    if not corpus:
        raise EvalError("pusty korpus")
    fp = corpus_fingerprint(corpus, pat)
    dataset_version = f"{baseline_cfg.get('dataset_name', 'dataset')}@{fp}"
    adapters = adapters if adapters is not None else discover(strict=False)
    missing = [m for m in [*eval_cfg["extractors"], *(f["method_id"] for f in eval_cfg["families"].values())]
               if m not in adapters]
    if missing:
        raise EvalError(f"brak adapterów (zainstaluj extras i entry points): {missing}")
    seed = int(baseline_cfg.get("run", {}).get("seed", 0))

    label: dict[str, str] = {}
    for o in corpus:
        m = re.fullmatch(pat, o.source_ref)
        label.setdefault(o.canonical_id, set()).add(m.group(1) if m else o.source_ref)
    label = {c: "|".join(sorted(v)) for c, v in label.items()}
    for i, o in enumerate(sorted(urls, key=lambda o: o.source_ref), 1):
        label[o.canonical_id] = f"url-{i}"
    by_label = {v: k for k, v in label.items()}

    ext, raw = collect_corpus_evidence([*corpus, *urls], store, adapters, extractors=eval_cfg["extractors"],
                                       methods={f["method_id"] for f in eval_cfg["families"].values()},
                                       dataset_version=dataset_version, seed=seed, threshold=eval_cfg["threshold"])
    obs = ext["bundle"].observations
    profile = eval_cfg["profiles"][inc_cfg["profile"]]
    fcfg = _profile_config(eval_cfg, inc_cfg["profile"], profile["families"], profile.get("min_support", 2),
                           dataset_version)
    config = {"fusion": {k: v for k, v in fcfg.items() if k != "dataset_version"}, "dataset_version": dataset_version,
              "feature_version": f"{fcfg['similarity_version']}@{fp}", "sample_size": inc_cfg["sample_size"],
              "min_fraction": inc_cfg["min_fraction"]}
    resolve_config(config)
    bank = PairBank(raw)
    by_id = {o.canonical_id: o for o in obs}
    everything = sorted(by_id)

    def names(groups):
        return [sorted((label[m] for m in g), key=_natural) for g in groups]

    def one(hold: str) -> dict:
        cid = by_label[hold]
        base_ids = [m for m in everything if m != cid]
        index = build_index([by_id[m] for m in base_ids], bank.subset(base_ids), config, seed=seed)
        res = attach(index, by_id[cid], bank, seed=seed, dataset_version=dataset_version)
        cmp = compare_with_full(res, bank, obs, seed=seed)
        ev = res.evidence
        return {
            "held_out": hold, "decision": ev.decision, "reason": ev.reason,
            "assigned_to": sorted((label[m] for m in next((g for g in res.index.groups() if cid in g), (cid,))),
                                  key=_natural),
            "representative": label.get(ev.representative_id) if ev.representative_id else None,
            "also_qualifying": len(ev.also_qualifying),
            "probes": [{"unit": sorted((label[m] for m in u.members), key=_natural),
                        "coverage": u.coverage, "accepted_fraction": u.accepted_fraction, "qualifies": u.qualifies,
                        "members": [{"member": label[p.member_id], "role": p.role, "status": p.status,
                                     "score": p.score, "accepted": p.accepted,
                                     "pair_evidence_id": p.pair_evidence_id} for p in u.probes]}
                       for u in ev.units if u.qualifies or u.accepted_fraction > 0],
            "index": {"parent": index.index_id, "version_before": index.version, "after": res.index.index_id,
                      "version_after": res.index.version},
            "run_id": res.run.run_id,
            "groups_incremental": names(res.index.groups()), "groups_full": names(map(tuple, cmp["groups_full"])),
            "pair_jaccard": cmp["pair_jaccard"], "identical_groups": cmp["identical_groups"],
            "new_group_identical": cmp["new_group_identical"], "cost": cmp["cost"],
        }

    primary = [one(h) for h in inc_cfg["holdout"]]
    loo = [one(label[m]) for m in sorted(everything, key=lambda m: _natural(label[m])) if not label[m].startswith("url-")]
    exps = []
    for ex in inc_cfg.get("order_experiments", []):
        hold = [by_label[h] for h in ex["holdout"]]
        base_ids = [m for m in everything if m not in hold]
        orders = [list(p) for p in permutations(hold)]
        r = order_experiment(obs, bank, config, base_ids, orders, seed=seed)
        exps.append({"holdout": ex["holdout"], "order_independent": r["order_independent"],
                     "min_pairwise_jaccard_between_orders": r["min_pairwise_jaccard_between_orders"],
                     "orders_identical_to_full": f"{r['orders_identical_to_full']}/{len(orders)}",
                     "groups_full": names(map(tuple, r["groups_full"])),
                     "orders": [{"order": [label[m] for m in o["order"]], "groups": names(map(tuple, o["groups"])),
                                 "decisions": [d["decision"] for d in o["decisions"]],
                                 "jaccard_vs_full": o["jaccard_vs_full"], "comparisons": o["comparisons"]}
                                for o in r["orders"]]})
    return {"dataset_version": dataset_version, "feature_version": config["feature_version"],
            "config": {"profile": inc_cfg["profile"], "sample_size": config["sample_size"],
                       "min_fraction": config["min_fraction"], "fusion": config["fusion"]},
            "holdout": primary, "leave_one_out": loo,
            "leave_one_out_summary": {"materials": len(loo), "identical_groups": sum(r["identical_groups"] for r in loo),
                                      "new_group_identical": sum(r["new_group_identical"] for r in loo),
                                      "diverging": [r["held_out"] for r in loo if not r["new_group_identical"]]},
            "order_experiments": exps, "excluded": excluded}
