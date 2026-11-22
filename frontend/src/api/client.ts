import type { ScenarioComparison } from '../counterfactual/types'
import type { MonitorScan, MonitorStatus, SupplementalSignal, CollectionData, ImportReport, Upload, Demo, ErrorBody, Evidence, ObservationCreated, Run, RunAccepted } from './types'

export const API_BASE = '/api/v1'

export class ApiError extends Error {
  readonly code: ErrorBody['code'] | 'network'
  readonly status: number
  readonly details: ErrorBody['details']
  readonly retryable: boolean
  constructor(status: number, body: Omit<Partial<ErrorBody>, 'code'> & { code: ApiError['code']; message: string }) {
    super(body.message)
    this.name = 'ApiError'
    this.status = status
    this.code = body.code
    this.details = body.details
    this.retryable = body.retryable ?? body.code === 'network'
  }
  get isNetwork() { return this.code === 'network' }
}

const KNOWN = new Set([
  'bad_request', 'validation_failed', 'payload_too_large', 'unsupported_media_type', 'observation_not_found',
  'run_not_found', 'evidence_not_found', 'scenario_not_available', 'run_not_ready', 'run_failed', 'storage_unavailable', 'internal_error',
])

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${API_BASE}${path}`, init)
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e
    throw new ApiError(0, { code: 'network', message: 'Utracono połączenie z serwerem. Sprawdź, czy API działa.' })
  }
  let data: unknown = null
  try {
    data = await res.json()
  } catch {
    /* brak treści JSON */
  }
  if (!res.ok) {
    const body = (data as { error?: Partial<ErrorBody> } | null)?.error
    // Nieznany kod traktujemy jak internal_error (zgodnie z kontraktem).
    const code = body?.code && KNOWN.has(body.code) ? body.code : 'internal_error'
    throw new ApiError(res.status, {
      code,
      message: body?.message ?? `Serwer zwrócił błąd (HTTP ${res.status}).`,
      details: body?.details,
      retryable: body?.retryable ?? res.status >= 500,
    })
  }
  if (data === null) throw new ApiError(res.status, { code: "internal_error", message: "API zwróciło nieprawidłową odpowiedź." })
  return data as T
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  getImport: (id: string) => request<ImportReport>(`/imports/${encodeURIComponent(id)}`),
  getCollection: () => request<CollectionData>('/collection', { cache: 'no-store' }),
  getUploads: () => request<Upload[]>('/collection/uploads', { cache: 'no-store' }),
  updateCollectionItem: (kind: 'uploads' | 'materials', id: string, changes: { label?: string; archived?: boolean }) =>
    request(`/collection/${kind}/${encodeURIComponent(id)}`, { ...json(changes), method: 'PATCH' }),
  downloadUpload: (id: string, memberId?: string) =>
    `${API_BASE}/collection/uploads/${encodeURIComponent(id)}${memberId ? `/members/${encodeURIComponent(memberId)}` : ''}/download`,
  retryImport: (id: string) => request<ImportReport>(`/imports/${encodeURIComponent(id)}/retry`, { method: 'POST' }),
  getMaterialSignals: (runId: string, materialId: string, signal: AbortSignal) =>
    request<SupplementalSignal[]>(`/collection/${encodeURIComponent(runId)}/materials/${encodeURIComponent(materialId)}/signals`, { signal }),
  importCollection: (files: FileList | File[]) => {
    const form = new FormData()
    Array.from(files).forEach((file) => form.append('files', file))
    return request<ImportReport>('/imports', { method: 'POST', headers: { Prefer: 'respond-async' }, body: form })
  },
  importCollectionUrl: (url: string, fetch = true) =>
    request<ImportReport>('/imports/url', json({ url, fetch })),
  promoteEdge: (runId: string, evidenceId: string) =>
    request('/collection/edges', json({ run_id: runId, evidence_id: evidenceId })),
  unpromoteEdge: (evidenceId: string) =>
    request(`/collection/edges/${encodeURIComponent(evidenceId)}`, { method: 'DELETE' }),
  getMonitor: () => request<MonitorStatus>('/monitor', { cache: 'no-store' }),
  scanMonitor: () => request<MonitorScan>('/monitor/scan', { method: 'POST' }),
  getDemo: (signal?: AbortSignal) => request<Demo>('/demo', { signal }),
  createObservationUrl: (url: string, label?: string, mode: 'offline' | 'fetch' = 'offline') =>
    request<ObservationCreated>('/observations', json({ url, mode, ...(label ? { label } : {}) })),
  createObservationFile: (file: File, label?: string) => {
    const form = new FormData()
    form.append('file', file)
    if (label) form.append('label', label)
    return request<ObservationCreated>('/observations', { method: 'POST', body: form })
  },
  createRun: (observationId: string, profile?: 'demo' | 'jury') =>
    request<RunAccepted>('/runs', json({ observation_id: observationId, ...(profile ? { profile } : {}) })),
  getRun: (runId: string, signal?: AbortSignal) => request<Run>(`/runs/${encodeURIComponent(runId)}`, { signal }),
  getEvidence: (runId: string, evidenceId: string, signal?: AbortSignal) =>
    request<Evidence>(`/runs/${encodeURIComponent(runId)}/evidence/${encodeURIComponent(evidenceId)}`, { signal }),
  getScenario: (runId: string, evidenceId: string, disabled: string[], materialId?: string, signal?: AbortSignal) => {
    const query = new URLSearchParams()
    disabled.forEach((family) => query.append('disabled', family))
    if (materialId) query.set('material_id', materialId)
    return request<ScenarioComparison>(
      `/runs/${encodeURIComponent(runId)}/evidence/${encodeURIComponent(evidenceId)}/scenarios${query.size ? `?${query}` : ''}`,
      { signal },
    )
  },
}
