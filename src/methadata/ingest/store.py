"""Magazyn artefaktów: bloby po SHA-256, wersje obserwacji (JSONL), artefakty pochodne, odczyt (ArtifactReader)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterator, Optional, Sequence

from ..contracts import ids
from ..contracts.models import Artifact, ArtifactRef, ArtifactRole, Observation, ParentRelation

CHUNK = 1 << 20
_SAFE = re.compile(r"[^A-Za-z0-9._\-]")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class LimitExceeded(Exception):
    pass


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        for d in ("blobs", "observations", "derived", "tmp"):
            (self.root / d).mkdir(parents=True, exist_ok=True)
        self._art_index: dict[str, tuple[str, int]] | None = None

    # ---------------------------------------------------------------- bloby
    def blob_ref(self, sha: str) -> str:
        return f"blobs/{sha[:2]}/{sha}"

    def blob_path(self, sha: str) -> Path:
        return self.root / self.blob_ref(sha)

    def has_blob(self, sha: str) -> bool:
        return self.blob_path(sha).is_file()

    def put_stream(self, fh: BinaryIO, *, max_bytes: int | None = None) -> tuple[str, int, bytes, bool]:
        """Zapisuje strumień; zwraca (sha256, rozmiar, początek pliku, czy_blob_już_istniał).
        Przekroczenie max_bytes -> LimitExceeded (liczone faktycznie, nie z nagłówka)."""
        h = hashlib.sha256()
        size = 0
        head = b""
        fd, tmp = tempfile.mkstemp(dir=self.root / "tmp", prefix="in-")
        try:
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = fh.read(CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise LimitExceeded(f"przekroczono limit {max_bytes} B")
                    if len(head) < 65536:
                        head += chunk[: 65536 - len(head)]
                    h.update(chunk)
                    out.write(chunk)
            sha = h.hexdigest()
            dest = self.blob_path(sha)
            existed = dest.is_file()
            if existed:
                os.unlink(tmp)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp, dest)
            return sha, size, head, existed
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def put_bytes(self, data: bytes) -> tuple[str, int, bool]:
        sha = ids.sha256_bytes(data)
        existed = self.has_blob(sha)
        if not existed:
            _atomic_write(self.blob_path(sha), data)
        return sha, len(data), existed

    # ---------------------------------------------------------------- obserwacje (wersje)
    def _obs_file(self, obs_id: str) -> Path:
        return self.root / "observations" / f"{obs_id}.jsonl"

    def versions(self, obs_id: str) -> list[dict]:
        p = self._obs_file(obs_id)
        if not p.is_file():
            return []
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]

    def save_observation(self, obs: Observation, import_id: str) -> tuple[str, int]:
        """Zwraca (status, wersja): new | unchanged | new_version."""
        vs = self.versions(obs.observation_id)
        new = obs.model_dump(mode="json")
        if vs:
            if vs[-1]["observation"] == new:
                return "unchanged", vs[-1]["version"]
            status, ver = "new_version", vs[-1]["version"] + 1
        else:
            status, ver = "new", 1
        rec = {"version": ver, "import_id": import_id, "imported_at": now_iso(), "observation": new}
        with self._obs_file(obs.observation_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        self._art_index = None
        return status, ver

    def observation_ids(self) -> list[str]:
        return sorted(p.stem for p in (self.root / "observations").glob("*.jsonl"))

    def observations(self) -> list[Observation]:
        return [self.observation(i) for i in self.observation_ids()]

    # ---------------------------------------------------------------- ArtifactReader
    def observation(self, observation_id: str) -> Observation:
        vs = self.versions(observation_id)
        if not vs:
            raise KeyError(observation_id)
        return Observation.model_validate(vs[-1]["observation"])

    def _index(self) -> dict[str, tuple[str, int]]:
        if self._art_index is None:
            idx: dict[str, tuple[str, int]] = {}
            for oid in self.observation_ids():
                for v in self.versions(oid):
                    for a in v["observation"]["artifacts"]:
                        idx[a["artifact_id"]] = (oid, v["version"])
            self._art_index = idx
        return self._art_index

    def artifact(self, artifact_id: str) -> Artifact:
        hit = self._index().get(artifact_id)
        if hit:
            oid, ver = hit
            for v in self.versions(oid):
                if v["version"] == ver:
                    for a in v["observation"]["artifacts"]:
                        if a["artifact_id"] == artifact_id:
                            return Artifact.model_validate(a)
        for a in self._all_derived():
            if a.artifact_id == artifact_id:
                return a
        raise KeyError(artifact_id)

    def open_bytes(self, ref: ArtifactRef | str) -> bytes:
        aid = ref if isinstance(ref, str) else ref.artifact_id
        a = self.artifact(aid)
        if a.local_ref is None:
            raise FileNotFoundError(f"artefakt {aid} nie ma pliku (role={a.role.value})")
        return (self.root / a.local_ref).read_bytes()

    def open_path(self, artifact_id: str) -> Path:
        a = self.artifact(artifact_id)
        if a.local_ref is None:
            raise FileNotFoundError(artifact_id)
        return self.root / a.local_ref

    def children(self, artifact_id: str) -> Sequence[Artifact]:
        a = self.artifact(artifact_id)
        obs = self.observation(a.observation_id)
        kids = [x for x in obs.artifacts if x.parent and x.parent.artifact_id == artifact_id]
        kids += [d for d in self.list_derived(artifact_id)]
        return kids

    # ---------------------------------------------------------------- artefakty pochodne
    def _derived_dir(self, parent_id: str, producer: str) -> Path:
        return self.root / "derived" / _SAFE.sub("_", parent_id) / _SAFE.sub("_", producer)

    def put_derived(self, parent_id: str, producer: str, name: str, data: bytes | str | dict | list, *,
                    media_type: str | None = None, role: ArtifactRole = ArtifactRole.other) -> Artifact:
        """Zapis artefaktu pochodnego (np. wynik OCR) dla artefaktu `parent_id`; idempotentny dla tych samych bajtów.
        Nie zmienia obserwacji ani canonical_id. `producer` = np. wersja ekstraktora."""
        parent = self.artifact(parent_id)
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
            media_type = media_type or "application/json"
        elif isinstance(data, str):
            data = data.encode("utf-8")
            media_type = media_type or "text/plain"
        if not name or "/" in name or name.startswith("."):
            raise ValueError(f"niepoprawna nazwa artefaktu pochodnego: {name!r}")
        sha, size, _ = self.put_bytes(data)
        origin = f"derived:{producer}/{name}"
        art = Artifact(
            artifact_id=ids.artifact_id(parent.observation_id, role.value, sha, origin + "<" + parent_id),
            observation_id=parent.observation_id, role=role, local_ref=self.blob_ref(sha), sha256=sha, size=size,
            media_type=media_type or "application/octet-stream",
            parent=ArtifactRef(artifact_id=parent_id), parent_relation=ParentRelation.derived_from, origin=origin)
        meta = self._derived_dir(parent_id, producer) / (_SAFE.sub("_", name) + ".json")
        _atomic_write(meta, json.dumps({"name": name, "producer": producer, "created_at": now_iso(),
                                        "artifact": art.model_dump(mode="json")},
                                       ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8"))
        return art

    def get_derived(self, parent_id: str, producer: str, name: str) -> Optional[Artifact]:
        meta = self._derived_dir(parent_id, producer) / (_SAFE.sub("_", name) + ".json")
        if not meta.is_file():
            return None
        return Artifact.model_validate(json.loads(meta.read_text(encoding="utf-8"))["artifact"])

    def read_derived(self, parent_id: str, producer: str, name: str) -> Optional[bytes]:
        a = self.get_derived(parent_id, producer, name)
        return None if a is None else (self.root / a.local_ref).read_bytes()

    def list_derived(self, parent_id: str, producer: str | None = None) -> list[Artifact]:
        base = self.root / "derived" / _SAFE.sub("_", parent_id)
        if not base.is_dir():
            return []
        dirs = [base / _SAFE.sub("_", producer)] if producer else sorted(p for p in base.iterdir() if p.is_dir())
        out = []
        for d in dirs:
            for m in sorted(d.glob("*.json")):
                out.append(Artifact.model_validate(json.loads(m.read_text(encoding="utf-8"))["artifact"]))
        return out

    def _all_derived(self) -> Iterator[Artifact]:
        for m in sorted((self.root / "derived").glob("*/*/*.json")):
            yield Artifact.model_validate(json.loads(m.read_text(encoding="utf-8"))["artifact"])
