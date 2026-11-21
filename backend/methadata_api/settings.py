"""Konfiguracja backendu ze zmiennych środowiska."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

API_VERSION = "1.0.0"
MAX_UPLOAD_BYTES = 52_428_800  # 50 MiB, zgodnie z x-max-upload-bytes w openapi.yaml
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE_CONFIG = REPO_ROOT / "configs" / "baseline" / "baseline.json"
DEFAULT_JURY_CONFIG = REPO_ROOT / "configs" / "demo" / "jury.json"
PROFILES = ("demo", "jury")
DEMO_DIR = Path(__file__).resolve().parent / "demo_data"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    workers: int = 2
    baseline_config: Path = DEFAULT_BASELINE_CONFIG
    max_upload_bytes: int = MAX_UPLOAD_BYTES
    demo_dir: Path = DEMO_DIR

    default_profile: str = "demo"
    jury_data_dir: Path | None = None
    jury_config: Path = DEFAULT_JURY_CONFIG

    def __post_init__(self) -> None:
        if self.default_profile not in PROFILES:
            raise ValueError(f"Nieznany profil domyślny: {self.default_profile}")
        if self.default_profile == "jury" and self.jury_data_dir is None:
            raise ValueError("Profil jury wymaga METHADATA_JURY_DATA_DIR.")
        if self.jury_data_dir is not None and self.jury_data_dir.resolve() == self.data_dir.resolve():
            raise ValueError("Profil jury wymaga osobnego katalogu danych.")

    @classmethod
    def from_env(cls) -> "Settings":
        jury_dir = os.environ.get("METHADATA_JURY_DATA_DIR")
        return cls(
            data_dir=Path(os.environ.get("METHADATA_DATA_DIR", "var/methadata-data")).expanduser(),
            workers=max(1, int(os.environ.get("METHADATA_WORKERS", "2"))),
            default_profile=os.environ.get("METHADATA_ANALYSIS_PROFILE", "demo"),
            jury_data_dir=Path(jury_dir).expanduser() if jury_dir else None,
            baseline_config=Path(os.environ.get("METHADATA_BASELINE_CONFIG", str(DEFAULT_BASELINE_CONFIG))),
            max_upload_bytes=int(os.environ.get("METHADATA_MAX_UPLOAD_BYTES", str(MAX_UPLOAD_BYTES))),
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
