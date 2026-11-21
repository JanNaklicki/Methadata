// Typy odpowiadają schematom Scenario* w contracts/http/openapi.yaml.
export interface ScenarioMetrics {
  score: number | null
  fused: number | null
  status: 'ok' | 'no_evidence' | 'insufficient_coverage' | 'insufficient_support'
  coverage: number
  supporting_origins: number
  accepted: boolean
}

interface ScenarioPath {
  member_id: string
  hops: { from_id: string; to_id: string; score: number; families: string[] }[]
}

export interface ScenarioVariant {
  engine_run_id: string
  pair: ScenarioMetrics
  group: string[]
  assignment_status: 'grouped' | 'singleton' | 'insufficient_data'
  indirect_paths: ScenarioPath[]
}

export interface ScenarioEdgeChange {
  pair_ids: [string, string]
  kind: 'utrata_pokrycia' | 'utrata_wsparcia' | 'spadek_podobienstwa' | 'nowa_krawedz' | 'zmiana_parametrow'
  baseline: ScenarioMetrics
  scenario: ScenarioMetrics
}

export interface ScenarioComparison {
  run_id: string
  evidence_id: string
  dataset_version: string
  target_id: string
  pair_ids: [string, string]
  families: string[]
  disabled: string[]
  threshold: number
  coverage_min: number
  min_support: number
  baseline: ScenarioVariant
  scenario: ScenarioVariant
  edge_changes: ScenarioEdgeChange[]
  minimal_masks: string[][]
}
