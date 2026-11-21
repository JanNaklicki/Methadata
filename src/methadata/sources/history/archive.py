"""Publiczne kopie archiwalne: interfejs, backend z zapisanych próbek (testy, bez sieci) i opcjonalny klient Wayback."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Protocol, Sequence
from urllib.parse import quote


@dataclass(frozen=True)
class ArchiveSnapshot:
    url: str
    timestamp: str          # ISO lub YYYYMMDDhhmmss
    body: bytes             # treść (niezaufana; nigdy nie wykonywana)
    source: str = "archive:recorded"
    access_url: Optional[str] = None


class ArchiveClient(Protocol):
    def snapshots(self, url: str) -> Sequence[ArchiveSnapshot]: ...


class RecordedArchive:
    """Zapisane próbki: {url: [{"timestamp":..., "html":..., "access_url":...}]}. Brak URL-a -> pusta lista."""

    def __init__(self, samples: Mapping[str, Sequence[Mapping]], source: str = "archive:recorded"):
        self._s, self._src = samples, source

    @classmethod
    def from_file(cls, path) -> "RecordedArchive":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def snapshots(self, url: str) -> Sequence[ArchiveSnapshot]:
        return [ArchiveSnapshot(url=url, timestamp=r["timestamp"], body=r["html"].encode("utf-8"), source=self._src,
                                access_url=r.get("access_url")) for r in self._s.get(url, [])]


def wayback_cdx_url(url: str, limit: int = 20) -> str:
    return ("https://web.archive.org/cdx/search/cdx?output=json&fl=timestamp,original&filter=statuscode:200"
            f"&collapse=digest&limit={int(limit)}&url={quote(url, safe='')}")


def wayback_access_url(url: str, timestamp: str) -> str:
    return f"https://web.archive.org/web/{timestamp}id_/{url}"  # id_ = oryginalne bajty, bez przepisywania


class WaybackArchive:
    """Opcjonalny backend sieciowy (Internet Archive). Nie jest używany w testach; błąd sieci -> pusta lista."""

    def __init__(self, fetch=None, limit: int = 20, timeout: float = 15.0):
        self._fetch, self.limit, self.timeout = fetch, limit, timeout

    def _get(self, u: str) -> bytes:
        if self._fetch:
            return self._fetch(u)
        import urllib.request  # leniwie
        req = urllib.request.Request(u, headers={"User-Agent": "methadata-history/1.0"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310
            return r.read(5_000_000)

    def snapshots(self, url: str) -> Sequence[ArchiveSnapshot]:
        try:
            rows = json.loads(self._get(wayback_cdx_url(url, self.limit)) or b"[]")[1:]
            out = []
            for ts, orig in rows:
                acc = wayback_access_url(orig, ts)
                out.append(ArchiveSnapshot(url=url, timestamp=ts, body=self._get(acc), source="archive:wayback", access_url=acc))
            return out
        except Exception:  # noqa: BLE001
            return []
