"""SNF na rzeczywistym korpusie: Store (F02) -> ekstrakcja (runner F03) -> komparatory z cechami całego korpusu
(collect_pairs, jak F06) -> sieci per widok -> SNF -> grupowanie -> porównanie z K09 na tym samym podzbiorze."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Optional

from methadata.baseline.core import _natural, corpus_fingerprint, select_corpus
from methadata.clustering.fusion.core import collect_pairs
from methadata.contracts import Bundle, discover
from methadata.ingest import Store
from methadata.runner import run_experiment, save_run

from .core import compare_with_fusion, resolve_config, snf_cluster

DEFAULT_EXTRACTORS = ["assets-hash-extractor", "lexical-text-extractor", "dom-struct-extractor"]


def run_snf(store_dir: str | Path, baseline_cfg: Mapping[str, Any], snf_cfg: Mapping[str, Any], *,
            adapters: Optional[Mapping[str, Any]] = None, reference: Optional[list] = None) -> dict:
    store = Store(store_dir)
    cc = baseline_cfg.get("corpus", {})
    pat = cc.get("label_pattern", r"dir:(.+)")
    corpus, excluded, _ = select_corpus(store, cc)
    if not corpus:
        raise ValueError("pusty korpus")
    fp = corpus_fingerprint(corpus, pat)
    dataset_version = f"{baseline_cfg.get('dataset_name', 'dataset')}@{fp}"
    adapters = adapters if adapters is not None else discover(strict=False)
    extractors = list(snf_cfg.get("extractors") or DEFAULT_EXTRACTORS)
    cfg = resolve_config({**snf_cfg, "dataset_version": dataset_version})
    methods = [v["method_id"] for _, v in sorted(cfg["views"].items())]
    missing = [m for m in [*extractors, *methods] if m not in adapters]
    if missing:
        raise ValueError(f"brak adapterów (zainstaluj extras i entry points): {missing}")
    seed = int(baseline_cfg.get("run", {}).get("seed", 0))
    base = {"dataset_version": dataset_version, "seed": seed, "extractors": extractors, "comparators": [],
            "clusterer": "components-threshold-v1", "relation": "campaign_candidate",
            "cluster_config": {"threshold": cfg["threshold"], "similarity_version": cfg["similarity_version"],
                               "method_id": "snf-v1", "dataset_version": dataset_version,
                               "relation": "campaign_candidate"}}
    ext = run_experiment(base, corpus, adapters, reader=store)
    obs, by_obs = ext["bundle"].observations, {}
    for f in ext["bundle"].features:
        by_obs.setdefault(f.observation_id, []).append(f)
    tag = "snf-inputs-" + hashlib.sha256(json.dumps([dataset_version, methods]).encode()).hexdigest()[:10]
    raw = collect_pairs(obs, by_obs, [adapters[m] for m in methods], run_id=tag)
    res = snf_cluster(obs, raw, cfg, seed=seed)
    cmp_ = compare_with_fusion(res, obs, raw, seed=seed)

    label = {}
    for o in corpus:
        m = re.fullmatch(pat, o.source_ref)
        label.setdefault(o.canonical_id, set()).add(m.group(1) if m else o.source_ref)
    label = {c: "|".join(sorted(v)) for c, v in label.items()}
    lab = lambda g: sorted((label[m] for m in g), key=_natural)  # noqa: E731
    grp = lambda gs: sorted((lab(g) for g in gs), key=lambda g: _natural(g[0]))  # noqa: E731
    pr = lambda p: [label[p[0]], label[p[1]]]  # noqa: E731

    edges = []
    for p, d in sorted(res.details.items(), key=lambda kv: (-(kv[1]["fused"] or 0), kv[0])):
        if d["fused"] and (d["fused"] >= cfg["threshold"] or d["amplified"] or d["direct"] >= cfg["threshold"]):
            edges.append({"a": label[p[0]], "b": label[p[1]], "fused": d["fused"], "direct": d["direct"],
                          "amplified": d["amplified"], "views": {k: v["score"] for k, v in d["views"].items()},
                          "via": [{"via": label[v["via"]], "strength": v["strength"],
                                   "legs": [{"pair": pr(lg["pair"]), "view": lg["view"], "score": lg["score"],
                                             "pair_evidence_id": lg["pair_evidence_id"]}
                                            for side in ("legs_a", "legs_b") for lg in v[side] if lg["score"] is not None]}
                                  for v in d["via"]],
                          "n_refs": {label[c]: len(r) for c, r in d["refs"].items()}})
    report = {"dataset_version": dataset_version, "run_id": res.run.run_id, "config": {k: v for k, v in res.config.items()},
              "subset": sorted((label[m] for m in res.subset), key=_natural),
              "excluded_missing_views": {label[m]: v for m, v in sorted(res.excluded.items(), key=lambda kv: label[kv[0]])},
              "view_stats": res.view_stats,
              "view_networks": {k: [[label[a], label[b], round(s, 4)] for a, b, s in v] for k, v in res.view_edges.items()},
              "snf_groups": grp(res.groups()),
              "singletons": sorted((label[a.material_id] for a in res.run.assignments if a.status.value == "singleton"),
                                   key=_natural),
              "amplified_edges": [e for e in edges if e["amplified"]],
              "edges": edges,
              "k09": {"groups": grp(cmp_["k09_groups"]), "identical_to_snf": cmp_["identical"],
                      "rand_index": cmp_["rand_index"], "run_id": cmp_["k09_run_id"],
                      "snf_only_pairs": [pr(p) for p in cmp_["snf_only_pairs"]],
                      "k09_only_pairs": [pr(p) for p in cmp_["k09_only_pairs"]],
                      "edge_differences": [{**r, "pair": pr(r["pair"])} for r in cmp_["edge_differences"]],
                      "config": cmp_["k09_config"]},
              "excluded_from_corpus": excluded}
    if reference:
        ref = [sorted(g, key=_natural) for g in reference]
        report["reference_control"] = {"note": "próbka kontrolna (odniesienie z configs/fusion/eval.json), oddzielnie",
                                       "reference": ref, "snf_matches": grp(res.groups()) == sorted(ref, key=lambda g: _natural(g[0])),
                                       "k09_matches": grp(cmp_["k09_groups"]) == sorted(ref, key=lambda g: _natural(g[0]))}
    bundle = Bundle(observations=obs, features=ext["bundle"].features, statuses=ext["bundle"].statuses,
                    pairs=sorted([*raw, *res.pairs], key=lambda p: p.pair_evidence_id), runs=[res.run])
    return {"report": report, "bundle": bundle, "result": res, "comparison": cmp_, "label": label,
            "record": {**ext["record"], "snf_run_id": res.run.run_id}}


def write_outputs(res: Mapping[str, Any], out_dir: str | Path) -> Path:
    out = save_run({"record": res["record"], "bundle": res["bundle"]}, out_dir)
    (out / "snf_report.json").write_text(json.dumps(res["report"], indent=1, ensure_ascii=False, sort_keys=True),
                                         encoding="utf-8")
    return out
