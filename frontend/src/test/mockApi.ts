import { vi } from 'vitest'
import demo from '../../../contracts/http/examples/demo.json'
import accepted from '../../../contracts/http/examples/run_accepted.json'
import queued from '../../../contracts/http/examples/run_queued.json'
import running from '../../../contracts/http/examples/run_running.json'
import succeeded from '../../../contracts/http/examples/run_succeeded_complete.json'
import noEvidence from '../../../contracts/http/examples/run_succeeded_no_evidence.json'
import failed from '../../../contracts/http/examples/run_failed.json'
import createdUrl from '../../../contracts/http/examples/observation_created_url.json'
import createdFile from '../../../contracts/http/examples/observation_created_file.json'
import evidenceFull from '../../../contracts/http/examples/evidence_full.json'
import evidenceUnavailable from '../../../contracts/http/examples/evidence_unavailable.json'
import errValidation from '../../../contracts/http/examples/error_validation.json'
import errRunNotFound from '../../../contracts/http/examples/error_run_not_found.json'
import errStorage from '../../../contracts/http/examples/error_storage_unavailable.json'

export const ex = { demo, accepted, queued, running, succeeded, noEvidence, failed, createdUrl, createdFile, evidenceFull, evidenceUnavailable, errValidation, errRunNotFound, errStorage }

export type Responder = (req: { url: string; method: string; body: BodyInit | null | undefined }) => { status?: number; body: unknown } | 'network'

/** Podstawia fetch odpowiedziami z przykładów W01. Zwraca mock do asercji wywołań. */
export function mockFetch(responder: Responder) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const r = responder({ url: String(input), method: init?.method ?? 'GET', body: init?.body })
    if (r === 'network') throw new TypeError('Failed to fetch')
    return new Response(JSON.stringify(r.body), { status: r.status ?? 200, headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

/** Standardowy backend: kolejne odpowiedzi getRun z listy (ostatnia się powtarza). */
export function standardApi(runs: unknown[] = [ex.succeeded], overrides: Responder = () => 'network') {
  let i = 0
  return mockFetch((req) => {
    const { url, method } = req
    if (url.endsWith('/api/v1/demo')) return { body: ex.demo }
    if (url.endsWith('/api/v1/observations') && method === 'POST') {
      return req.body instanceof FormData ? { status: 201, body: ex.createdFile } : { status: 201, body: ex.createdUrl }
    }
    if (url.endsWith('/api/v1/runs') && method === 'POST') return { status: 202, body: ex.accepted }
    if (/\/api\/v1\/runs\/[^/]+$/.test(url)) return { body: runs[Math.min(i++, runs.length - 1)] }
    if (url.endsWith('/evidence/pev-939f4e5676461047')) return { body: ex.evidenceUnavailable }
    if (/\/evidence\/[^/]+$/.test(url)) return { body: ex.evidenceFull }
    return overrides(req)
  })
}
