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
export interface ClusterView { cluster_id: string; material_ids: string[]; relation: Relation; summary?: string }
export type Outcome = 'grouped' | 'no_groups' | 'insufficient_evidence'
export interface ClusterRunView {
  cluster_run: Record<string, unknown>
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
  sides: [EvidenceSide, EvidenceSide]
}
