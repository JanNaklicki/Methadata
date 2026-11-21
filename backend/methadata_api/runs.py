"""Lokalna kolejka przebiegów w jednym procesie, trwały zapis statusu/wyniku/dowodów per run_id."""
from __future__ import annotations

import logging
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

from methadata.contracts import Bundle
from methadata.ingest.store import now_iso
from methadata.runner import save_run

from .engine import compute_run
from .storage import Storage, atomic_write_json, read_json

log = logging.getLogger("methadata_api.runs")
RUN_ID = re.compile(r"^run-[0-9a-f]{16}$")

FAILED_MSG = "Analiza nie powiodła się. Spróbuj uruchomić ją ponownie; jeśli problem wraca, sprawdź plik wejściowy."
INTERRUPTED_MSG = "Analiza została przerwana (restart usługi). Uruchom ją ponownie."
Engine = Callable[..., dict]


def failed_error(message: str = FAILED_MSG) -> dict:
    return {"code": "run_failed", "message": message, "retryable": True}


class RunManager:
    def __init__(self, storage: Storage, engine: Engine = compute_run):
        self.storage, self.engine = storage, engine
        self.instance = uuid.uuid4().hex
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=storage.settings.workers, thread_name_prefix="methadata-run")

    # ---- ścieżki
    def run_dir(self, run_id: str) -> Path:
        if not RUN_ID.match(run_id):
            raise KeyError(run_id)
        return self.storage.settings.runs_dir / run_id

    def _state_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "state.json"

    # ---- stan
    def state(self, run_id: str) -> Optional[dict]:
        try:
            return read_json(self._state_path(run_id))
        except KeyError:
            return None

    def _write(self, st: dict) -> None:
        st["updated_at"] = now_iso()
        atomic_write_json(self._state_path(st["run_id"]), st)

    def _update(self, run_id: str, **fields: Any) -> bool:
        """Zapis tylko dla przebiegu należącego do tej instancji (po restarcie stare wątki nie nadpisują wyniku)."""
        with self._lock:
            st = self.state(run_id)
            if st is None or st.get("instance") != self.instance:
                return False
            st.update(fields)
            self._write(st)
            return True

    def result(self, run_id: str) -> Optional[dict]:
        return read_json(self.run_dir(run_id) / "result.json")

    def bundle(self, run_id: str) -> Optional[Bundle]:
        p = self.run_dir(run_id) / "bundle.json"
        try:
            return Bundle.model_validate_json(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    # ---- cykl życia
    def recover(self) -> int:
        """Po starcie: przebiegi niezakończone (queued/running) -> failed, z możliwością ponowienia."""
        n = 0
        runs = self.storage.settings.runs_dir
        if not runs.is_dir():
            return 0
        for d in sorted(runs.iterdir()):
            st = read_json(d / "state.json")
            if st and st.get("status") in ("queued", "running"):
                st.update(status="failed", error=failed_error(INTERRUPTED_MSG), instance=self.instance)
                st.pop("progress", None)
                self._write(st)
                n += 1
        return n

    def create(self, observation_id: str, profile: str) -> dict:
        run_id = "run-" + uuid.uuid4().hex[:16]
        now = now_iso()
        st = {"run_id": run_id, "observation_id": observation_id, "profile": profile, "status": "queued",
              "created_at": now, "updated_at": now, "instance": self.instance}
        with self._lock:
            self._write(st)
        self._pool.submit(self._execute, run_id, observation_id, profile)
        return st

    def _execute(self, run_id: str, observation_id: str, profile: str) -> None:
        def progress(stage: str, fraction: float) -> None:
            self._update(run_id, status="running", progress={"stage": stage, "fraction": fraction})

        try:
            if not self._update(run_id, status="running", progress={"stage": "Start analizy", "fraction": 0.0}):
                return
            out = self.engine(self.storage, run_id, observation_id, profile, progress)
            d = self.run_dir(run_id)
            save_run({"record": out["record"], "bundle": out["bundle"]}, d)
            atomic_write_json(d / "result.json", out["view"])
            self._update(run_id, status="succeeded", progress=None)
        except Exception:  # noqa: BLE001 - każdy błąd silnika kończy przebieg jako failed
            log.exception("przebieg %s nie powiódł się", run_id)
            self._update(run_id, status="failed", error=failed_error(), progress=None)

    def shutdown(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=not wait)

    # ---- widok publiczny
    def public(self, st: dict) -> dict:
        out = {k: st[k] for k in ("run_id", "observation_id", "status", "created_at", "updated_at")}
        if st["status"] == "running":
            out["progress"] = st.get("progress") or {"stage": "Analiza"}
        elif st["status"] == "succeeded":
            res = self.result(st["run_id"])
            if res is None:  # wynik utracony: uczciwie jako failed z możliwością ponowienia
                out["status"], out["error"] = "failed", failed_error()
            else:
                out["result"] = res
        elif st["status"] == "failed":
            out["error"] = st.get("error") or failed_error()
        return out
