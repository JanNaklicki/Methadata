"""Przyjęcie zgłoszeń: plik (zdjęcie, HTML, nagranie, ZIP...), URL jako tekst (offline) i jawne pobranie URL."""
from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..contracts import ids
from ..contracts.models import Artifact, ArtifactRole, InputKind, Observation
from ..ingest import FileImporter, ImportReport, Store
from ..ingest.detect import AUDIO_KINDS, IMAGE_KINDS, VIDEO_KINDS, sniff
from ..ingest.importer import TAG_URL, normalize_url, parse_url_list
from ..ingest.manifest import write_manifest
from ..ingest.store import now_iso
from .fetch import FetchPolicy, Fetcher, FetchResult
from .urlcheck import analyze_url

_SAFE = re.compile(r"[^A-Za-z0-9._\-]")


def _store(s) -> Store:
    return s if isinstance(s, Store) else Store(s)


# ---------------------------------------------------------------- pliki i tekst (offline)
def submit_files(paths, store) -> ImportReport:
    """Zdjęcie, HTML, nagranie, katalog lub ZIP z dysku -> obserwacje (bez sieci)."""
    return FileImporter().import_sources([str(p) for p in paths], _store(store))


def submit_bytes(data: bytes, filename: str, store) -> ImportReport:
    """Przesłany plik jako bajty (np. upload). Nazwa służy tylko jako wskazówka rozszerzenia i source_ref."""
    name = _SAFE.sub("_", Path(filename).name) or "upload"
    with tempfile.TemporaryDirectory(prefix="intake-") as d:
        (Path(d) / name).write_bytes(data)
        return submit_files([Path(d) / name], store)


def classify(data: bytes, filename: str = "") -> str:
    """image | video | audio | html | archive | document | unknown (z zawartości, nie z nazwy katalogu)."""
    k = sniff(data[:65536], filename, len(data)).kind
    if k in IMAGE_KINDS:
        return "image"
    if k in VIDEO_KINDS:
        return "video"
    if k in AUDIO_KINDS:
        return "audio"
    if k in ("html", "mhtml"):
        return "html"
    if k == "zip":
        return "archive"
    return "document" if k in ("pdf", "json", "text", "har") else "unknown"


def submit_text(text: str, store) -> tuple[ImportReport, list[dict]]:
    """Tekst z jednym lub wieloma URL (lista z komentarzami/nagłówkiem) -> obserwacje url_only, bez pobierania."""
    parsed = parse_url_list(text)
    urls = parsed[0] if parsed else []
    rep = FileImporter().import_sources(urls, _store(store)) if urls else ImportReport(import_id="none")
    rep.ignored_lines += parsed[1] if parsed else 0
    return rep, [analyze_url(u) for u in urls]


def submit_url(url: str, store) -> ImportReport:
    return FileImporter().import_sources([url.strip()], _store(store))


# ---------------------------------------------------------------- pobranie URL
@dataclass
class FetchOutcome:
    observation_id: str
    fetch: FetchResult           # status faktycznego pobrania z sieci
    store_status: str            # new | unchanged | new_version | kept_previous | url_only
    version: Optional[int]
    replay: Optional[dict] = None  # test odtwarzania z magazynu, osobno od pobrania

    def summary(self) -> dict:
        f = self.fetch
        return {"observation_id": self.observation_id, "fetch_status": f.fetch_status, "http_status": f.http_status,
                "error": f.error, "final_url": f.final_url, "redirects": [h.to_dict() for h in f.redirects],
                "bytes": None if f.body is None else len(f.body), "duration_s": f.duration_s,
                "store_status": self.store_status, "version": self.version, "replay": self.replay}


def _role_for_body(body: bytes, ctype: Optional[str]) -> tuple[ArtifactRole, str]:
    det = sniff(body[:65536], "")
    if det.kind in ("html", "mhtml"):
        return ArtifactRole.landing_html, det.media_type
    if det.kind in IMAGE_KINDS:
        return ArtifactRole.creative_image, det.media_type
    if det.kind in VIDEO_KINDS:
        return ArtifactRole.video, det.media_type
    if det.kind in AUDIO_KINDS:
        return ArtifactRole.audio, det.media_type
    mt = (ctype or det.media_type).split(";")[0].strip() or "application/octet-stream"
    return (ArtifactRole.document if det.kind in ("pdf", "json", "text") else ArtifactRole.other), mt


def fetch_url(url: str, store, *, policy: FetchPolicy = FetchPolicy(), fetcher: Optional[Fetcher] = None) -> FetchOutcome:
    """Jawne pobranie. Obserwacja (ten sam ID co url_only z importera) zawsze istnieje po wywołaniu;
    błąd pobrania nie kasuje wcześniej zapisanej treści, a próba trafia do fetches.jsonl."""
    store = _store(store)
    url = url.strip()
    norm = normalize_url(url)
    oid = ids.observation_id("url:" + norm, InputKind.url_only.value)
    res = (fetcher or Fetcher(policy)).fetch(url)
    prev = store.versions(oid)
    prev_obs = Observation.model_validate(prev[-1]["observation"]) if prev else None
    status, ver = "kept_previous", prev[-1]["version"] if prev else None
    if res.fetched:
        obs = _build_observation(store, url, norm, oid, res, prev_obs)
        status, ver = store.save_observation(obs, "fetch-" + ids.sha256_bytes(res.started_at.encode())[:8])
    elif prev_obs is None:
        rep = submit_url(url, store)  # użyteczna obserwacja url_only mimo błędu
        status, ver = "url_only", rep.entries[0].version if rep.entries else None
    out = FetchOutcome(oid, res, status, ver)
    if res.fetched:
        out.replay = replay_check(store, oid)
    with (store.root / "fetches.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": res.started_at, "observation_id": oid, "requested_url": url,
                            **out.summary()}, ensure_ascii=False, sort_keys=True) + "\n")
    write_manifest(store)
    return out


def _build_observation(store: Store, url: str, norm: str, oid: str, res: FetchResult,
                       prev: Optional[Observation]) -> Observation:
    body = res.body or b""
    sha, size, _ = store.put_bytes(body)
    role, media = _role_for_body(body, res.content_type)
    usha = ids.sha256_bytes(url.encode("utf-8"))
    record = {"requested_url": url, "final_url": res.final_url, "http_status": res.http_status,
              "redirects": [h.to_dict() for h in res.redirects], "content_type": res.content_type,
              "body_sha256": sha, "body_size": size, "fetch_status": res.fetch_status}
    rbytes = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8")
    rsha, rsize, _ = store.put_bytes(rbytes)
    arts = [
        Artifact(artifact_id=ids.artifact_id(oid, "input_url", usha, "url"), observation_id=oid,
                 role=ArtifactRole.input_url, sha256=usha, size=len(url.encode()), media_type="text/uri-list",
                 url=url, origin="url"),
        Artifact(artifact_id=ids.artifact_id(oid, role.value, sha, "fetch-body"), observation_id=oid, role=role,
                 local_ref=store.blob_ref(sha), sha256=sha, size=size, media_type=media, url=res.final_url,
                 origin="fetch-body"),
        Artifact(artifact_id=ids.artifact_id(oid, "document", rsha, "fetch-record"), observation_id=oid,
                 role=ArtifactRole.document, local_ref=store.blob_ref(rsha), sha256=rsha, size=rsize,
                 media_type="application/json", origin="fetch-record"),
    ]
    arts.sort(key=lambda a: a.artifact_id)
    tags = ["url_fetch", f"http_{res.http_status}"]
    if res.http_status and res.http_status >= 400:
        tags.append("excluded_from_corpus")
    ctime = None
    if prev and {a.artifact_id for a in prev.artifacts} == {a.artifact_id for a in arts}:
        ctime = prev.capture_time  # ponowienie tej samej treści nie tworzy nowej wersji
    ctime = ctime or res.started_at
    pairs = [(a.role.value, a.sha256) for a in arts]
    try:
        cid = ids.canonical_id(pairs)
    except ValueError:
        cid = ids.canonical_id([], url=norm)
    return Observation(observation_id=oid, canonical_id=cid, source_ref="url:" + norm,
                       input_kind=InputKind.full_capture, artifacts=arts, capture_time=ctime, tags=sorted(tags))


def replay_check(store, observation_id: str) -> dict:
    """Test odtwarzania offline: zapisane bajty zgadzają się z sumą i rekordem pobrania. Nie dotyka sieci
    i nie mówi nic o tym, czy pobranie z sieci się udało (to jest FetchResult.fetch_status)."""
    store = _store(store)
    try:
        obs = store.observation(observation_id)
    except KeyError:
        return {"replay_ok": False, "reason": "no_observation"}
    by = {a.origin: a for a in obs.artifacts}
    rec, body = by.get("fetch-record"), by.get("fetch-body")
    if not rec or not body:
        return {"replay_ok": False, "reason": "no_fetch_capture"}
    try:
        data = store.open_bytes(body.artifact_id)
        meta = json.loads(store.open_bytes(rec.artifact_id))
    except (OSError, ValueError) as e:
        return {"replay_ok": False, "reason": f"unreadable: {e}"}
    ok = ids.sha256_bytes(data) == body.sha256 == meta.get("body_sha256") and len(data) == meta.get("body_size")
    return {"replay_ok": bool(ok), "reason": None if ok else "hash_mismatch", "body_sha256": body.sha256}
