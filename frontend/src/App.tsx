import { useCallback, useEffect, useState } from 'react'
import StartScreen from './components/StartScreen'
import RunView from './components/RunView'

export interface Route { runId: string | null; evidenceId: string | null }

export function parseRoute(path: string): Route {
  const m = path.match(/^\/runs\/([^/]+)(?:\/evidence\/([^/]+))?\/?$/)
  if (!m) return { runId: null, evidenceId: null }
  return { runId: decodeURIComponent(m[1]), evidenceId: m[2] ? decodeURIComponent(m[2]) : null }
}

export default function App({ pollMs }: { pollMs?: number }) {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.pathname))

  useEffect(() => {
    const onPop = () => setRoute(parseRoute(window.location.pathname))
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  const navigate = useCallback((path: string) => {
    window.history.pushState(null, '', path)
    setRoute(parseRoute(path))
  }, [])

  return (
    <div className="app">
      <header className="top">
        <a href="/" onClick={(e) => { e.preventDefault(); navigate('/') }}>Methadata</a>
        <span className="muted">porównywanie materiałów reklamowych</span>
      </header>
      <main>
        {route.runId ? (
          <RunView key={route.runId} runId={route.runId} evidenceId={route.evidenceId} navigate={navigate} pollMs={pollMs} />
        ) : (
          <StartScreen onRun={(runId) => navigate(`/runs/${encodeURIComponent(runId)}`)} />
        )}
      </main>
    </div>
  )
}
