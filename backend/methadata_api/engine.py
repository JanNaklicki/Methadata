"""Silnik analizy współdzielony z CLI F04 i F06."""
from __future__ import annotations

import hashlib
import json
from typing import Callable

from methadata.baseline.core import BaselineError, load_baseline_config
from methadata.clustering.fusion.core import fuse_and_cluster
from methadata.runner.run import config_hash
from methadata.fusion_eval.core import _profile_config, collect_corpus_evidence, load_eval_config
from methadata.contracts import Bundle, PairEvidence, discover
from methadata.runner import run_experiment

from .settings import PROFILES
from .storage import Storage
from .summarizer import summarize_clusters
from .views import build_view

Progress = Callable[[str, float], None]


def rekey(bundle: Bundle, run_id: str) -> Bundle:
    """Przepisuje run_id (publiczny, unikalny dla żądania) w dowodach i przebiegu; krawędzie wskazują nowe ID dowodów."""
    mapping: dict[str, str] = {}
    pairs = []
    for p in bundle.pairs:
        d = p.model_dump()
        d.update(run_id=run_id, pair_evidence_id=None)
        np_ = PairEvidence.model_validate(d)
        mapping[p.pair_evidence_id] = np_.pair_evidence_id
        pairs.append(np_)
    runs = []
    for r in bundle.runs:
        d = r.model_dump()
        d["run_id"] = run_id
        for e in d["edges"]:
            e["pair_evidence_id"] = mapping[e["pair_evidence_id"]]
        runs.append(type(r).model_validate(d))
    return bundle.model_copy(update={"pairs": pairs, "runs": runs})


def fusion_run(settings, observations, store, adapters, dataset_version, run_id, *,
               extra_extractors=(), extra_methods=(), previous=None, previous_record=None):
    path = settings.jury_config
    cfg = json.loads(path.read_text(encoding="utf-8"))
    eval_cfg = load_eval_config(path.parent / cfg["eval_config"])
    name = cfg["fusion_profile"]
    profile = eval_cfg["profiles"][name]
    seed = cfg["seed"]
    fusion_cfg = _profile_config(eval_cfg, name, profile["families"], profile["min_support"], dataset_version)
    methods = {f["method_id"] for f in fusion_cfg["families"].values()}
    extractors = list(dict.fromkeys([*eval_cfg["extractors"], *extra_extractors]))
    extraction_key = config_hash({"config": eval_cfg, "extra_methods": extra_methods,
                                  "extractors": [(name, adapters[name].spec.version) for name in extractors]})
    cached = previous if previous_record and previous_record.get("extraction_key") == extraction_key else None
    extracted, raw = collect_corpus_evidence(
        observations, store, adapters, extractors=extractors, methods=methods | set(extra_methods),
        dataset_version=dataset_version, seed=seed, threshold=fusion_cfg["threshold"], cached=cached,
    )
    ext = extracted["bundle"]
    fused = fuse_and_cluster(ext.observations, raw, fusion_cfg, seed=seed)
    edges = {edge.pair_evidence_id for edge in fused.run.edges}
    pairs = []
    for pair in fused.pairs:
        evidence = fused.fusions[pair.pair]
        supports = {origin.origin for origin in evidence.origins if origin.supports}
        if pair.pair_evidence_id in edges and supports == {"content-text", "dom-structure"}:
            pair = pair.model_copy(update={
                "explanation": "Podobieństwo narracji i szablonu na podstawie tekstu oraz DOM. "
                "Ustalenie wspólnego nadawcy wymaga osobnych dowodów. " + pair.explanation,
            })
        pairs.append(pair)
    bundle = rekey(ext.model_copy(update={"pairs": pairs, "runs": [fused.run]}), run_id)
    # Zachowujemy identyfikatory dowodów składowych, które fuzja zapisała w uzasadnieniu.
    bundle.pairs.extend(p for p in raw if p.method_id in methods)
    for pair in raw:
        if pair.method_id in extra_methods:
            supplemental = pair.model_dump()
            supplemental.update(run_id=run_id, pair_evidence_id=None)
            bundle.pairs.append(PairEvidence.model_validate(supplemental))
    record = {"engine_run_id": fused.run.run_id, "run_id": run_id, "fusion_profile": name,
              "dataset_version": dataset_version, "config": fusion_cfg, "seed": seed, "extraction_key": extraction_key}
    return record, bundle


def compute_run(storage: Storage, run_id: str, observation_id: str, profile: str, progress: Progress) -> dict:
    """-> {"record", "bundle", "view"}. Korpus = indeks + analizowana obserwacja (migawka pod blokadą)."""
    selected = storage.for_profile(profile)
    if selected is None:
        raise ValueError(f"niedostępny profil {profile}")
    progress("Wczytywanie materiałów", 0.1)
    with selected.lock:
        store = selected.store()
        idx = selected.index() or {"observation_ids": []}
        wanted = list(dict.fromkeys([*idx["observation_ids"], observation_id]))
        observations = [store.observation(i) for i in wanted]  # KeyError gdy obserwacji brak
        labels = selected.labels()
    fp = hashlib.sha256("\x1f".join(sorted({o.canonical_id for o in observations})).encode()).hexdigest()[:16]
    adapters = discover(strict=False)
    progress("Analiza grafik, tekstu i struktury strony", 0.3)
    dataset_name = idx.get("dataset_name", "methadata")
    dataset_version = f"{dataset_name}+zgłoszenie@{fp}"
    record, bundle = fusion_run(storage.settings, observations, store, adapters, dataset_version, run_id)
    progress("Przygotowanie wyniku", 0.8)
    record.update(profile=profile, target_observation_id=observation_id)
    view = build_view(run_id, bundle, labels, store, storage.settings.previews_dir, observation_id)
    summarize_clusters(bundle, view, storage.settings.meta_dir / "cluster_summaries.json")
    return {"record": record, "bundle": bundle, "view": view}


__all__ = ["compute_run", "rekey", "PROFILES", "BaselineError"]
