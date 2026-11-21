"""Lokalizacja rzeczywistych danych (poza gitem) bez ścieżek zaszytych na sztywno.

Kolejność: zmienna METHADATA_BACKUP, potem katalog `BACKUP` w najbliższym katalogu nadrzędnym
(działa w głównym checkoucie i w worktree w `.claude/worktrees/<id>`). Brak danych → ścieżka
nieistniejąca, a testy na danych rzeczywistych mają się pomijać.
"""
from __future__ import annotations

import os
from pathlib import Path

_HERE = Path(__file__).resolve()


def find_backup() -> Path:
    env = os.environ.get("METHADATA_BACKUP")
    if env:
        return Path(env)
    for p in _HERE.parents:
        if (p / "BACKUP").is_dir():
            return p / "BACKUP"
    return _HERE.parents[3] / "BACKUP"


BACKUP = find_backup()
SCAM_LINKS = BACKUP.parent / "scam_links.dec"
