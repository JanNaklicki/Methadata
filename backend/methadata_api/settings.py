"""Konfiguracja backendu ze zmiennych środowiska."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

API_VERSION = "1.0.0"
MAX_UPLOAD_BYTES = 52_428_800  # 50 MiB, zgodnie z x-max-upload-bytes w openapi.yaml
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE_CONFIG = REPO_ROOT / "configs" / "baseline" / "baseline.json"
DEMO_DIR = Path(__file__).resolve().parent / "demo_data"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    workers: int = 2
    baseline_config: Path = DEFAULT_BASELINE_CONFIG
    max_upload_bytes: int = MAX_UPLOAD_BYTES
    demo_dir: Path = DEMO_DIR

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            data_dir=Path(os.environ.get("METHADATA_DATA_DIR", "var/methadata-data")).expanduser(),
            workers=max(1, int(os.environ.get("METHADATA_WORKERS", "2"))),
            baseline_config=Path(os.environ.get("METHADATA_BASELINE_CONFIG", str(DEFAULT_BASELINE_CONFIG))),
        )

    # układ katalogu danych
    @property
    def store_dir(self) -> Path: return self.data_dir / "store"
    @property
    def runs_dir(self) -> Path: return self.data_dir / "runs"
    @property
    def previews_dir(self) -> Path: return self.data_dir / "previews"
    @property
    def meta_dir(self) -> Path: return self.data_dir / "meta"
