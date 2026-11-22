// Typy wyprowadzone z contracts/http/openapi.yaml (W01).
export type Relation = 'duplicate_capture' | 'shared_template' | 'shared_narrative' | 'campaign_candidate'
export type InputKind = 'full_capture' | 'url_only' | 'creative_only'
export type ErrorCode =
  | 'bad_request' | 'validation_failed' | 'payload_too_large' | 'unsupported_media_type'
  | 'observation_not_found' | 'run_not_found' | 'evidence_not_found' | 'scenario_not_available' | 'run_not_ready'
  | 'run_failed' | 'storage_unavailable' | 'internal_error'

export interface ErrorBody {
  code: ErrorCode
  message: string
  details?: { field: string; message: string }[]
  retryable?: boolean
}
export interface ErrorResponse { error: ErrorBody }

export interface Demo {
  observation_id: string
  dataset_name: string
  dataset_version: string
  description: string
  material_count: number
  input_kind?: InputKind
}

export interface ObservationCreated {
  observation_id: string
  canonical_id: string
  input_kind: InputKind
  created: boolean
  label?: string | null
  display_url?: string | null
  artifacts?: { artifact_id: string; role: string; media_type?: string | null; size?: number | null; sha256?: string | null; display_url?: string | null }[]
  analysis: { mode: 'offline'; note: string }
}

export interface RunAccepted { run_id: string; observation_id: string; status: 'queued'; status_url: string }

export interface Preview {
  href: string
  media_type: 'image/png' | 'image/jpeg' | 'image/webp' | 'image/gif'
  width?: number
  height?: number
  alt?: string
}
export interface Material {
  material_id: string
  observation_id: string
  input_kind: InputKind
  label: string
  display_url?: string | null
  thumbnail?: Preview | null
  available_evidence?: string[]
}
export interface PairEvidenceView {
  evidence_id: string
  pair: [string, string]
  method_id: string
  relation: Relation
  score: number | null
  explanation: string
  evidence_url: string
}
export interface ClusterView { index?: number; cluster_id: string; material_ids: string[]; relation: Relation; summary?: string }
export type Outcome = 'grouped' | 'no_groups' | 'insufficient_evidence'
export interface SharedArtifact {
  kind: 'domain' | 'tracker' | 'image' | 'script' | 'person' | 'brand' | 'contact' | 'affiliate' | 'infrastructure'
  label: string
  material_ids: string[]
  /** Tylko `image`: podgląd obrazu, gdy jest w magazynie. */
  preview?: Preview | null
}
export interface DomainMeta {
  ips?: string[]
  tls_issuer?: string
  tls_not_before?: string
  registrar?: string
  created?: string
  nameservers?: string[]
}
export interface SuggestedEdge {
  source: string
  target: string
  pair_evidence_id: string
  score: number
  method_id: string
  label: string
  explanation: string
}
export interface ClusterRunView {
  investigation?: {
    artifacts: SharedArtifact[]
    domains: Record<string, string[]>
    domain_meta?: Record<string, DomainMeta>
    /** Zwięzły indeks treści materiału (małe litery) do wyszukiwania po słowach kluczowych. */
    search_text?: Record<string, string>
    captured_at: Record<string, string>
    edge_families: Record<string, string[]>
    suggested_edges?: SuggestedEdge[]
    manual_edge_ids?: string[]
  }
  collection?: boolean
  cluster_run: Record<string, unknown> & {
    edges?: { source: string; target: string; pair_evidence_id: string; weight?: number }[]
    assignments?: { material_id: string; status: string }[]
  }
  summary: {
    outcome: Outcome
    material_count: number
    grouped: number
    singleton: number
    noise: number
    insufficient_data: number
    message?: string
  }
  materials: Material[]
  clusters: ClusterView[]
  pair_evidence: PairEvidenceView[]
}

interface RunBase { run_id: string; observation_id: string; created_at: string; updated_at: string }
export type Run =
  | (RunBase & { status: 'queued' })
  | (RunBase & { status: 'running'; progress: { stage: string; fraction?: number } })
  | (RunBase & { status: 'succeeded'; result: ClusterRunView })
  | (RunBase & { status: 'failed'; error: ErrorBody })

export interface TextExcerpt { text: string; highlight_start?: number; highlight_end?: number; source_label?: string }
export interface EvidenceItem {
  artifact_id: string
  sha256?: string
  role: string
  media_type?: string | null
  display_url?: string | null
  locator?: { kind: string; [k: string]: unknown } | null
  text_excerpts?: TextExcerpt[]
  preview?: Preview | null
}
export interface EvidenceSide { material_id: string; label: string; display_url?: string | null; items: EvidenceItem[] }
export interface Evidence {
  evidence_id: string
  run_id: string
  relation: Relation
  method_id: string
  score: number | null
  explanation: string
  available: boolean
  limitations?: string[]
  families?: {name: string; score: number | null; supports: boolean}[]
  signals?: SupplementalSignal[]
  inactive_signals?: string[]
  sides: [EvidenceSide, EvidenceSide]
}

export interface CollectionData {
  version: number
  run_id: string | null
  graph: ClusterRunView | null
  max_upload_bytes: number
  default_profile?: "demo" | "jury"
  max_analysis_upload_bytes?: number
  sources: Record<string, { label: string; date: string }[]>
  imports: Pick<ImportReport, 'id' | 'status' | 'created_at'>[]
}

export interface ImportReport {
  id: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  created_at: string
  run_id: string | null
  entries: { label: string; status: 'added' | 'duplicate' | 'skipped'; material_id?: string; reason?: string }[]
  issues: { label: string; reason: string }[]
  merges: { previous: string[][]; cluster_id: string; material_ids: string[] }[]
  stage?: string
  archive?: { loaded: number; skipped: number; metadata: number }
  upload_ids?: string[]
  first_material?: boolean
  error?: string
}


export interface SupplementalSignal {
  evidence_id: string
  pair: [string, string]
  label: string
  score: number | null
  status: 'missing' | 'scored'
  explanation: string
}

export interface ManagedMaterial {
  id: string
  label: string
  archived: boolean
  analysable: boolean
  source_labels: string[]
}

export interface Upload {
  id: string
  filename: string
  label: string
  size: number | null
  created_at: string
  archived: boolean
  downloadable: boolean
  import_id: string | null
  status: ImportReport['status']
  materials: ManagedMaterial[]
  members: { id: string; path: string; size: number; material_id: string | null }[]
}

export interface MonitorSource { kind: 'json-export'; name: string; available: boolean; synthetic: boolean }
export interface MonitorState { cursor?: string | null; pending?: string[]; totals?: Record<string, number>; last_run?: string | null; last_error?: string | null }
export interface MonitorStatus { source: MonitorSource; state: MonitorState | Record<string, never> }
export interface MonitorScan {
  ok: boolean
  source: MonitorSource
  error?: string | null
  new?: number
  updated?: number
  duplicate?: number
  rejected?: number
  exhausted?: boolean
  payer_groups_touched?: number
  state?: MonitorState
}
