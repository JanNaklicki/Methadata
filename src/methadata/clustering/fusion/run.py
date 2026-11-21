"""Fuzja na rzeczywistym korpusie: Store (F02) -> ekstrakcja (runner F03: assets + leksykalna) -> komparatory z cechami
całego korpusu -> fuzja -> K01. Korpus i metryki bazowe jak w pomiarze F04 (baseline.select_corpus)."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Optional

from methadata.baseline.core import _natural, corpus_fingerprint, select_corpus
from methadata.contracts import Bundle, discover
from methadata.ingest import Store
from methadata.runner import run_experiment, save_run

from .core import cluster_config, collect_pairs, fuse_and_cluster, resolve_config

EXTRACTORS = ["assets-hash-extractor", "lexical-text-extractor"]


def run_fusion(store_dir: str | Path, baseline_cfg: Mapping[str, Any], fusion_cfg: Mapping[str, Any], *,
               adapters: Optional[Mapping[str, Any]] = None) -> dict:
    store = Store(store_dir)
    cc = baseline_cfg.get("corpus", {})
    pat = cc.get("label_pattern", r"dir:(.+)")
    corpus, excluded, _ = select_corpus(store, cc)
    if not corpus:
        raise ValueError("pusty korpus")
    fp = corpus_fingerprint(corpus, pat)
    dataset_version = f"{baseline_cfg.get('dataset_name', 'dataset')}@{fp}"
    adapters = adapters if adapters is not None else discover(strict=False)
    fcfg = resolve_config({**fusion_cfg, "dataset_version": dataset_version})
    base = {"dataset_version": dataset_version, "seed": baseline_cfg.get("run", {}).get("seed", 0),
            "extractors": EXTRACTORS, "comparators": [], "clusterer": "components-threshold-v1",
            "cluster_config": cluster_config(fcfg), "relation": "campaign_candidate"}
    ext = run_experiment(base, corpus, adapters, reader=store)   # tylko cechy; pary liczymy poniżej na pełnym korpusie
    obs, by_obs = ext["bundle"].observations, {}
    for f in ext["bundle"].features:
        by_obs.setdefault(f.observation_id, []).append(f)
    comparators = [adapters[m] for m in _method_ids(fcfg)]
    run_tag = "fusion-inputs-" + hashlib.sha256(json.dumps([dataset_version, sorted(fcfg["families"])]).encode()).hexdigest()[:10]
    raw = collect_pairs(obs, by_obs, comparators, run_id=run_tag)
    res = fuse_and_cluster(obs, raw, fcfg, seed=int(base["seed"]))
    label = {}
    for o in corpus:
        m = re.fullmatch(pat, o.source_ref)
        label.setdefault(o.canonical_id, set()).add(m.group(1) if m else o.source_ref)
    label = {c: "|".join(sorted(v)) for c, v in label.items()}
    grp = lambda gs: sorted((sorted((label[m] for m in g), key=_natural) for g in gs), key=lambda g: _natural(g[0]))  # noqa: E731
    pairs = []
    for p, pf in sorted(res.fusions.items(), key=lambda kv: (-(kv[1].score or 0), kv[0])):
        d = pf.details()
        d.update({"a": label[p[0]], "b": label[p[1]]})
        pairs.append(d)
    report = {"dataset_version": dataset_version, "run_id": res.run.run_id, "config": res.config,
              "groups": grp(c.members for c in res.clusters),
              "singletons": sorted((label[a.material_id] for a in res.run.assignments if not a.cluster_ids), key=_natural),
              "ablation_groups": {k: grp(v) for k, v in res.ablation_groups.items()},
              "status_counts": {s: sum(1 for p in pairs if p["status"] == s) for s in sorted({p["status"] for p in pairs})},
              "pairs": pairs, "excluded": excluded}
    bundle = Bundle(observations=obs, features=ext["bundle"].features, statuses=ext["bundle"].statuses,
                    pairs=sorted([*raw, *res.pairs], key=lambda p: p.pair_evidence_id), runs=[res.run])
    return {"report": report, "bundle": bundle, "result": res, "record": {**ext["record"], "fusion_run_id": res.run.run_id}}


def _method_ids(fcfg):
    # id adaptera komparatora = jego method_id (konwencja E01/E02/E03)
    return [fc["method_id"] for _, fc in sorted(fcfg["families"].items())]


def write_outputs(res: Mapping[str, Any], out_dir: str | Path) -> Path:
    out = save_run({"record": res["record"], "bundle": res["bundle"]}, out_dir)
    (out / "fusion_report.json").write_text(json.dumps(res["report"], indent=1, ensure_ascii=False, sort_keys=True),
                                            encoding="utf-8")
    return out
