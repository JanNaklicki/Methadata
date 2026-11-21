"""F04: bazowy pomiar = Store (F02) -> runner (F03) z assets-jaccard-v1 (E01) i components-threshold-v1 (K01).
Moduł nie implementuje algorytmów; wybiera korpus, składa konfigurację runnera, weryfikuje wejście i wynik."""
from __future__ import annotations

import hashlib
import json
import re
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping, Optional

from ..clustering.components import build_result
from ..contracts import discover
from ..experiments.assets.adapters import KIND_IMAGE
from ..ingest import Store
from ..runner import config_hash, run_experiment, save_run

BASELINE_VERSION = "1"


class BaselineError(Exception):
    """Wejście lub konfiguracja rozbieżne z pomiarem."""


def load_baseline_config(path: str | Path) -> dict:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    frozen = cfg.get("frozen_run_config_hash")
    if frozen is not None and config_hash(cfg["run"]) != frozen:
        raise BaselineError(f"sekcja `run` zmieniona względem zamrożonej ({config_hash(cfg['run'])} != {frozen}); "
                            "zaktualizuj frozen_run_config_hash świadomie")
    return cfg


def _label(source_ref: str, pattern: str) -> str:
    m = re.fullmatch(pattern, source_ref)
    return m.group(1) if m else source_ref


def select_corpus(store: Store, corpus_cfg: Mapping[str, Any]):
    """-> (obserwacje korpusu, wyłączone aliasy {source_ref: powód}, samodzielne URL-e)."""
    tag = corpus_cfg.get("include_tag", "page_capture")
    drop_tags = set(corpus_cfg.get("exclude_tags", []))
    drop_refs = set(corpus_cfg.get("exclude_source_refs", []))
    corpus, excluded, urls = [], {}, []
    for o in sorted(store.observations(), key=lambda o: o.source_ref):
        if "url_observation" in o.tags:
            urls.append(o)
        elif tag not in o.tags:
            excluded[o.source_ref] = "inny rodzaj wejścia: " + ",".join(o.tags)
        elif drop_tags & set(o.tags):
            excluded[o.source_ref] = "tag wykluczający: " + ",".join(sorted(drop_tags & set(o.tags)))
        elif o.source_ref in drop_refs:
            excluded[o.source_ref] = "odłożony w konfiguracji"
        else:
            corpus.append(o)
    seen: dict[str, str] = {}
    for o in corpus:  # kopie bajt w bajt: jeden materiał, raportuj aliasy
        seen.setdefault(o.canonical_id, o.source_ref)
    return corpus, excluded, urls


def corpus_fingerprint(corpus, pattern: str) -> str:
    rows = sorted({(_label(o.source_ref, pattern), o.canonical_id) for o in corpus})
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]


def run_baseline(store_dir: str | Path, cfg: Mapping[str, Any], *, verify: bool = True,
                 adapters: Optional[Mapping[str, Any]] = None) -> dict:
    store = Store(store_dir)
    cc = cfg.get("corpus", {})
    pat = cc.get("label_pattern", r"dir:(.+)")
    corpus, excluded, urls = select_corpus(store, cc)
    if not corpus:
        raise BaselineError("pusty korpus: magazyn nie zawiera obserwacji page_capture spełniających konfigurację")
    uniq: dict[str, list[str]] = {}
    for o in corpus:
        uniq.setdefault(o.canonical_id, []).append(_label(o.source_ref, pat))
    fp = corpus_fingerprint(corpus, pat)
    dataset_version = f"{cfg.get('dataset_name', 'dataset')}@{fp}"
    adapters = adapters if adapters is not None else discover(strict=False)

    run_cfg = json.loads(json.dumps(cfg["run"]))
    run_cfg["dataset_version"] = dataset_version
    run_cfg["cluster_config"] = {**run_cfg.get("cluster_config", {}), "dataset_version": dataset_version}
    res = run_experiment(run_cfg, corpus, adapters, reader=store)
    bundle, record = res["bundle"], res["record"]
    label = {c: "|".join(sorted(set(v))) for c, v in uniq.items()}
    mats = sorted(label.values())

    cluster_run = bundle.runs[0]
    groups_by_id: dict[str, list[str]] = {}
    singletons = []
    for a in cluster_run.assignments:
        if a.cluster_ids:
            groups_by_id.setdefault(a.cluster_ids[0], []).append(label[a.material_id])
        else:
            singletons.append(label[a.material_id])
    groups = sorted((sorted(g, key=_natural) for g in groups_by_id.values()), key=lambda g: _natural(g[0]))

    imgs: dict[str, set[str]] = {}
    obs_canon = {o.observation_id: o.canonical_id for o in bundle.observations}
    for f in bundle.features:
        if f.family == "asset" and f.kind == KIND_IMAGE:
            imgs.setdefault(obs_canon[f.observation_id], set()).add(f.value)
    method = run_cfg["comparators"][0]
    pairs = []
    for p in bundle.pairs:
        if p.method_id != method:
            continue
        a, b = p.pair
        shared = sorted(imgs.get(a, set()) & imgs.get(b, set()))
        pairs.append({"a": label[a], "b": label[b], "score": p.score, "shared_images": len(shared),
                      "shared_sha256": shared, "pair_evidence_id": p.pair_evidence_id, "explanation": p.explanation})
    pairs.sort(key=lambda r: (-(r["score"] or 0), _natural(r["a"]), _natural(r["b"])))

    # przypadek zawodności: niski próg skleja rodziny przez słabe krawędzie (łańcuch)
    probe = cfg.get("failure_probe_threshold")
    failure = None
    if probe:
        r = build_result(bundle.observations, bundle.pairs, {**run_cfg["cluster_config"], "threshold": probe})
        failure = {"threshold": probe, "groups": sorted((sorted((label[m] for m in c.members), key=_natural)
                                                        for c in r.clusters), key=lambda g: _natural(g[0]))}

    report = {
        "baseline_version": BASELINE_VERSION, "run_id": record["run_id"], "dataset_version": dataset_version,
        "config_hash": record["config_hash"], "adapters": record["adapters"],
        "threshold": run_cfg["cluster_config"].get("threshold"),
        "counts": {"materials": len(mats), "pairs": len(pairs), "pairs_total": record["counts"]["pairs_total"],
                   "pairs_with_shared_images": sum(1 for p in pairs if p["shared_images"] > 0)},
        "materials": mats, "aliases": {label[c]: sorted(set(v)) for c, v in uniq.items() if len(set(v)) > 1},
        "excluded": excluded,
        "groups": groups, "singletons": sorted(singletons, key=_natural),
        "pairs": pairs, "failure_case": failure,
        "standalone_urls": [{"observation_id": o.observation_id, "canonical_id": o.canonical_id,
                             "source_ref": o.source_ref, "state": "poza zbiorem porównań: brak grafik do Jaccarda"}
                            for o in urls],
        "checks": [],
    }
    if verify and cfg.get("expected"):
        report["checks"] = verify_expected(report, cfg["expected"])
    return {"record": record, "bundle": bundle, "report": report}


def _natural(s: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def verify_expected(report: Mapping[str, Any], exp: Mapping[str, Any]) -> list[dict]:
    checks = []

    def chk(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": "" if ok else detail})

    c = report["counts"]
    if "dataset_fingerprint" in exp:
        got = report["dataset_version"].rsplit("@", 1)[-1]
        chk("odcisk korpusu", got == exp["dataset_fingerprint"], f"{got} != {exp['dataset_fingerprint']}")
    if "materials" in exp:
        chk(f"unikalne strony = {exp['materials']}", c["materials"] == exp["materials"], str(c["materials"]))
    if "pairs" in exp:
        chk(f"pary = {exp['pairs']}", c["pairs"] == exp["pairs"] == c["pairs_total"], f"{c['pairs']}/{c['pairs_total']}")
    if "pairs_with_shared_images" in exp:
        chk(f"pary ze wspólnymi grafikami = {exp['pairs_with_shared_images']}",
            c["pairs_with_shared_images"] == exp["pairs_with_shared_images"], str(c["pairs_with_shared_images"]))
    if "threshold" in exp:
        chk(f"próg = {exp['threshold']}", report["threshold"] == exp["threshold"], str(report["threshold"]))
    if "groups" in exp:
        want = sorted((sorted(g, key=_natural) for g in exp["groups"]), key=lambda g: _natural(g[0]))
        chk(f"grupy = {want}", report["groups"] == want, str(report["groups"]))
        rest = sorted(set(report["materials"]) - {m for g in want for m in g}, key=_natural)
        chk("reszta to singletony", report["singletons"] == rest, f"{report['singletons']} != {rest}")
    for key, want in (exp.get("scores") or {}).items():
        a, b = key.split("-")
        got = next((p["score"] for p in report["pairs"] if {p["a"], p["b"]} == {a, b}), None)
        chk(f"Jaccard {key} = {want}", got is not None and abs(got - want) < exp.get("score_tolerance", 0.0005), str(got))
    return checks


def write_outputs(res: Mapping[str, Any], out_dir: str | Path) -> Path:
    out = save_run({"record": res["record"], "bundle": res["bundle"]}, out_dir)
    (out / "baseline_report.json").write_text(json.dumps(res["report"], indent=1, ensure_ascii=False, sort_keys=True),
                                              encoding="utf-8")
    return out
