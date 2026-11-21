"""Interfejs zapisu artefaktów pochodnych (zgodny z F01/F02) i magazyn pamięciowy do testów."""
from __future__ import annotations

from typing import Optional, Protocol

from methadata.contracts import Artifact, ArtifactRef, ids
from methadata.contracts.models import ArtifactRole, ParentRelation


class DerivedStore(Protocol):
    def put_derived(self, *, observation_id: str, role: ArtifactRole, data: bytes, media_type: Optional[str],
                    url: Optional[str], parent: ArtifactRef, parent_relation: ParentRelation,
                    origin: str) -> Artifact:
        """Utrwala bajty raz (adresowanie SHA-256) i zwraca Artifact z parent/origin. Idempotentne."""


class MemoryDerivedStore:
    """Magazyn testowy zgodny z F01: ID wynikają z treści, bajty trzymane w pamięci."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.artifacts: dict[str, Artifact] = {}

    def put_derived(self, *, observation_id, role, data, media_type, url, parent, parent_relation, origin):
        sha = ids.sha256_bytes(data)
        aid = ids.artifact_id(observation_id, role.value, sha, origin)
        art = Artifact(artifact_id=aid, observation_id=observation_id, role=role,
                       local_ref=f"derived/{sha[:2]}/{sha}", sha256=sha, size=len(data), media_type=media_type,
                       url=url, parent=parent, parent_relation=parent_relation, origin=origin)
        self.blobs[sha] = data
        self.artifacts[aid] = art
        return art

    def open_bytes(self, ref: ArtifactRef) -> bytes:
        return self.blobs[self.artifacts[ref.artifact_id].sha256]
