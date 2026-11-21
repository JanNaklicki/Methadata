"""Runner: konfiguracja -> ekstrakcja -> porównania wszystkich par -> grupowanie -> zapis przebiegu.
Wywołanie z Pythona (backend W02): `run_experiment(config, observations, adapters, reader=...)`."""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from ..contracts import (AdapterKind, Bundle, ClusterRun, ExtractionStatus, Feature, Observation,
                         PairEvidence, Relation, discover)

RUNNER_VERSION = "1"


class BundleReader:
    """Minimalny ArtifactReader nad Bundle (+ katalog artefaktów dla bajtów). F02 dostarcza pełny magazyn."""

    def __init__(self, observations: Sequence[Observation], artifacts_dir: str | Path | None = None):
        self._obs = {o.observation_id: o for o in observations}
        self._art = {a.artifact_id: a for o in observations for a in o.artifacts}
        self._dir = Path(artifacts_dir) if artifacts_dir else None

    def observation(self, observation_id): return self._obs[observation_id]
    def artifact(self, artifact_id): return self._art[artifact_id]

    def open_bytes(self, ref):
        a = self._art[ref.artifact_id]
        if self._dir is None or not a.local_ref:
            raise FileNotFoundError(f"brak bajtów artefaktu {a.artifact_id}")
        return (self._dir / a.local_ref).read_bytes()

    def children(self, artifact_id):
        return [a for a in self._art.values() if a.parent and a.parent.artifact_id == artifact_id]


def config_hash(config: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def load_config(path: str | Path) -> dict:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    base = Path(path).resolve().parent
    for k in ("bundle", "artifacts_dir", "manifest", "labels", "out_dir"):
        if cfg.get(k) and not Path(cfg[k]).is_absolute():
            cfg[k] = str((base / cfg[k]).resolve())
    cfg["adapter_paths"] = [str((base / p).resolve()) if not Path(p).is_absolute() else p
                            for p in cfg.get("adapter_paths", [])]
    return cfg


def _dedupe(observations: Sequence[Observation]) -> list[Observation]:
    """Jeden materiał (canonical_id) = jedna obserwacja; wybór niezależny od kolejności (najmniejszy observation_id)."""
    best: dict[str, Observation] = {}
    for o in sorted(observations, key=lambda o: o.observation_id):
        best.setdefault(o.canonical_id, o)
    return [best[k] for k in sorted(best)]


def _pick(adapters: Mapping[str, Any], ids: Optional[Sequence[str]], kind: AdapterKind) -> list[Any]:
    if ids is None:
        return [a for k, a in sorted(adapters.items()) if a.spec.kind is kind]
    out = []
    for i in ids:
        a = adapters.get(i)
        if a is None:
            raise KeyError(f"adapter {i!r} nie został odkryty")
        if a.spec.kind is not kind:
            raise TypeError(f"adapter {i!r} ma kind={a.spec.kind.value}, oczekiwano {kind.value}")
        out.append(a)
    return out


def run_experiment(config: Mapping[str, Any], observations: Sequence[Observation], adapters: Mapping[str, Any], *,
                   reader: Any = None, features: Sequence[Feature] = (), statuses: Sequence[ExtractionStatus] = ()) -> dict:
    """Zwraca {"record": opis przebiegu (JSON), "bundle": Bundle}. Wynik nie zależy od kolejności `observations`.
    config: dataset_version, seed, relation, extractors?, comparators?, clusterer, cluster_config?, split?"""
    t0 = time.perf_counter()
    seed = int(config.get("seed", 0))
    relation = Relation(config.get("relation", "shared_template"))
    obs = _dedupe(observations)
    reader = reader or BundleReader(observations)
    extractors = _pick(adapters, config.get("extractors"), AdapterKind.extractor)
    comparators = _pick(adapters, config.get("comparators"), AdapterKind.comparator)
    clusterers = _pick(adapters, [config["clusterer"]] if config.get("clusterer") else None, AdapterKind.clusterer)
    if len(clusterers) != 1:
        raise ValueError("konfiguracja musi wskazać dokładnie jeden clusterer")
    clusterer = clusterers[0]
    chash = config_hash({k: v for k, v in config.items() if k not in ("out_dir",)})
    ids = [o.canonical_id for o in obs]
    run_id = "run-" + hashlib.sha256(
        "\x1f".join([chash, str(config.get("dataset_version", "")), *ids]).encode()).hexdigest()[:16]
    timings: dict[str, float] = {}

    t = time.perf_counter()
    feats: dict[str, Feature] = {f.feature_id: f for f in features}
    stats: list[ExtractionStatus] = list(statuses)
    for o in obs:
        for ex in extractors:
            opt = None
            fs, ss = ex.extract(o, reader=reader, optional=opt)
            feats.update({f.feature_id: f for f in fs})
            stats.extend(ss)
    feat_list = sorted(feats.values(), key=lambda f: f.feature_id)
    by_obs: dict[str, list[Feature]] = {}
    for f in feat_list:
        by_obs.setdefault(f.observation_id, []).append(f)
    timings["extract"] = time.perf_counter() - t

    t = time.perf_counter()
    pairs: list[PairEvidence] = []
    for i, a in enumerate(obs):
        for b in obs[i + 1:]:
            for cmp_ in comparators:
                pairs.extend(cmp_.compare(a, b, {a.observation_id: by_obs.get(a.observation_id, []),
                                                 b.observation_id: by_obs.get(b.observation_id, [])},
                                          run_id=run_id, optional=None))
    pairs.sort(key=lambda p: p.pair_evidence_id)
    timings["compare"] = time.perf_counter() - t

    t = time.perf_counter()
    cr: ClusterRun = clusterer.cluster(obs, pairs, dict(config.get("cluster_config", {})), seed=seed, optional=None)
    cr = cr.model_copy(update={"run_id": run_id, "dataset_version": str(config.get("dataset_version", cr.dataset_version)),
                               "seed": seed, "config": {**cr.config, **{"runner_config_hash": chash}}})
    timings["cluster"] = time.perf_counter() - t
    timings["total"] = time.perf_counter() - t0

    bundle = Bundle(observations=obs, features=feat_list, statuses=stats, pairs=pairs, runs=[cr])
    record = {
        "runner_version": RUNNER_VERSION, "run_id": run_id, "experiment_id": config.get("experiment_id", run_id),
        "config": dict(config), "config_hash": chash, "dataset_version": cr.dataset_version, "seed": seed,
        "relation": relation.value,
        "adapters": {a.spec.id: {"kind": a.spec.kind.value, "version": a.spec.version}
                     for a in [*extractors, *comparators, clusterer]},
        "feature_versions": sorted({f.extractor_version for f in feat_list}),
        "models": sorted({f"{f.vector_ref.model}@{f.vector_ref.model_version}" for f in feat_list if f.vector_ref}),
        "materials": ids, "split": config.get("split"), "timings": timings,
        "counts": {"observations": len(obs), "features": len(feat_list), "pairs_evidence": len(pairs),
                   "pairs_total": len(obs) * (len(obs) - 1) // 2},
    }
    return {"record": record, "bundle": bundle}


def save_run(result: Mapping[str, Any], out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "run.json").write_text(json.dumps(result["record"], indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    (out / "bundle.json").write_text(result["bundle"].model_dump_json(indent=1), encoding="utf-8")
    return out


def load_run(out_dir: str | Path) -> tuple[dict, Bundle]:
    out = Path(out_dir)
    return (json.loads((out / "run.json").read_text(encoding="utf-8")),
            Bundle.model_validate_json((out / "bundle.json").read_text(encoding="utf-8")))


def run_from_config(config: Mapping[str, Any], *, adapters: Optional[Mapping[str, Any]] = None) -> dict:
    for p in config.get("adapter_paths", []):
        if p not in sys.path:
            sys.path.insert(0, p)
    if adapters is None:
        adapters = discover(strict=False)
    src = Bundle.model_validate_json(Path(config["bundle"]).read_text(encoding="utf-8"))
    reader = BundleReader(src.observations, config.get("artifacts_dir"))
    res = run_experiment(config, src.observations, adapters, reader=reader, features=src.features, statuses=src.statuses)
    if config.get("out_dir"):
        save_run(res, config["out_dir"])
    return res
