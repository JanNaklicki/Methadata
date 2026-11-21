import type { Demo, ErrorBody, Evidence, ObservationCreated, Run, RunAccepted } from './types'

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
  'run_not_found', 'evidence_not_found', 'run_not_ready', 'run_failed', 'storage_unavailable', 'internal_error',
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
  return data as T
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  getDemo: (signal?: AbortSignal) => request<Demo>('/demo', { signal }),
  createObservationUrl: (url: string, label?: string) =>
    request<ObservationCreated>('/observations', json({ url, mode: 'offline', ...(label ? { label } : {}) })),
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
}
