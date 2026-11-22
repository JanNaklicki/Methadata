import { useCallback, useEffect, useState } from 'react'
import StartScreen from './components/StartScreen'
import RunView from './components/RunView'
import CollectionScreen from './components/CollectionScreen'
import { collectionPath, parseRoute, type Navigate, type Route } from './routes'

export { parseRoute }
export type { Route }

const currentPath = () => window.location.pathname + window.location.search

function titleFor(route: Route) {
  if (route.collection) return 'Baza materiałów · Methadata'
  if (route.runId) return 'Wynik analizy · Methadata'
  return 'Nowa analiza · Methadata'
}

export default function App({ pollMs }: { pollMs?: number }) {
  const [route, setRoute] = useState<Route>(() => parseRoute(currentPath()))

  useEffect(() => {
    const onPop = () => setRoute(parseRoute(currentPath()))
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  useEffect(() => { document.title = titleFor(route) }, [route.collection, route.runId]) // eslint-disable-line react-hooks/exhaustive-deps

  /** Nowe przejście dodaje wpis do historii; `replace` służy zmianom w obrębie tej samej strony (filtry, zakładki). */
  const navigate = useCallback<Navigate>((path, options) => {
    if (path === currentPath()) return
    if (options?.replace) window.history.replaceState(null, '', path)
    else window.history.pushState(null, '', path)
    setRoute(parseRoute(path))
  }, [])

  const newAnalysis = !route.collection && !route.runId

  return (
    <div className="app">
      <a className="skip-link" href="#main">Przejdź do treści</a>
      <header className="top">
        <div className="top-inner">
          <a className="brand" href="/" onClick={(e) => { e.preventDefault(); navigate('/') }}>
            <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
            Methadata
          </a>
          <nav aria-label="Główna nawigacja">
            <a href="/" aria-current={newAnalysis ? 'page' : undefined}
              onClick={(e) => { e.preventDefault(); navigate('/') }}>Nowa analiza</a>
            <a href="/collection" aria-current={route.collection ? 'page' : undefined}
              onClick={(e) => { e.preventDefault(); navigate('/collection') }}>Baza materiałów</a>
          </nav>
        </div>
      </header>
      <main id="main" tabIndex={-1}>
        {route.collection ? <CollectionScreen view={route.view ?? 'explorer'} params={route.params} navigate={navigate} /> : route.runId ? (
          <RunView key={route.runId} runId={route.runId} evidenceId={route.evidenceId} params={route.params}
            navigate={navigate} pollMs={pollMs} />
        ) : (
          <StartScreen onHint={(query) => navigate(collectionPath('explorer', new URLSearchParams({ q: query })))}
            onCollection={(id) => navigate(id ? `${collectionPath('explorer')}?import=${id}` : '/collection')}
            onRun={(runId) => navigate(`/runs/${encodeURIComponent(runId)}`)} />
        )}
      </main>
    </div>
  )
}
