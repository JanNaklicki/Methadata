"""Trwały stan monitora (kursor, kolejka analizy) zapisywany atomowo po skutecznym zapisie rekordów."""
from __future__ import annotations

import fcntl
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

from ..ingest.store import _atomic_write


class LockedError(Exception):
    pass


@dataclass
class MonitorState:
    cursor: Optional[str] = None
    pending: list[str] = field(default_factory=list)   # observation_id zapisane, ale jeszcze nieprzeanalizowane
    totals: dict = field(default_factory=lambda: {"new": 0, "updated": 0, "duplicate": 0, "rejected": 0})
    last_run: Optional[str] = None
    last_error: Optional[str] = None
    schema: int = 1

    def to_dict(self) -> dict:
        return {"schema": self.schema, "cursor": self.cursor, "pending": sorted(set(self.pending)),
                "totals": self.totals, "last_run": self.last_run, "last_error": self.last_error}


class StateFile:
    def __init__(self, store_root: str | Path, name: str):
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", name) or "source"
        self.dir = Path(store_root) / "monitor"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{safe}.state.json"
        self.log_path = self.dir / f"{safe}.runs.jsonl"
        self.lock_path = self.dir / f"{safe}.lock"

    def load(self) -> MonitorState:
        if not self.path.is_file():
            return MonitorState()
        d = json.loads(self.path.read_text(encoding="utf-8"))
        st = MonitorState(cursor=d.get("cursor"), pending=list(d.get("pending", [])), last_run=d.get("last_run"),
                          last_error=d.get("last_error"))
        st.totals.update(d.get("totals", {}))
        return st

    def save(self, st: MonitorState) -> None:
        _atomic_write(self.path, (json.dumps(st.to_dict(), ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode())

    def log(self, entry: dict) -> None:
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")

    @contextmanager
    def lock(self) -> Iterator[None]:
        """Jeden przebieg naraz (cron/launchd mogą nałożyć uruchomienia)."""
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise LockedError("inny przebieg monitora trwa")
            yield
        finally:
            os.close(fd)
