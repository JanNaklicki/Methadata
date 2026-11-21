"""Interfejsy modułów. Moduł implementuje jeden z nich i wystawia `spec: AdapterSpec`."""
from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Optional, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from .models import (Artifact, ArtifactRef, ClusterRun, ExtractionStatus, Feature, Observation,
                     PairEvidence)


class AdapterKind(str, Enum):
    importer = "importer"
    extractor = "extractor"
    comparator = "comparator"
    clusterer = "clusterer"


class OptionalInput(BaseModel):
    """Opcjonalne wejście grafowe lub wektorowe. Moduł deklaruje je w spec; runner podaje
    pod nazwą `name` zmapowany obiekt w argumencie `optional`. Gdy go brak, moduł musi działać bez niego."""
    model_config = ConfigDict(extra="forbid")
    name: str
    kind: str = Field(pattern=r"^(vector|graph)$")
    description: str = ""
    # vector: wartości Feature(value_type=vector) z VectorRef o tym model/dim; graph: artefakt role=graph_edges
    model: str | None = None
    dim: int | None = None


class AdapterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")  # myślniki dozwolone
    kind: AdapterKind
    version: str
    description: str = ""
    extras: list[str] = Field(default_factory=list)  # nazwy extras z pyproject wymaganych do działania
    optional_inputs: list[OptionalInput] = Field(default_factory=list)


class ArtifactReader(Protocol):
    """Odczyt artefaktów po ID/odwołaniu; implementuje F02 (magazyn adresowany SHA-256)."""
    def observation(self, observation_id: str) -> Observation: ...
    def artifact(self, artifact_id: str) -> Artifact: ...
    def open_bytes(self, ref: ArtifactRef) -> bytes:
        """Bajty całego artefaktu; fragment wskazuje ref.locator (interpretuje wołający)."""
    def children(self, artifact_id: str) -> Sequence[Artifact]: ...


@runtime_checkable
class Importer(Protocol):
    spec: AdapterSpec
    def import_source(self, source: str, *, store: Any) -> Sequence[Observation]:
        """Idempotentny import: ten sam rekord źródłowy -> te same ID; zmiana treści -> nowa wersja."""


@runtime_checkable
class Extractor(Protocol):
    spec: AdapterSpec
    def extract(self, observation: Observation, *, reader: ArtifactReader,
                optional: Mapping[str, Any] | None = None) -> tuple[Sequence[Feature], Sequence[ExtractionStatus]]: ...


@runtime_checkable
class Comparator(Protocol):
    spec: AdapterSpec
    def compare(self, a: Observation, b: Observation, features: Mapping[str, Sequence[Feature]], *,
                run_id: str, optional: Mapping[str, Any] | None = None) -> Sequence[PairEvidence]:
        """features: observation_id -> cechy. Para niewykonalna -> PairEvidence ze score=None."""


@runtime_checkable
class Clusterer(Protocol):
    spec: AdapterSpec
    def cluster(self, observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                config: Mapping[str, Any], *, seed: int = 0,
                optional: Mapping[str, Any] | None = None) -> ClusterRun:
        """Wynik niezależny od kolejności wejścia (dla tego samego seeda)."""


_KIND_METHOD = {
    AdapterKind.importer: "import_source",
    AdapterKind.extractor: "extract",
    AdapterKind.comparator: "compare",
    AdapterKind.clusterer: "cluster",
}


def check_adapter(obj: Any) -> AdapterSpec:
    spec = getattr(obj, "spec", None)
    if not isinstance(spec, AdapterSpec):
        raise TypeError(f"{obj!r}: brak atrybutu spec: AdapterSpec")
    if not callable(getattr(obj, _KIND_METHOD[spec.kind], None)):
        raise TypeError(f"{spec.id}: kind={spec.kind.value} wymaga metody {_KIND_METHOD[spec.kind]}()")
    return spec
