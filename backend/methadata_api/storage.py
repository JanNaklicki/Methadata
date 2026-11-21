"""Katalog danych backendu: magazyn F02 (Store), metadane (etykiety, indeks), seedowanie zestawu demo."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

from methadata.contracts import ids
from methadata.ingest import FileImporter, Store

from .errors import safe_text
from .settings import Settings


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, sort_keys=True, indent=1)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


class Storage:
    """Jedyny punkt dostępu do danych. `lock` serializuje zapisy do magazynu i metadanych oraz migawki dla przebiegów."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.lock = threading.RLock()
        self.jury = (
            Storage(replace(settings, data_dir=settings.jury_data_dir, jury_data_dir=None, default_profile="demo"))
            if settings.jury_data_dir is not None else None
        )

    def for_profile(self, profile: str) -> Storage | None:
        if profile == "demo":
            return self
        if profile == "jury":
            return self.jury
        return None

    # ---- dostępność
    def available(self) -> bool:
        d = self.settings.data_dir
        return d.is_dir() and os.access(d, os.W_OK | os.X_OK)

    def prepare(self) -> None:
        for d in (self.settings.store_dir, self.settings.runs_dir, self.settings.previews_dir, self.settings.meta_dir):
            d.mkdir(parents=True, exist_ok=True)

    def store(self) -> Store:
        return Store(self.settings.store_dir)

    # ---- etykiety
    def _labels_path(self) -> Path:
        return self.settings.meta_dir / "labels.json"

    def labels(self) -> dict[str, str]:
        return read_json(self._labels_path(), {}) or {}

    def set_label(self, observation_id: str, label: str) -> None:
        with self.lock:
            cur = self.labels()
            cur[observation_id] = safe_text(label, 200)
            atomic_write_json(self._labels_path(), cur)

    # ---- indeks (materiały, z którymi porównywana jest analizowana obserwacja)
    def _index_path(self) -> Path:
        return self.settings.meta_dir / "index.json"

    def index(self) -> Optional[dict]:
        return read_json(self._index_path())

    # ---- seed zestawu demo (idempotentny)
    def seed_demo(self) -> dict:
        demo = self.settings.demo_dir
        manifest = json.loads((demo / "manifest.json").read_text(encoding="utf-8"))
        h = hashlib.sha256()
        for p in sorted(x for x in demo.rglob("*") if x.is_file() and x.name != "generate_demo.py"):
            h.update(p.relative_to(demo).as_posix().encode())
            h.update(p.read_bytes())
        digest = h.hexdigest()[:16]
        with self.lock:
            self.prepare()
            cur = self.index()
            store = self.store()
            if cur and cur.get("seed_digest") == digest and all(store.versions(i) for i in
                                                                  cur["observation_ids"] + [cur["target_observation_id"]]):
                return cur
            labels = self.labels()
            rep = FileImporter().import_sources([demo / e["dir"] for e in manifest["index"]] + [demo / manifest["target"]["dir"]],
                                                store)
            if rep.errors:
                raise RuntimeError("seed demo: " + "; ".join(i.message for i in rep.errors))
            oids = {e.source_ref: e.observation_id for e in rep.entries}
            idx_ids = [oids["dir:" + e["dir"]] for e in manifest["index"]]
            target = oids["dir:" + manifest["target"]["dir"]]
            for e in [*manifest["index"], manifest["target"]]:
                labels[oids["dir:" + e["dir"]]] = safe_text(e["label"], 200)
            atomic_write_json(self._labels_path(), labels)
            info = {"seed_digest": digest, "observation_ids": idx_ids, "target_observation_id": target,
                    "dataset_name": manifest["dataset_name"], "dataset_version": manifest["dataset_version"],
                    "description": manifest["description"]}
            atomic_write_json(self._index_path(), info)
            return info
