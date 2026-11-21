"""Rekordy v1 (§2 PLAN_KLASTROWANIA.md). Źródło prawdy dla JSON Schema w schemas/v1/."""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import ids
from .urls import mask_url

SCHEMA_VERSION = "1"

Id = Annotated[str, Field(pattern=ids.ID_PATTERN)]
Sha256 = Annotated[str, Field(pattern=ids.SHA256_PATTERN)]
Score = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
SchemaVersion = Literal["1"]


class _Rec(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- referencje

class TextSpan(_Rec):
    kind: Literal["text_span"] = "text_span"
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(ge=0)]  # wyłącznie, w znakach Unicode dekodowanego tekstu UTF-8

    @model_validator(mode="after")
    def _ordered(self):
        if self.end <= self.start:
            raise ValueError("text_span: end musi być > start")
        return self


class ImageRect(_Rec):
    kind: Literal["image_rect"] = "image_rect"
    x: Annotated[int, Field(ge=0)]
    y: Annotated[int, Field(ge=0)]
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    frame_ms: Optional[Annotated[int, Field(ge=0)]] = None  # klatka filmu; None dla obrazu


class HarEntry(_Rec):
    kind: Literal["har_entry"] = "har_entry"
    index: Annotated[int, Field(ge=0)]  # indeks w log.entries


class ByteRange(_Rec):
    kind: Literal["byte_range"] = "byte_range"
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def _ordered(self):
        if self.end <= self.start:
            raise ValueError("byte_range: end musi być > start")
        return self


Locator = Annotated[Union[TextSpan, ImageRect, HarEntry, ByteRange], Field(discriminator="kind")]


class ArtifactRef(_Rec):
    """Odwołanie do artefaktu, opcjonalnie do jego fragmentu. Bez locator = cały artefakt."""
    artifact_id: Id
    locator: Optional[Locator] = None


# ---------------------------------------------------------------- Artifact / Observation

class ArtifactRole(str, Enum):
    landing_html = "landing_html"
    landing_dom = "landing_dom"
    landing_screenshot = "landing_screenshot"
    creative_image = "creative_image"
    resource = "resource"
    har = "har"
    input_url = "input_url"
    archive = "archive"
    document = "document"
    video = "video"
    audio = "audio"
    vector_store = "vector_store"  # opcjonalne wejście wektorowe (patrz interfaces.OptionalInput)
    graph_edges = "graph_edges"    # opcjonalne wejście grafowe
    other = "other"


class ParentRelation(str, Enum):
    archive_member = "archive_member"      # plik w ZIP
    har_response = "har_response"          # odpowiedź osadzona w HAR
    inline_in = "inline_in"                # skrypt/styl inline w HTML
    referenced_by = "referenced_by"        # zasób wskazany przez tag/regułę/wpis HAR
    derived_from = "derived_from"          # np. DOM po renderowaniu, klatka filmu


class Artifact(_Rec):
    schema_version: SchemaVersion = SCHEMA_VERSION
    artifact_id: Id
    observation_id: Id
    role: ArtifactRole
    local_ref: Optional[str] = None  # ścieżka względem katalogu artefaktów; None gdy brak pliku (np. input_url)
    sha256: Sha256
    size: Optional[Annotated[int, Field(ge=0)]] = None
    media_type: Optional[str] = None
    url: Optional[str] = None          # kontrolowane wejście, wartość surowa
    display_url: Optional[str] = None  # postać do prezentacji, z zamaskowanymi identyfikatorami
    parent: Optional[ArtifactRef] = None  # relacja rodzic–dziecko; locator = miejsce dziecka w rodzicu
    parent_relation: Optional[ParentRelation] = None
    origin: Optional[str] = None       # nazwa/lokalizacja w źródle

    @model_validator(mode="after")
    def _check(self):
        if (self.parent is None) != (self.parent_relation is None):
            raise ValueError("parent i parent_relation podaje się razem")
        if self.parent is not None and self.parent.artifact_id == self.artifact_id:
            raise ValueError("artefakt nie może być własnym rodzicem")
        if self.role is ArtifactRole.input_url and not self.url:
            raise ValueError("input_url wymaga pola url")
        if self.role is not ArtifactRole.input_url and self.local_ref is None:
            raise ValueError("artefakt plikowy wymaga local_ref")
        if self.local_ref is not None and (self.local_ref.startswith("/") or ".." in self.local_ref.split("/")):
            raise ValueError("local_ref musi być względny i bez '..'")
        if self.url:
            expected = mask_url(self.url)
            if self.display_url is None:
                self.display_url = expected
            elif self.display_url != expected:
                raise ValueError("display_url musi mieć zamaskowane identyfikatory kliknięcia")
        elif self.display_url is not None:
            raise ValueError("display_url bez url")
        return self


class InputKind(str, Enum):
    full_capture = "full_capture"   # kreacja i/lub zapisana strona
    url_only = "url_only"
    creative_only = "creative_only"


class Observation(_Rec):
    schema_version: SchemaVersion = SCHEMA_VERSION
    observation_id: Id
    canonical_id: Id
    source_ref: str = Field(min_length=1)
    input_kind: InputKind
    artifacts: list[Artifact] = Field(min_length=1)
    capture_time: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
    tags: list[str] = Field(default_factory=list)  # np. "synthetic" dla próbek bez danych źródłowych

    @model_validator(mode="after")
    def _check(self):
        seen = set()
        for a in self.artifacts:
            if a.observation_id != self.observation_id:
                raise ValueError(f"artefakt {a.artifact_id} należy do innej obserwacji")
            if a.artifact_id in seen:
                raise ValueError(f"zduplikowany artifact_id {a.artifact_id}")
            seen.add(a.artifact_id)
        for a in self.artifacts:
            if a.parent and a.parent.artifact_id not in seen:
                raise ValueError(f"rodzic {a.parent.artifact_id} artefaktu {a.artifact_id} nie istnieje w obserwacji")
        roles = {a.role for a in self.artifacts}
        if self.input_kind is InputKind.url_only and roles - {ArtifactRole.input_url}:
            raise ValueError("url_only może zawierać wyłącznie input_url")
        if self.input_kind is not InputKind.url_only and ArtifactRole.input_url in roles and len(roles) == 1:
            raise ValueError("obserwacja z samym input_url ma input_kind=url_only")
        return self


# ---------------------------------------------------------------- Feature / ExtractionStatus

class VectorRef(_Rec):
    ref: ArtifactRef            # artefakt role=vector_store
    index: Annotated[int, Field(ge=0)]
    model: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    dim: Annotated[int, Field(gt=0)]


class ValueType(str, Enum):
    string = "string"
    number = "number"
    boolean = "boolean"
    string_list = "string_list"
    json = "json"
    vector = "vector"


class Feature(_Rec):
    schema_version: SchemaVersion = SCHEMA_VERSION
    feature_id: Id
    observation_id: Id
    family: str = Field(min_length=1)  # np. "asset", "text", "dom", "url"
    kind: str = Field(min_length=1)    # np. "image_sha256"
    value_type: ValueType
    value: Any = None
    vector_ref: Optional[VectorRef] = None
    extractor_version: str = Field(min_length=1)
    evidence_refs: list[ArtifactRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self):
        t, v = self.value_type, self.value
        if t is ValueType.vector:
            if self.vector_ref is None or v is not None:
                raise ValueError("value_type=vector wymaga vector_ref i value=null")
            return self
        if self.vector_ref is not None:
            raise ValueError("vector_ref tylko dla value_type=vector")
        ok = {
            ValueType.string: isinstance(v, str),
            ValueType.number: isinstance(v, (int, float)) and not isinstance(v, bool),
            ValueType.boolean: isinstance(v, bool),
            ValueType.string_list: isinstance(v, list) and all(isinstance(x, str) for x in v),
            ValueType.json: v is not None,
        }[t]
        if not ok:
            raise ValueError(f"value nie pasuje do value_type={t.value}")
        return self


class ExtractionState(str, Enum):
    ok = "ok"
    missing_input = "missing_input"
    failed = "failed"
    unsupported = "unsupported"


class ExtractionStatus(_Rec):
    schema_version: SchemaVersion = SCHEMA_VERSION
    observation_id: Id
    artifact_id: Optional[Id] = None
    extractor_version: str = Field(min_length=1)
    state: ExtractionState
    detail: Optional[str] = None  # np. komunikat błędu; pusty wynik `ok` ≠ brak danych


# ---------------------------------------------------------------- PairEvidence

class Relation(str, Enum):
    duplicate_capture = "duplicate_capture"
    shared_template = "shared_template"
    shared_narrative = "shared_narrative"
    campaign_candidate = "campaign_candidate"


class PairEvidence(_Rec):
    schema_version: SchemaVersion = SCHEMA_VERSION
    pair_evidence_id: Optional[Id] = None  # wyliczane, jeśli nie podano
    run_id: Id
    pair: tuple[Id, Id]  # uporządkowane canonical_id
    method_id: Id
    relation: Relation
    score: Optional[Score] = None  # None = porównanie niewykonalne
    evidence_refs: dict[Id, list[ArtifactRef]] = Field(default_factory=dict)  # klucz: canonical_id strony
    explanation: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self):
        a, b = self.pair
        if a == b:
            raise ValueError("para musi składać się z dwóch różnych materiałów")
        if a > b:
            raise ValueError("pair musi być uporządkowana rosnąco")
        extra = set(self.evidence_refs) - {a, b}
        if extra:
            raise ValueError(f"evidence_refs wskazuje materiały spoza pary: {sorted(extra)}")
        if self.score is not None and not (self.evidence_refs.get(a) and self.evidence_refs.get(b)):
            raise ValueError("wykonane porównanie wymaga dowodów po obu stronach")
        if self.pair_evidence_id is None:
            self.pair_evidence_id = ids.pair_evidence_id(self.run_id, self.pair, self.method_id, self.relation.value)
        return self


# ---------------------------------------------------------------- ClusterRun

class AssignmentStatus(str, Enum):
    grouped = "grouped"
    singleton = "singleton"
    noise = "noise"
    insufficient_data = "insufficient_data"


class Assignment(_Rec):
    material_id: Id
    cluster_ids: list[Id] = Field(default_factory=list)  # lokalne dla uruchomienia; może być kilka
    status: AssignmentStatus

    @model_validator(mode="after")
    def _check(self):
        if (self.status is AssignmentStatus.grouped) != bool(self.cluster_ids):
            raise ValueError("cluster_ids niepuste wtedy i tylko wtedy, gdy status=grouped")
        return self


class Edge(_Rec):
    source: Id
    target: Id
    relation: Relation
    pair_evidence_id: Id  # każda relacja wskazuje dowód
    weight: Optional[Score] = None


class ClusterRun(_Rec):
    schema_version: SchemaVersion = SCHEMA_VERSION
    run_id: Id
    dataset_version: str = Field(min_length=1)
    method_id: Id
    method_version: str = Field(min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)
    seed: int
    relation: Relation
    assignments: list[Assignment]
    edges: list[Edge] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self):
        mats = [a.material_id for a in self.assignments]
        if len(set(mats)) != len(mats):
            raise ValueError("materiał przypisany więcej niż raz")
        for e in self.edges:
            if e.source not in mats or e.target not in mats:
                raise ValueError("krawędź łączy materiał spoza przypisań")
        return self


# ---------------------------------------------------------------- Bundle (JSON do UI/testów)

class Bundle(_Rec):
    """Spójny pakiet rekordów. validate_references() sprawdza odwołania między rekordami."""
    schema_version: SchemaVersion = SCHEMA_VERSION
    observations: list[Observation] = Field(default_factory=list)
    features: list[Feature] = Field(default_factory=list)
    statuses: list[ExtractionStatus] = Field(default_factory=list)
    pairs: list[PairEvidence] = Field(default_factory=list)
    runs: list[ClusterRun] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self):
        obs = {o.observation_id: o for o in self.observations}
        arts = {a.artifact_id: a for o in self.observations for a in o.artifacts}
        canon = {o.canonical_id for o in self.observations}

        def need_art(ref: ArtifactRef, where: str):
            if ref.artifact_id not in arts:
                raise ValueError(f"{where}: nieznany artefakt {ref.artifact_id}")

        for f in self.features:
            if f.observation_id not in obs:
                raise ValueError(f"feature {f.feature_id}: nieznana obserwacja")
            for r in f.evidence_refs:
                need_art(r, f"feature {f.feature_id}")
            if f.vector_ref:
                need_art(f.vector_ref.ref, f"feature {f.feature_id}")
        for s in self.statuses:
            if s.observation_id not in obs:
                raise ValueError("status: nieznana obserwacja")
            if s.artifact_id and s.artifact_id not in arts:
                raise ValueError(f"status: nieznany artefakt {s.artifact_id}")
        pev = set()
        for p in self.pairs:
            for c in p.pair:
                if c not in canon:
                    raise ValueError(f"pair: nieznany materiał {c}")
            for c, refs in p.evidence_refs.items():
                for r in refs:
                    need_art(r, f"pair {p.pair_evidence_id}")
                    owner = next(o for o in self.observations if r.artifact_id in {a.artifact_id for a in o.artifacts})
                    if owner.canonical_id != c:
                        raise ValueError(f"pair {p.pair_evidence_id}: dowód {r.artifact_id} nie należy do {c}")
            pev.add(p.pair_evidence_id)
        for run in self.runs:
            for a in run.assignments:
                if a.material_id not in canon:
                    raise ValueError(f"run {run.run_id}: nieznany materiał {a.material_id}")
            for e in run.edges:
                if e.pair_evidence_id not in pev:
                    raise ValueError(f"run {run.run_id}: krawędź bez PairEvidence {e.pair_evidence_id}")
        return self
