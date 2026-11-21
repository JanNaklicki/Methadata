"""Analiza nowych/zmienionych reklam: płatnik (S01) -> porównania par -> grupy (runner F03)."""
from __future__ import annotations

import copy
from typing import Sequence

from ..contracts import discover
from ..ingest import Store
from ..runner import run_experiment, save_run
from .core import TAG

DEFAULT_RUN = {
    "seed": 0, "relation": "campaign_candidate",
    "extractors": ["source-payer-extractor"], "comparators": ["source-payer-compare"],
    "clusterer": "components-threshold-v1",
    "cluster_config": {"threshold": 0.5, "similarity_version": "source-payer-compare", "method_id": "source-payer-compare", "relation": "campaign_candidate"},
}


def payer_analyzer(run_config: dict | None = None, *, save: bool = True):
    """Fabryka analizatora `(store, changed_ids) -> dict`. Korpus = wszystkie reklamy z biblioteki w magazynie
    (grupa zależy od całości), raport wskazuje grupy zawierające zmienione rekordy. Wynik zapisany w
    `<store>/monitor/analysis/latest/` (run.json, bundle.json) przez `save_run`."""
    cfg = copy.deepcopy({**DEFAULT_RUN, **(run_config or {})})

    def analyze(store: Store, changed_ids: Sequence[str]) -> dict:
        obs = [o for o in store.observations() if TAG in o.tags]
        cfg["dataset_version"] = cfg["cluster_config"]["dataset_version"] = f"ad-library@{len(obs)}"
        res = run_experiment(cfg, obs, discover(strict=False), reader=store)
        run = res["bundle"].runs[0]
        changed = set(changed_ids)
        by_can = {o.canonical_id: o.observation_id for o in obs}
        members: dict[str, list[str]] = {}
        for a in run.assignments:
            for cid in a.cluster_ids:
                members.setdefault(cid, []).append(by_can.get(a.material_id, a.material_id))
        groups = sorted(sorted(m) for m in members.values())
        touched = [g for g in groups if changed & set(g)]
        if save:
            save_run(res, store.root / "monitor" / "analysis" / "latest")
        return {"run_id": res["record"]["run_id"], "observations": len(obs), "changed": len(changed),
                "groups_touched": touched, "counts": res["record"]["counts"]}

    return analyze

