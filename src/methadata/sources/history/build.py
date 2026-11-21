"""Budowa wersji: z bajtów HTML, z magazynu F02 (kolejne zgłoszenia/wersje obserwacji) i z kopii archiwalnych."""
from __future__ import annotations

import hashlib
from typing import Optional, Sequence

from methadata.contracts.models import ArtifactRole

from .analyze import analyze_html, normalize_url
from .archive import ArchiveClient
from .model import PageVersion, parse_time
from .timeline import History


def make_version(url: str, body: bytes, captured_at: str, *, known_at: Optional[str] = None, source: str = "report",
                 observation_id: Optional[str] = None, version: Optional[int] = None,
                 contact_extractor=None, published_at: Optional[str] = None) -> Optional[PageVersion]:
    """None, gdy czas pobrania nieczytelny (bez czasu nie da się umieścić wersji na osi)."""
    cap = parse_time(captured_at)
    if cap is None:
        return None
    nurl = normalize_url(url)
    snap = analyze_html(body.decode("utf-8", errors="replace"), nurl, contact_extractor=contact_extractor)
    pub = parse_time(published_at or snap.get("published_at"))
    return PageVersion(url=nurl, captured_at=cap, known_at=parse_time(known_at) or cap, source=source,
                       sha256=hashlib.sha256(body).hexdigest(), snapshot=snap, published_at=pub,
                       observation_id=observation_id, version=version)


def add_archive(history: History, client: ArchiveClient, url: str, *, known_at: Optional[str] = None,
                contact_extractor=None) -> dict:
    """Dodaje kopie archiwalne URL-a; known_at = moment pobrania z archiwum (domyślnie czas kopii)."""
    stats = {"new": 0, "late": 0, "duplicate": 0, "skipped": 0}
    for s in client.snapshots(url):
        v = make_version(url, s.body, s.timestamp, known_at=known_at, source=s.source, contact_extractor=contact_extractor)
        stats["skipped" if v is None else history.add(v)] += 1
    return stats


def from_store(store, *, urls: Optional[Sequence[str]] = None, contact_extractor=None) -> History:
    """Każda zapisana wersja każdej obserwacji z URL-em (source_ref 'url:...') to osobna wersja strony:
    captured_at = capture_time obserwacji, known_at = imported_at wersji w magazynie. Treść z blobów po SHA-256."""
    from methadata.contracts.models import Observation
    want = {normalize_url(u) for u in urls} if urls else None
    h = History()
    for oid in store.observation_ids():
        for rec in store.versions(oid):
            obs = Observation.model_validate(rec["observation"])
            if not obs.source_ref.startswith(("url:", "http")):
                continue
            url = normalize_url(obs.source_ref)
            if want is not None and url not in want:
                continue
            art = next((a for a in obs.artifacts if a.role in (ArtifactRole.landing_html, ArtifactRole.landing_dom)), None)
            if art is None or not art.local_ref or not store.has_blob(art.sha256):
                continue
            v = make_version(url, store.blob_path(art.sha256).read_bytes(), obs.capture_time or "", known_at=rec.get("imported_at"),
                             source="intake", observation_id=oid, version=rec["version"], contact_extractor=contact_extractor)
            if v is not None:
                h.add(v)
    return h
