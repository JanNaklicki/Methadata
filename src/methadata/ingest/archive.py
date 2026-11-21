"""Bezpieczne czytanie wpisów ZIP: walidacja ścieżek, limity zasobów, bez zapisu na dysk poza magazynem."""
from __future__ import annotations

import re
import stat
import unicodedata
import zipfile
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class Limits:
    max_file_bytes: int = 2 << 30          # pojedynczy plik źródłowy
    max_entries: int = 5000                # wpisów w jednym archiwum
    max_member_bytes: int = 256 << 20      # jeden wpis po rozpakowaniu
    max_total_bytes: int = 1 << 30         # suma po rozpakowaniu
    max_ratio: float = 200.0               # rozpakowany/skompresowany dla wpisów > 1 MiB
    max_name_len: int = 1024


class UnsafeEntry(Exception):
    pass


_DRIVE = re.compile(r"^[A-Za-z]:")


def safe_member_name(raw: str, limits: Limits = Limits()) -> str:
    """Zwraca znormalizowaną (NFC, '/') ścieżkę względną albo zgłasza UnsafeEntry."""
    if not raw or "\x00" in raw:
        raise UnsafeEntry("pusta nazwa lub bajt NUL")
    if len(raw) > limits.max_name_len:
        raise UnsafeEntry("zbyt długa nazwa")
    name = raw.replace("\\", "/")
    if name.startswith("/") or _DRIVE.match(name):
        raise UnsafeEntry("ścieżka bezwzględna")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts:
        raise UnsafeEntry("pusta ścieżka")
    if any(p == ".." for p in parts):
        raise UnsafeEntry("segment '..' (path traversal)")
    return unicodedata.normalize("NFC", "/".join(parts))


def iter_members(zf: zipfile.ZipFile, limits: Limits = Limits()) -> Iterator[tuple[str, zipfile.ZipInfo | None, str | None]]:
    """Daje (surowa_nazwa, info, powód_odrzucenia). Katalogi pomijane po cichu. Powód != None => wpis do pominięcia."""
    infos = zf.infolist()
    if len(infos) > limits.max_entries:
        raise UnsafeEntry(f"zbyt wiele wpisów: {len(infos)} > {limits.max_entries}")
    declared = 0
    seen: set[str] = set()
    for info in infos:
        if info.is_dir():
            continue
        try:
            name = safe_member_name(info.filename, limits)
        except UnsafeEntry as e:
            yield info.filename, info, f"unsafe_path: {e}"
            continue
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            yield name, info, "symlink"
        elif info.flag_bits & 0x1:
            yield name, info, "encrypted"
        elif name in seen:
            yield name, info, "duplicate_name"
        elif info.file_size > limits.max_member_bytes:
            yield name, info, f"member_too_large: {info.file_size}"
        elif info.file_size > (1 << 20) and info.compress_size and info.file_size / info.compress_size > limits.max_ratio:
            yield name, info, "compression_ratio"
        else:
            declared += info.file_size
            if declared > limits.max_total_bytes:
                yield name, info, "total_size_limit"
            else:
                seen.add(name)
                yield name, info, None
