"""Read collection ZIPs directly into the content-addressed store."""
from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import PurePosixPath
import re
from urllib.parse import parse_qsl, urlsplit
from zipfile import BadZipFile, ZipFile
from zlib import error as CompressionError

from methadata.contracts import ids
from methadata.contracts.models import Artifact, ArtifactRef, ArtifactRole, InputKind, Observation, ParentRelation
from methadata.ingest.archive import Limits, UnsafeEntry, iter_members
from methadata.ingest.detect import Detected, MEDIA, sniff
from methadata.ingest.importer import FileImporter, JUNK, TAG_ARCHIVE, TAG_LOOSE, role_for
from methadata.ingest.store import LimitExceeded

LIMITS = Limits()
MAX_DEPTH = 3
PAGE_EXTENSIONS = {".html", ".htm", ".xhtml", ".har", ".mhtml", ".mht"}


@dataclass(frozen=True)
class Member:
    name: str
    origin: str
    parent: str | None
    sha: str
    size: int
    detected: Detected


def read_zip(store, sha, size, label, issues, account):
    entries_left = LIMITS.max_entries
    bytes_left = LIMITS.max_total_bytes
    members = []
    outer = Member(label, "ZIP", None, sha, size, Detected("zip", "application/zip", "content"))

    def visit(container, depth):
        nonlocal entries_left, bytes_left
        try:
            with ZipFile(store.blob_path(container.sha)) as archive:
                for name, info, reason in iter_members(archive, LIMITS):
                    if entries_left == 0:
                        account["skipped"] += 1
                        issues.append({"label": container.origin, "reason": "Osiągnięto limit wpisów w drzewie ZIP."})
                        break
                    entries_left -= 1
                    # Escape the separator inside names so nested provenance and IDs remain unambiguous.
                    origin = f"{container.origin}!/{name.replace('%', '%25').replace('!', '%21')}"
                    parts = PurePosixPath(name).parts
                    if any(p.casefold() in JUNK or p.casefold() == "__macosx" or p.startswith("._") for p in parts):
                        account["metadata"] += 1
                        continue
                    if reason or info.file_size > bytes_left:
                        account["skipped"] += 1
                        issues.append({"label": origin, "reason": reason or "Osiągnięto limit rozpakowanego rozmiaru."})
                        continue
                    # Charge declared bytes before reading, including entries that fail CRC.
                    bytes_left -= info.file_size
                    try:
                        with archive.open(info) as source:
                            digest, length, head, _ = store.put_stream(source, max_bytes=info.file_size)
                    except (BadZipFile, CompressionError, EOFError, RuntimeError, NotImplementedError, LimitExceeded) as exc:
                        account["skipped"] += 1
                        issues.append({"label": origin, "reason": f"Nie można odczytać wpisu ({type(exc).__name__})."})
                        continue
                    entry = Member(name, origin, container.origin, digest, length, sniff(head, name, length))
                    members.append(entry)
                    account["loaded"] += 1
                    if entry.detected.kind == "zip":
                        if depth < MAX_DEPTH:
                            visit(entry, depth + 1)
                        else:
                            issues.append({"label": origin, "reason": "Osiągnięto limit zagnieżdżenia ZIP."})
        except (BadZipFile, UnsafeEntry) as exc:
            issues.append({"label": container.origin, "reason": f"Nie można rozpakować ZIP ({exc})."})

    visit(outer, 0)
    return outer, members


def archive_groups(outer, members):
    """Group pages and their resources at each archive level."""
    children = defaultdict(list)
    for entry in members:
        children[entry.parent].append(entry)

    def level(container):
        roots = set()
        for entry in children[container.origin]:
            path = PurePosixPath(entry.name)
            parent = path.parts[:-1]
            if any(part.casefold().endswith("_files") for part in parent):
                continue
            if path.suffix.lower() in PAGE_EXTENSIONS or path.name.casefold() == "archive_info.json":
                roots.add(parent[:-1] if parent and parent[-1].casefold() == "html" else parent)
        capture_roots = sorted(root for root in roots if not any(root[:i] in roots for i in range(len(root))))
        groups = defaultdict(list)
        separate = []
        for entry in children[container.origin]:
            parts = PurePosixPath(entry.name).parts
            root = next((r for r in capture_roots if parts[:-1][:len(r)] == r), None)
            nested = level(entry) if children.get(entry.origin) else []
            if root is None and nested:
                for name, items, capture in nested:
                    separate.append((name, [(entry, entry.name), *items], capture))
                continue
            key = ("capture", root) if root is not None else ("file", parts)
            relative = "/".join(parts[len(root):]) if root is not None else parts[-1]
            groups[key].append((entry, relative))
            # A nested ZIP found among a page's assets supplies evidence for that page.
            for _, items, _ in nested:
                groups[key].extend(items)
        for (kind, parts), items in sorted(groups.items()):
            name = parts[-1] if parts else container.name
            if len(groups) == 1 and not separate and kind == "file":
                name = container.name
            unique = {entry.origin: (entry, relative) for entry, relative in items}
            separate.append((name, list(unique.values()), kind == "capture"))
        return separate

    return level(outer)


def build_observation(store, outer, entries, is_capture):
    source_ref = "zip:" + ids.sha256_bytes(
        "\n".join([outer.sha, *(f"{entry.origin}={entry.sha}" for entry, _ in entries)]).encode()
    )
    oid = ids.observation_id(source_ref, InputKind.full_capture.value)
    artifacts = []
    parents = {}
    has_html = any(entry.detected.kind == "html" for entry, _ in entries)
    for entry, relative in [(outer, outer.name), *entries]:
        relative_parts = PurePosixPath(relative).parts
        kind = TAG_ARCHIVE if is_capture or entry.parent != outer.origin else TAG_LOOSE
        if len(relative_parts) > 1 and relative_parts[-2].casefold() in {"html", "screenshots"}:
            relative = "/".join((relative_parts[-2].casefold(), relative_parts[-1]))
        elif is_capture and len(relative_parts) == 1 and entry.detected.kind == "png":
            relative = "screenshots/" + relative
        role = role_for(relative, entry.detected, kind)
        if entry.detected.kind == "mhtml" and not has_html:
            role = ArtifactRole.landing_html
        parent_id = parents.get(entry.parent)
        artifact = Artifact(
            artifact_id=ids.artifact_id(oid, role.value, entry.sha, entry.origin),
            observation_id=oid, role=role, local_ref=store.blob_ref(entry.sha),
            sha256=entry.sha, size=entry.size, media_type=entry.detected.media_type, origin=entry.origin,
            parent=ArtifactRef(artifact_id=parent_id) if parent_id else None,
            parent_relation=ParentRelation.archive_member if parent_id else None,
        )
        parents[entry.origin] = artifact.artifact_id
        artifacts.append(artifact)
    tags, capture_time = FileImporter.archive_metadata(store, artifacts)
    return Observation(
        observation_id=oid, canonical_id=ids.canonical_id([], url=source_ref),
        source_ref=source_ref, input_kind=InputKind.full_capture, artifacts=artifacts,
        capture_time=capture_time, tags=[TAG_ARCHIVE, "zip_archive", *tags],
    )


def build_loose_observation(store, filename, kind, sha, size):
    detected = Detected(kind, MEDIA[kind], "content")
    role = ArtifactRole.landing_html if kind == "mhtml" else role_for(filename, detected, TAG_LOOSE)
    input_kind = (InputKind.creative_only if role in
                  {ArtifactRole.creative_image, ArtifactRole.audio, ArtifactRole.video} else InputKind.full_capture)
    source = "collection-file:" + ids.sha256_bytes(f"{sha}:{filename}".encode())
    oid = ids.observation_id(source, input_kind.value)
    artifact = Artifact(
        artifact_id=ids.artifact_id(oid, role.value, sha, filename), observation_id=oid,
        role=role, local_ref=store.blob_ref(sha), sha256=sha, size=size,
        media_type=detected.media_type, origin=filename,
    )
    return Observation(
        observation_id=oid, canonical_id=ids.canonical_id([(role.value, sha)], url=sha),
        source_ref=source, input_kind=input_kind, artifacts=[artifact], tags=[TAG_LOOSE],
    )


_HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$", re.IGNORECASE)


def normalize_import_url(raw: str) -> tuple[str, str]:
    """Normalizuje adres URL lub samą domenę -> (url, etykieta_domeny). Rzuca ValueError dla błędnego wejścia."""
    text = raw.strip()
    if not text or any(ch.isspace() for ch in text) or len(text) > 16384:
        raise ValueError("Podaj poprawny adres URL lub domenę (maks. 16 384 znaków).")
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("Obsługiwane są wyłącznie adresy http(s) oraz nazwy domen.")
    host = parts.hostname.lower().rstrip(".")
    if not _HOST_RE.match(host):
        raise ValueError(f"Niepoprawna nazwa domeny: {host}")
    path = parts.path or "/"
    normalized = f"{parts.scheme.lower()}://{parts.netloc}{path}" + (f"?{parts.query}" if parts.query else "")
    label = host if path in {"", "/"} and not parts.query else f"{host}{path[:60]}"
    return normalized, label


_NESTED_URL_KEYS = {"url", "u", "redirect", "dest", "destination", "target", "goto", "next", "clickurl"}


def _extract_nested_urls(url: str) -> list[str]:
    """Wyciąga zagnieżdżone docelowe adresy URL z parametrów trackerów reklamowych (np. &URL=https://...)."""
    out: list[str] = []
    seen = {url}
    for k, v in parse_qsl(urlsplit(url).query, keep_blank_values=False):
        if k.lower() not in _NESTED_URL_KEYS:
            continue
        cand = v.strip()
        if cand.startswith(("http://", "https://")) and cand not in seen:
            try:
                norm, _ = normalize_import_url(cand)
            except ValueError:
                continue
            seen.add(norm)
            out.append(norm)
    return out


def build_url_observation(store, url: str, fetch: bool = True) -> tuple[Observation, str]:
    """Pobiera treść i przekierowania spod URL (lub zapisuje sam adres URL z minimalnym HAR)."""
    from methadata.ingest.store import now_iso
    from methadata.intake.fetch import FetchPolicy, Fetcher

    started = now_iso()
    res = Fetcher(FetchPolicy(timeout_s=10.0, max_bytes=10 * 1024 * 1024, max_redirects=5)).fetch(url) if fetch else None

    final_url = res.final_url if res else url
    captured_at = (res.started_at if res and res.started_at else started)
    has_body = bool(res and res.fetched and res.body)
    input_kind = InputKind.full_capture
    source = "collection-url:" + ids.sha256_bytes(url.encode())
    oid = ids.observation_id(source, input_kind.value)
    artifacts: list[Artifact] = []

    url_sha = ids.sha256_bytes(final_url.encode("utf-8"))
    url_art = Artifact(
        artifact_id=ids.artifact_id(oid, ArtifactRole.input_url.value, url_sha, final_url),
        observation_id=oid,
        role=ArtifactRole.input_url,
        sha256=url_sha,
        url=final_url,
        origin=url,
    )
    artifacts.append(url_art)

    # Syntetyzuj lekki HAR z przekierowaniami i domeną docelową, aby url-har-extractor mógł indeksować domeny.
    entries = []
    if res and res.redirects:
        for hop in res.redirects:
            entries.append({
                "startedDateTime": captured_at,
                "request": {"method": "GET", "url": hop.url, "headers": []},
                "response": {
                    "status": hop.status,
                    "headers": [{"name": "Location", "value": hop.location}] if hop.location else [],
                    "content": {"size": 0, "mimeType": ""},
                },
            })
    entries.append({
        "startedDateTime": captured_at,
        "request": {"method": "GET", "url": final_url, "headers": []},
        "response": {
            "status": (res.http_status if res and res.http_status else 0),
            "headers": [],
            "content": {
                "size": len(res.body) if has_body else 0,
                "mimeType": (res.content_type or "") if res else "",
            },
        },
    })
    for nested_url in _extract_nested_urls(url):
        if nested_url != final_url:
            entries.append({
                "startedDateTime": captured_at,
                "request": {"method": "GET", "url": nested_url, "headers": []},
                "response": {"status": 200, "headers": [], "content": {"size": 0, "mimeType": "text/html"}},
            })
    har_bytes = json.dumps({"log": {"version": "1.2", "entries": entries}}, ensure_ascii=False).encode("utf-8")
    har_sha, har_size, _ = store.put_bytes(har_bytes)
    artifacts.append(Artifact(
        artifact_id=ids.artifact_id(oid, ArtifactRole.har.value, har_sha, "fetch.har"),
        observation_id=oid,
        role=ArtifactRole.har,
        local_ref=store.blob_ref(har_sha),
        sha256=har_sha,
        size=har_size,
        media_type="application/json",
        origin="fetch.har",
    ))

    note = "Zapisano sam adres URL."
    if has_body and res is not None and res.body is not None:
        body = res.body
        body_sha, body_size, _ = store.put_bytes(body)
        ctype = (res.content_type or "").split(";", 1)[0].strip().lower()
        detected = sniff(body, "index.html" if "html" in ctype or not ctype else "resource")
        if "html" in ctype or detected.kind == "html":
            role, media_type, fname = ArtifactRole.landing_html, "text/html", "landing.html"
        elif detected.kind in {"png", "jpeg", "webp", "gif"}:
            role, media_type, fname = ArtifactRole.creative_image, detected.media_type, f"image.{detected.kind}"
        elif detected.kind in {"mp4", "webm"}:
            role, media_type, fname = ArtifactRole.video, detected.media_type, f"video.{detected.kind}"
        elif detected.kind in {"mp3", "wav", "ogg"}:
            role, media_type, fname = ArtifactRole.audio, detected.media_type, f"audio.{detected.kind}"
        else:
            role, media_type, fname = ArtifactRole.landing_html, ctype or "text/html", "landing.html"
        artifacts.append(Artifact(
            artifact_id=ids.artifact_id(oid, role.value, body_sha, fname),
            observation_id=oid,
            role=role,
            local_ref=store.blob_ref(body_sha),
            sha256=body_sha,
            size=body_size,
            media_type=media_type,
            origin=final_url,
        ))
        hops = len(res.redirects)
        hop_txt = f", {hops} przekier." if hops else ""
        note = f"Pobrano stronę (HTTP {res.http_status or 200}{hop_txt})."
    elif fetch and res is not None:
        reason = res.error or res.fetch_status
        note = f"Zapisano sam adres URL (nie udało się pobrać treści: {reason})."

    identity_pairs = [(a.role.value, a.sha256) for a in artifacts if a.sha256]
    obs = Observation(
        observation_id=oid,
        canonical_id=ids.canonical_id(identity_pairs, url=final_url),
        source_ref=source,
        input_kind=input_kind,
        artifacts=artifacts,
        capture_time=captured_at,
        tags=[TAG_LOOSE, "url_import"],
    )
    return obs, note
