"""Skan źródła reklam (bonus): jednorazowy przebieg `methadata.monitor` uruchamiany z interfejsu.

Źródłem jest eksport JSON/JSONL (bez sieci): METHADATA_MONITOR_FEED albo syntetyczny przykład z repozytorium.
Rekordy trafiają do osobnego magazynu (`<data>/monitor`), więc nie mieszają się z kolekcją ani z indeksem jury.
Dostęp do Meta Ad Library API (token) pozostaje w CLI: `python -m methadata.monitor run --meta-search ...`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock
from typing import Any

from methadata.ingest import Store
from methadata.monitor import JsonFileProvider, StateFile, payer_analyzer, run_once

from .errors import safe_text
from .settings import REPO_ROOT, Settings

NAME = "ui-scan"
SAMPLE_FEED = REPO_ROOT / "configs" / "monitor" / "sample_feed.jsonl"
CONFIG = REPO_ROOT / "configs" / "monitor" / "monitor.json"


class SourceMonitor:
    def __init__(self, settings: Settings):
        self.root = settings.data_dir / "monitor" / "store"
        self._lock = Lock()

    @property
    def feed(self) -> Path:
        env = os.environ.get("METHADATA_MONITOR_FEED", "").strip()
        return Path(env).expanduser() if env else SAMPLE_FEED

    def _describe(self) -> dict[str, Any]:
        feed = self.feed
        return {"kind": "json-export", "name": safe_text(feed.name, 120), "available": feed.exists(),
                "synthetic": feed == SAMPLE_FEED}

    def status(self) -> dict[str, Any]:
        state = StateFile(self.root, NAME).load().to_dict() if (self.root / "monitor").exists() else {}
        return {"source": self._describe(), "state": state}

    def scan(self) -> dict[str, Any]:
        source = self._describe()
        if not source["available"]:
            return {"ok": False, "source": source, "error": "Nie znaleziono pliku źródła (METHADATA_MONITOR_FEED)."}
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "source": source, "error": "Skan już trwa."}
        try:
            config = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.is_file() else {}
            report = run_once(
                JsonFileProvider(self.feed, source_id=NAME), Store(self.root), name=NAME,
                analyzer=payer_analyzer(config.get("run")), page_size=config.get("page_size", 100),
                max_pages=config.get("max_pages", 10), retries=0, backoff_s=0.0)
        finally:
            self._lock.release()
        summary = report.summary()
        analysis = summary.get("analysis") or {}
        return {
            "ok": report.ok, "source": source, "error": safe_text(summary["error"], 300) if summary["error"] else None,
            "new": summary["new"], "updated": summary["updated"], "duplicate": summary["duplicate"],
            "rejected": summary["rejected"], "exhausted": summary["exhausted"],
            "payer_groups_touched": len(analysis.get("groups_touched") or []),
            "state": StateFile(self.root, NAME).load().to_dict(),
        }
