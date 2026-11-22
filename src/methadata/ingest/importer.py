"""Importer katalogów, plików, ZIP i list URL -> Observation + Artifact w magazynie (F02).

Podział na obserwacje (jednostki):
  * katalog z HTML (lub HAR) -> `page_capture` (pliki bezpośrednie + podkatalogi zasobów),
  * katalog-archiwum (archive_info.json) i plik ZIP -> osobne `archive_capture` (te same bajty co kopia stron,
    ale inne źródło; ZIP i jego rozpakowana kopia mają ten sam canonical_id),
  * pojedynczy plik poza zapisem strony -> `loose_file`,
  * URL -> `url_observation` (bez pobierania).
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Optional, Sequence
from urllib.parse import urlsplit, urlunsplit

from ..contracts import ids
from ..contracts.interfaces import AdapterKind, AdapterSpec
from ..contracts.models import (Artifact, ArtifactRef, ArtifactRole, InputKind, Observation, ParentRelation)
from .archive import Limits, UnsafeEntry, iter_members
from .detect import AUDIO_KINDS, IMAGE_KINDS, VIDEO_KINDS, Detected, sniff
from .store import LimitExceeded, Store, _atomic_write, now_iso

JUNK = {".ds_store", "thumbs.db", "desktop.ini", ".localized"}
URL_RE = re.compile(r"^https?://\S+$", re.I)
TAG_PAGE, TAG_ARCHIVE, TAG_LOOSE, TAG_URL = "page_capture", "archive_capture", "loose_file", "url_observation"


@dataclass
class Issue:
    source: str
    code: str
    message: str
    origin: str | None = None
    severity: str = "error"  # error | warning | info

    def to_dict(self):
        return dict(self.__dict__)


@dataclass
class Entry:
    observation_id: str
    source_ref: str
    canonical_id: str
    status: str        # new | unchanged | new_version | duplicate_capture
    version: int
    tags: list[str]
    alias_of: list[str] = field(default_factory=list)

    def to_dict(self):
        return dict(self.__dict__)


@dataclass
class ImportReport:
    import_id: str
    entries: list[Entry] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    blobs_new: int = 0
    blobs_reused: int = 0
    skipped_junk: int = 0
    ignored_lines: int = 0

    def count(self, status: str) -> int:
        return sum(1 for e in self.entries if e.status == status)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    def summary(self) -> dict:
        return {"import_id": self.import_id, "observations": len(self.entries),
                **{s: self.count(s) for s in ("new", "duplicate_capture", "unchanged", "new_version")},
                "blobs_new": self.blobs_new, "blobs_reused": self.blobs_reused,
                "skipped_junk": self.skipped_junk, "issues": len(self.issues), "errors": len(self.errors)}


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def normalize_url(url: str) -> str:
    p = urlsplit(url.strip())
    host = (p.hostname or "").lower()
    port = f":{p.port}" if p.port and (p.scheme.lower(), p.port) not in (("http", 80), ("https", 443)) else ""
    return urlunsplit((p.scheme.lower(), host + port, p.path or "/", p.query, ""))


@dataclass
class _Unit:
    source_ref: str
    kind: str
    root: Path
    files: list[tuple[str, Path]] = field(default_factory=list)  # (rel NFC, ścieżka)


# ---------------------------------------------------------------- role
def role_for(rel: str, det: Detected, unit_kind: str) -> ArtifactRole:
    parts = rel.split("/")
    depth = len(parts) - 1
    lname = parts[-1].lower()
    k = det.kind
    arch = unit_kind == TAG_ARCHIVE
    if k == "zip":
        return ArtifactRole.archive
    if k == "har":
        return ArtifactRole.har
    if k == "html":
        if depth == 0 or (arch and parts[0] == "html"):
            return ArtifactRole.landing_dom if re.search(r"rendered|dom", lname) else ArtifactRole.landing_html
        return ArtifactRole.resource
    if k == "mhtml":
        return ArtifactRole.document
    if k in IMAGE_KINDS:
        if k == "png" and ((arch and parts[0] == "screenshots") or (depth == 0 and unit_kind == TAG_PAGE)):
            return ArtifactRole.landing_screenshot
        if depth == 0 and unit_kind != TAG_ARCHIVE:
            return ArtifactRole.creative_image
        return ArtifactRole.resource
    if k in VIDEO_KINDS:
        return ArtifactRole.video
    if k in AUDIO_KINDS:
        return ArtifactRole.audio
    if arch or depth == 0:
        return ArtifactRole.document if k in ("pdf", "json", "text") else ArtifactRole.other
    return ArtifactRole.resource


def _input_kind(unit_kind: str, roles: set[ArtifactRole]) -> InputKind:
    if unit_kind == TAG_LOOSE and roles <= {ArtifactRole.creative_image, ArtifactRole.video, ArtifactRole.audio}:
        return InputKind.creative_only
    return InputKind.full_capture


# ---------------------------------------------------------------- skan katalogu
class _Planner:
    def __init__(self, root: Path, issues: list[Issue], report: ImportReport):
        self.root, self.issues, self.report = root, issues, report
        self.units: list[_Unit] = []

    def ref(self, p: Path) -> str:
        try:
            rel = p.relative_to(self.root).as_posix()
        except ValueError:
            rel = p.name
        return nfc(self.root.name if rel == "." else rel)

    def _entries(self, d: Path) -> tuple[list[Path], list[Path]]:
        files, dirs = [], []
        try:
            items = sorted(os.scandir(d), key=lambda e: nfc(e.name))
        except OSError as e:
            self.issues.append(Issue(self.ref(d), "dir_unreadable", str(e)))
            return files, dirs
        for e in items:
            p = Path(e.path)
            if e.name.lower() in JUNK or e.name.startswith("._"):
                self.report.skipped_junk += 1
            elif e.is_symlink():
                self.issues.append(Issue(self.ref(p), "symlink_skipped", "dowiązanie symboliczne pominięte", severity="warning"))
            elif e.is_dir():
                dirs.append(p)
            elif e.is_file():
                files.append(p)
        return files, dirs

    @staticmethod
    def _head(p: Path) -> bytes:
        try:
            with p.open("rb") as f:
                return f.read(65536)
        except OSError:
            return b""

    def _is_archive_dir(self, d: Path) -> bool:
        return (d / "archive_info.json").is_file() or ((d / "sha256.json").is_file() and (d / "network.har").is_file())

    def _zip_unit(self, z: Path, siblings: list[Path]) -> _Unit:
        u = _Unit("zip:" + self.ref(z), TAG_ARCHIVE, z.parent, [(nfc(z.name), z)])
        for s in siblings:
            if s.name in (z.name + ".sha256", z.stem + ".sha256"):
                u.files.append((nfc(s.name), s))
        return u

    def _collect(self, d: Path, rel: str, unit: _Unit, consumed: set[Path]) -> None:
        files, dirs = self._entries(d)
        for f in files:
            if f in consumed:
                continue
            if self._head(f)[:2] == b"PK" and zipfile.is_zipfile(f):
                self.units.append(self._zip_unit(f, files))
                consumed.update(s for s in files if s.name.startswith(f.name) or s.name == f.stem + ".sha256")
                continue
            unit.files.append((nfc(f"{rel}{f.name}"), f))
        for sub in dirs:
            if self._is_archive_dir(sub):
                self.archive_dir(sub)
            else:
                self._collect(sub, f"{rel}{sub.name}/", unit, consumed)

    def archive_dir(self, d: Path) -> None:
        u = _Unit("dir:" + self.ref(d), TAG_ARCHIVE, d)
        self._walk_all(d, "", u)
        self.units.append(u)

    def _walk_all(self, d: Path, rel: str, u: _Unit) -> None:
        files, dirs = self._entries(d)
        u.files += [(nfc(rel + f.name), f) for f in files]
        for s in dirs:
            self._walk_all(s, f"{rel}{s.name}/", u)

    def scan_dir(self, d: Path) -> None:
        if self._is_archive_dir(d):
            return self.archive_dir(d)
        files, dirs = self._entries(d)
        zips = [f for f in files if self._head(f)[:2] == b"PK" and zipfile.is_zipfile(f)]
        sidecars = {s for z in zips for s in files if s.name in (z.name + ".sha256", z.stem + ".sha256")}
        for z in zips:
            self.units.append(self._zip_unit(z, files))
        rest = [f for f in files if f not in zips and f not in sidecars]
        kinds = {f: sniff(self._head(f), f.name).kind for f in rest}
        is_page = any(k in ("html", "mhtml") for k in kinds.values()) or any(k == "har" for k in kinds.values())
        if is_page:
            u = _Unit("dir:" + self.ref(d), TAG_PAGE, d, [(nfc(f.name), f) for f in rest])
            consumed: set[Path] = set(zips) | sidecars
            for sub in dirs:
                if self._is_archive_dir(sub):
                    self.archive_dir(sub)
                elif any(sniff(self._head(f), f.name).kind == "html" for f in self._entries(sub)[0]) \
                        and not sub.name.endswith("_files"):
                    self.scan_dir(sub)
                else:
                    self._collect(sub, nfc(sub.name) + "/", u, consumed)
            self.units.append(u)
        else:
            for f in rest:
                self.units.append(_Unit("file:" + self.ref(f), TAG_LOOSE, f.parent, [(nfc(f.name), f)]))
            for sub in dirs:
                self.scan_dir(sub)


def parse_url_list(text: str) -> Optional[tuple[list[str], int]]:
    """Zwraca (URL-e, liczba zignorowanych linii) gdy tekst wygląda na listę URL, inaczej None."""
    lines = [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
    urls = [l for l in lines if URL_RE.match(l)]
    if not urls or len(urls) * 2 < len(lines):
        return None
    return urls, len(lines) - len(urls)


# ---------------------------------------------------------------- import
class FileImporter:
    spec = AdapterSpec(id="importer-files-v1", kind=AdapterKind.importer, version="1.0",
                       description="Katalogi, pliki, ZIP i listy URL -> obserwacje z magazynem SHA-256")

    def __init__(self, limits: Limits = Limits()):
        self.limits = limits

    # --- API kontraktu
    def import_source(self, source: str, *, store: Any) -> Sequence[Observation]:
        store = store if isinstance(store, Store) else Store(store)
        rep = self.import_sources([source], store)
        return [store.observation(e.observation_id) for e in rep.entries]

    def import_sources(self, sources: Iterable[str | Path], store: Store | str | Path,
                       write_manifest: bool = True) -> ImportReport:
        store = store if isinstance(store, Store) else Store(store)
        rep = ImportReport(import_id=uuid.uuid4().hex[:12])
        run_blobs: set[str] = set()
        known: dict[str, set[str]] = {}
        for o in store.observations():
            known.setdefault(o.canonical_id, set()).add(o.observation_id)
        for src in sources:
            src = str(src)
            try:
                for ob, tags, spath in self._observations(src, store, rep, run_blobs):
                    self._save(store, rep, ob, tags, spath, known)
            except Exception as e:  # nieoczekiwany błąd jednego źródła nie przerywa pozostałych
                rep.issues.append(Issue(src, "source_failed", f"{type(e).__name__}: {e}"))
        store_log = store.root / "imports.jsonl"
        with store_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": now_iso(), "sources": [str(s) for s in sources], **rep.summary(),
                                "issues_detail": [i.to_dict() for i in rep.issues]}, ensure_ascii=False) + "\n")
        if write_manifest:
            from .manifest import write_manifest as wm
            wm(store)
        return rep

    def _save(self, store, rep, ob: Observation, tags, spath, known):
        status, ver = store.save_observation(ob, rep.import_id)
        peers = sorted(known.get(ob.canonical_id, set()) - {ob.observation_id})
        if status == "new" and peers:
            status = "duplicate_capture"
        known.setdefault(ob.canonical_id, set()).add(ob.observation_id)
        rep.entries.append(Entry(ob.observation_id, ob.source_ref, ob.canonical_id, status, ver, list(ob.tags), peers))
        with (store.root / "provenance.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"import_id": rep.import_id, "at": now_iso(), "observation_id": ob.observation_id,
                                "source_ref": ob.source_ref, "status": status, "version": ver,
                                "source_path": spath}, ensure_ascii=False) + "\n")

    # --- rozpoznanie źródeł
    def _observations(self, src, store, rep, run_blobs):
        if URL_RE.match(src):
            yield self._url_obs(src, "argument"), [TAG_URL], src
            return
        p = Path(src).expanduser()
        if not (p.exists() or p.is_symlink()):
            rep.issues.append(Issue(src, "source_missing", "źródło nie istnieje"))
            return
        if p.is_file():
            parsed = None
            try:
                if p.stat().st_size <= 1_000_000:
                    head = p.read_bytes()
                    if sniff(head[:65536], p.name).kind in ("text", "unknown") and b"\x00" not in head:
                        parsed = parse_url_list(head.decode("utf-8", "ignore"))
            except OSError as e:
                rep.issues.append(Issue(src, "file_unreadable", str(e)))
                return
            if parsed:
                urls, ignored = parsed
                rep.ignored_lines += ignored
                seen = set()
                for u in urls:
                    try:
                        n = normalize_url(u)
                    except ValueError as e:
                        rep.issues.append(Issue(src, "bad_url", f"{u[:80]}: {e}", severity="warning"))
                        continue
                    if n not in seen:
                        seen.add(n)
                        yield self._url_obs(u, p.name), [TAG_URL], str(p.resolve())
                return
        root = p.resolve() if p.is_dir() else p.resolve().parent
        pl = _Planner(root if p.is_dir() else root, rep.issues, rep)
        if p.is_dir():
            pl.scan_dir(root)
        else:
            f = p.resolve()
            head = pl._head(f)
            if head[:2] == b"PK" and zipfile.is_zipfile(f):
                pl.units.append(pl._zip_unit(f, [x for x in f.parent.iterdir() if x.is_file()]))
                pl.units[-1].source_ref = "zip:" + nfc(f.name)
            else:
                k = sniff(head, f.name).kind
                kind = TAG_PAGE if k in ("html", "mhtml") else TAG_LOOSE
                pl.units.append(_Unit(("dir:" if kind == TAG_PAGE else "file:") + nfc(f.name), kind, f.parent, [(nfc(f.name), f)]))
        for u in pl.units:
            try:
                ob = self._build(u, store, rep, run_blobs)
            except Exception as e:
                rep.issues.append(Issue(u.source_ref, "unit_failed", f"{type(e).__name__}: {e}"))
                continue
            if ob is not None:
                yield ob, ob.tags, str(u.root)

    # --- URL
    def _url_obs(self, url: str, origin_list: str) -> Observation:
        url = url.strip()
        norm = normalize_url(url)
        oid = ids.observation_id("url:" + norm, InputKind.url_only.value)
        sha = ids.sha256_bytes(url.encode("utf-8"))
        art = Artifact(artifact_id=ids.artifact_id(oid, "input_url", sha, "url"), observation_id=oid,
                       role=ArtifactRole.input_url, sha256=sha, size=len(url.encode("utf-8")),
                       media_type="text/uri-list", url=url, origin="url")
        return Observation(observation_id=oid, canonical_id=ids.canonical_id([], url=norm), source_ref="url:" + norm,
                           input_kind=InputKind.url_only, artifacts=[art], tags=[TAG_URL])

    # --- jednostka plikowa
    def _store_file(self, store, rep, run_blobs, path: Path, source: str, origin: str, max_bytes: int):
        try:
            with path.open("rb") as fh:
                sha, size, head, existed = store.put_stream(fh, max_bytes=max_bytes)
        except LimitExceeded as e:
            rep.issues.append(Issue(source, "file_too_large", str(e), origin))
            return None
        except OSError as e:
            rep.issues.append(Issue(source, "file_unreadable", str(e), origin))
            return None
        self._count_blob(rep, run_blobs, sha, existed)
        return sha, size, head

    @staticmethod
    def _count_blob(rep, run_blobs, sha, existed):
        if existed or sha in run_blobs:
            rep.blobs_reused += 1
        else:
            rep.blobs_new += 1
        run_blobs.add(sha)

    def _build(self, u: _Unit, store: Store, rep: ImportReport, run_blobs: set) -> Optional[Observation]:
        kind_for_id = InputKind.full_capture.value  # stałe, by zmiana typu zawartości nie zmieniała ID
        oid = ids.observation_id(u.source_ref, kind_for_id)
        recs: list[dict] = []
        for rel, path in sorted(u.files, key=lambda t: t[0]):
            stored = self._store_file(store, rep, run_blobs, path, u.source_ref, rel, self.limits.max_file_bytes)
            if stored is None:
                continue
            sha, size, head = stored
            det = sniff(head, path.name, size)
            self._note_unknown(rep, u, rel, det)
            r = {"rel": rel, "origin": rel, "sha": sha, "size": size, "det": det, "parent": None, "member": None}
            r["role"] = role_for(rel, det, u.kind)
            recs.append(r)
            if det.kind == "zip":
                recs += self._expand_zip(store, rep, run_blobs, u, r, path)
            if rel.lower().endswith(".sha256") and det.kind in ("text", "unknown"):
                self._check_sidecar(rep, u, rel, path, recs)
        if not recs:
            return None
        arts: list[Artifact] = []
        idmap: dict[int, str] = {}
        for r in recs:
            idmap[id(r)] = ids.artifact_id(oid, r["role"].value, r["sha"], r["origin"])
        for r in recs:
            par = r["parent"]
            arts.append(Artifact(
                artifact_id=idmap[id(r)], observation_id=oid, role=r["role"], local_ref=store.blob_ref(r["sha"]),
                sha256=r["sha"], size=r["size"], media_type=r["det"].media_type, origin=r["origin"],
                parent=ArtifactRef(artifact_id=idmap[id(par)]) if par else None,
                parent_relation=ParentRelation.archive_member if par else None))
        arts.sort(key=lambda a: (a.origin or "", a.artifact_id))
        seen: set[str] = set()
        arts = [a for a in arts if not (a.artifact_id in seen or seen.add(a.artifact_id))]
        pairs = [(a.role.value, a.sha256) for a in arts]
        try:
            cid = ids.canonical_id(pairs)
        except ValueError:
            cid = ids.canonical_id([], url="content-sha256:" + ",".join(sorted({a.sha256 for a in arts})))
        tags, ctime = [u.kind], None
        if u.kind == TAG_ARCHIVE:
            tags.append("zip_archive" if u.source_ref.startswith("zip:") else "unpacked_archive")
            extra, ctime = self.archive_metadata(store, arts)
            tags += extra
        roles = {a.role for a in arts}
        return Observation(observation_id=oid, canonical_id=cid, source_ref=u.source_ref,
                           input_kind=_input_kind(u.kind, roles), artifacts=arts, capture_time=ctime, tags=sorted(tags))

    @staticmethod
    def _note_unknown(rep, u, rel, det):
        if det.kind == "unknown":
            rep.issues.append(Issue(u.source_ref, "unknown_format", det.note or "nierozpoznany format", rel, "warning"))

    def _check_sidecar(self, rep, u, rel, path, recs):
        try:
            token = path.read_text(encoding="utf-8", errors="ignore").split()[0].lower()
        except (OSError, IndexError):
            return
        zips = [r for r in recs if r["det"].kind == "zip"]
        if re.fullmatch(r"[0-9a-f]{64}", token) and zips and token != zips[0]["sha"]:
            rep.issues.append(Issue(u.source_ref, "sidecar_sha256_mismatch",
                                    "suma .sha256 nie zgadza się z archiwum", rel, "warning"))

    def _expand_zip(self, store, rep, run_blobs, u, zrec, path) -> list[dict]:
        out: list[dict] = []
        total = 0
        try:
            with zipfile.ZipFile(store.blob_path(zrec["sha"])) as zf:
                for name, info, reject in iter_members(zf, self.limits):
                    origin = f"{zrec['rel']}!/{name}"
                    if reject:
                        rep.issues.append(Issue(u.source_ref, "archive_entry_rejected", reject, origin, "warning"))
                        continue
                    try:
                        with zf.open(info) as fh:
                            sha, size, head, existed = store.put_stream(
                                fh, max_bytes=min(self.limits.max_member_bytes, self.limits.max_total_bytes - total))
                    except LimitExceeded as e:
                        rep.issues.append(Issue(u.source_ref, "archive_entry_rejected", f"limit: {e}", origin, "warning"))
                        continue
                    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, EOFError) as e:
                        rep.issues.append(Issue(u.source_ref, "archive_entry_unreadable", str(e), origin))
                        continue
                    total += size
                    self._count_blob(rep, run_blobs, sha, existed)
                    det = sniff(head, name, size)
                    self._note_unknown(rep, u, origin, det)
                    role = ArtifactRole.archive if det.kind == "zip" else role_for(name, det, TAG_ARCHIVE)
                    if det.kind == "zip":
                        rep.issues.append(Issue(u.source_ref, "nested_archive_not_expanded",
                                                "zagnieżdżone archiwum zapisane bez rozpakowania", origin, "info"))
                    out.append({"rel": origin, "origin": origin, "sha": sha, "size": size, "det": det,
                                "parent": zrec, "role": role, "member": name})
        except UnsafeEntry as e:
            rep.issues.append(Issue(u.source_ref, "archive_rejected", str(e), zrec["rel"]))
        except (zipfile.BadZipFile, OSError) as e:
            rep.issues.append(Issue(u.source_ref, "archive_unreadable", str(e), zrec["rel"]))
        return out

    @staticmethod
    def archive_metadata(store: Store, arts: list[Artifact]) -> tuple[list[str], Optional[str]]:
        """Z dokumentów archiwum: czy pobranie zakończyło się błędem HTTP (wyłączenie z korpusu) i czas zapisu."""
        tags: list[str] = []
        ctime = None
        by_origin = {(a.origin or "").split("!/")[-1]: a for a in arts}
        by_filename = {PurePosixPath(origin).name: art for origin, art in by_origin.items()}
        code = None
        info = by_origin.get("archive_info.json") or by_filename.get("archive_info.json")
        if info:
            try:
                d = json.loads((store.root / info.local_ref).read_text(encoding="utf-8"))
                m = re.search(r"(?<!\d)([45]\d\d)\s*:", str(d.get("title", "")))
                code = int(m.group(1)) if m else None
                ts = d.get("timestamp_utc")
                if isinstance(ts, str):
                    ts = ts if re.search(r"(Z|[+-]\d\d:\d\d)$", ts) else ts + "Z"
                    if re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)", ts):
                        ctime = ts
            except (OSError, ValueError, AttributeError):
                pass
        har = by_origin.get("network.har") or by_filename.get("network.har")
        if code is None and har:
            try:
                d = json.loads((store.root / har.local_ref).read_text(encoding="utf-8"))
                st = d["log"]["entries"][0]["response"]["status"]
                code = st if isinstance(st, int) and st >= 400 else None
            except (OSError, ValueError, KeyError, IndexError, TypeError):
                pass
        if code:
            tags += ["excluded_from_corpus", f"http_{code}"]
        return tags, ctime


ADAPTERS = [FileImporter()]


def get_adapters():
    return ADAPTERS
