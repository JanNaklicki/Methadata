/**
 * Adresy aplikacji. Cały stan widoku, który ma przetrwać „wstecz” i odświeżenie, żyje w adresie:
 *
 *   /                                         nowa analiza
 *   /runs/:id[/evidence/:evidenceId]          wynik analizy (?widok=graf przełącza listę na graf)
 *   /collection                               baza materiałów
 *   /collection/{graph|evidence|timeline|files}
 *       ?group=…  grupa        ?material=…  wybrany materiał   ?evidence=…  para dowodu
 *       ?graph=artifacts  graf wspólnych śladów        ?import=…  otwarty raport importu
 *       ?q=…&domain=…&period=…&min=…  filtry bazy
 */

export type CollectionView = 'explorer' | 'graph' | 'evidence' | 'timeline' | 'library'

export const collectionSegments: Record<CollectionView, string> = {
  explorer: '', graph: 'graph', evidence: 'evidence', timeline: 'timeline', library: 'files',
}

export interface Route {
  runId: string | null
  evidenceId: string | null
  collection?: boolean
  view?: CollectionView
  params: URLSearchParams
  /** @deprecated zachowane dla zgodności: numer raportu importu z adresu. */
  importId?: string
}

export function parseRoute(path: string): Route {
  const url = new URL(path, 'http://app.local')
  const params = url.searchParams
  const pathname = url.pathname
  const run = pathname.match(/^\/runs\/([^/]+)(?:\/evidence\/([^/]+))?\/?$/)
  const legacyImport = pathname.match(/^\/collection\/imports\/([a-f0-9]+)\/?$/)
  if (legacyImport) {
    params.set('import', legacyImport[1])
    return { runId: null, evidenceId: null, collection: true, view: 'explorer', params, importId: legacyImport[1] }
  }
  const collection = pathname.match(/^\/collection(?:\/(graph|evidence|timeline|files))?\/?$/)
  if (collection) {
    const view = (Object.entries(collectionSegments).find(([, segment]) => segment === (collection[1] ?? '')) ?? ['explorer'])[0] as CollectionView
    return { runId: null, evidenceId: null, collection: true, view, params, ...(params.get('import') ? { importId: params.get('import')! } : {}) }
  }
  if (!run) return { runId: null, evidenceId: null, params }
  return { runId: decodeURIComponent(run[1]), evidenceId: run[2] ? decodeURIComponent(run[2]) : null, params }
}

function withQuery(path: string, params: URLSearchParams) {
  const query = params.toString()
  return query ? `${path}?${query}` : path
}

export function collectionPath(view: CollectionView, params: URLSearchParams = new URLSearchParams()) {
  const segment = collectionSegments[view]
  return withQuery(`/collection${segment ? `/${segment}` : ''}`, params)
}

export function runPath(runId: string, evidenceId: string | null, params: URLSearchParams = new URLSearchParams()) {
  const base = `/runs/${encodeURIComponent(runId)}${evidenceId ? `/evidence/${encodeURIComponent(evidenceId)}` : ''}`
  return withQuery(base, params)
}

/** Kopia parametrów ze zmianami; `null` albo pusty tekst usuwa parametr. */
export function withParams(params: URLSearchParams, changes: Record<string, string | null | undefined>) {
  const next = new URLSearchParams(params)
  for (const [key, value] of Object.entries(changes)) {
    if (value) next.set(key, value)
    else next.delete(key)
  }
  return next
}

export type Navigate = (path: string, options?: { replace?: boolean }) => void

/** Zwykłe kliknięcie przechodzi wewnątrz aplikacji; kliknięcie z modyfikatorem zostaje przeglądarce. */
export function linkClick(navigate: Navigate, path: string) {
  return (event: React.MouseEvent<HTMLAnchorElement>) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    event.preventDefault()
    navigate(path)
  }
}
