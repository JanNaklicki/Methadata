"""Silnik analizy: ten sam co w CLI (runner F03, konfiguracja `run` z F04). Zwraca przebieg gotowy do zapisu."""
from __future__ import annotations

import hashlib
from typing import Callable, Optional

from methadata.baseline.core import BaselineError, load_baseline_config
from methadata.contracts import Bundle, PairEvidence, discover
from methadata.runner import run_experiment

from .storage import Storage
from .views import build_view

Progress = Callable[[str, float], None]
PROFILES = ("demo",)


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


def compute_run(storage: Storage, run_id: str, observation_id: str, profile: str, progress: Progress) -> dict:
    """-> {"record", "bundle", "view"}. Korpus = indeks + analizowana obserwacja (migawka pod blokadą)."""
    if profile not in PROFILES:
        raise ValueError(f"nieznany profil {profile}")
    progress("Wczytywanie materiałów", 0.1)
    with storage.lock:
        store = storage.store()
        idx = storage.index() or {"observation_ids": []}
        wanted = list(dict.fromkeys([*idx["observation_ids"], observation_id]))
        observations = [store.observation(i) for i in wanted]  # KeyError gdy obserwacji brak
        labels = storage.labels()
    cfg = load_baseline_config(storage.settings.baseline_config)
    run_cfg = {k: v for k, v in cfg["run"].items()}
    fp = hashlib.sha256("\x1f".join(sorted({o.canonical_id for o in observations})).encode()).hexdigest()[:16]
    dataset_version = f"{cfg.get('name', 'baseline')}-index@{fp}"
    run_cfg["dataset_version"] = dataset_version
    run_cfg["cluster_config"] = {**run_cfg.get("cluster_config", {}), "dataset_version": dataset_version}
    progress("Analiza grafik i grupowanie", 0.3)
    res = run_experiment(run_cfg, observations, discover(strict=False), reader=store)
    progress("Przygotowanie wyniku", 0.8)
    bundle = rekey(res["bundle"], run_id)
    record = {**res["record"], "engine_run_id": res["record"]["run_id"], "run_id": run_id,
              "baseline_config": cfg.get("name"), "target_observation_id": observation_id}
    view = build_view(run_id, bundle, labels, store, storage.settings.previews_dir, observation_id)
    return {"record": record, "bundle": bundle, "view": view}


__all__ = ["compute_run", "rekey", "PROFILES", "BaselineError"]
