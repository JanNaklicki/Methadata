import { useRef, useState } from 'react'
import { useRunPolling } from '../usePolling'
import type { ClusterRunView, Material, PairEvidenceView } from '../api/types'
import EvidencePanel from './EvidencePanel'
import RelationshipGraph from './RelationshipGraph'
import { familyLabels } from './SignalBreakdown'
import { runPath, withParams, type Navigate } from '../routes'

interface Props {
  runId: string
  evidenceId: string | null
  params: URLSearchParams
  navigate: Navigate
  pollMs?: number
}

export default function RunView({ runId, evidenceId, params, navigate, pollMs }: Props) {
  const { run, error, connectionLost } = useRunPolling(runId, pollMs)

  return (
    <section aria-labelledby="run-h">
      <header className="run-header">
        <h1 id="run-h">Wynik analizy</h1>
        <p className="muted run-id">Numer analizy: <code>{runId}</code></p>
      </header>
      {connectionLost && (
        <div role="alert" className="error">
          Utracono połączenie z serwerem. Ponawiam próby…
        </div>
      )}
      {error && (
        <div role="alert" className="error">
          <strong>Nie można pobrać przebiegu.</strong> {error.message}
        </div>
      )}
      {!run && !error && !connectionLost && <p className="notice" role="status">Pobieranie stanu przebiegu…</p>}
      {run?.status === 'queued' && <p className="notice" role="status">Analiza czeka w kolejce…</p>}
      {run?.status === 'running' && (
        <p className="notice" role="status">
          Analiza w toku: {run.progress.stage}
          {run.progress.fraction != null && <> ({Math.round(run.progress.fraction * 100)}%)</>}
        </p>
      )}
      {run?.status === 'failed' && (
        <div role="alert" className="error">
          <strong>Analiza nie powiodła się.</strong> {run.error.message}
        </div>
      )}
      {run?.status === 'succeeded' && (
        <Result runId={runId} result={run.result} observationId={run.observation_id} evidenceId={evidenceId} params={params} navigate={navigate} />
      )}
    </section>
  )
}

function Result({ runId, result, observationId, evidenceId, params, navigate }: {
  runId: string; result: ClusterRunView; observationId: string; evidenceId: string | null; params: URLSearchParams; navigate: Navigate
}) {
  const mode: 'graph' | 'list' = params.get('widok') === 'graf' ? 'graph' : 'list'
  const setMode = (next: 'graph' | 'list') => navigate(runPath(runId, evidenceId, withParams(params, { widok: next === 'graph' ? 'graf' : null })), { replace: true })
  const opener = useRef<HTMLElement | null>(null)
  const viewToggle = useRef<HTMLDivElement>(null)
  const byId = new Map(result.materials.map((m) => [m.material_id, m]))
  const label = (id: string) => byId.get(id)?.label ?? id
  const { summary } = result
  const pair = result.pair_evidence.find((p) => p.evidence_id === evidenceId)
  const scenario = pair?.method_id === 'fusion-v1' && !result.collection ? { label } : undefined
  const submitted = result.materials.find((m) => m.observation_id === observationId)
  const accepted = new Set(result.cluster_run.edges?.map((edge) => edge.pair_evidence_id))
  const involvesSubmission = (p: PairEvidenceView) => Boolean(submitted && p.pair.includes(submitted.material_id))
  const ranked = [...result.pair_evidence].sort((a, b) =>
    Number(accepted.has(b.evidence_id)) - Number(accepted.has(a.evidence_id)) ||
    Number(involvesSubmission(b)) - Number(involvesSubmission(a)) ||
    (b.score ?? -1) - (a.score ?? -1))
  const primary = ranked.filter((p) => ranked.length <= 18 || accepted.has(p.evidence_id) ||
    (involvesSubmission(p) && p.score !== null))
  const primaryIds = new Set(primary.map((p) => p.evidence_id))
  const remaining = ranked.filter((p) => !primaryIds.has(p.evidence_id))
  const metrics = [
    ['Materiały', summary.material_count],
    ['W grupach', summary.grouped],
    ['Pojedyncze', summary.singleton],
    ['Bez dopasowania', summary.noise],
    ['Za mało danych', summary.insufficient_data],
  ]

  function openEvidence(id: string, button?: HTMLElement) {
    opener.current = button ?? null
    navigate(runPath(runId, id, params))
  }

  function renderPairs(pairs: PairEvidenceView[]) {
    return <ul className="pairs">
      {pairs.map((p) => <PairRow key={p.evidence_id} p={p} label={label} selected={p.evidence_id === evidenceId}
        accepted={accepted.has(p.evidence_id)} onOpen={(button) => openEvidence(p.evidence_id, button)} />)}
    </ul>
  }

  return (
    <>
      <div className={`summary ${summary.outcome}`} data-testid="summary">
        <h2>
          {summary.outcome === 'grouped' && 'Znaleziono grupy'}
          {summary.outcome === 'no_groups' && 'Nie znaleziono grup'}
          {summary.outcome === 'insufficient_evidence' && 'Za mało danych do rozstrzygnięcia'}
        </h2>
        <p>{summary.message}</p>
        <dl className="run-metrics">
          {metrics.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}
        </dl>
      </div>

      <div className="workspace-heading">
        <h2>Powiązania</h2>
        <div ref={viewToggle} className="view-tabs" role="group" aria-label="Widok powiązań">
          <button type="button" aria-pressed={mode === 'list'} onClick={() => setMode('list')}>Lista</button>
          <button type="button" aria-pressed={mode === 'graph'} onClick={() => setMode('graph')}>Graf</button>
        </div>
      </div>
      <div className="evidence-slot">
        {evidenceId && (
          <EvidencePanel key={evidenceId} runId={runId} evidenceId={evidenceId} scenario={scenario}
            onClose={() => {
              navigate(runPath(runId, null, params))
              const target = opener.current?.isConnected ? opener.current : viewToggle.current?.querySelector<HTMLButtonElement>('button[aria-pressed="true"]')
              target?.focus()
            }} />
        )}
      </div>

      {mode === 'graph' ? <RelationshipGraph result={result} highlighted={submitted ? [submitted.material_id] : []}
        onEvidence={openEvidence} /> : (
        <div className="results-layout">
          <section aria-labelledby="pairs-h">
            <h3 id="pairs-h" className="section-title">Porównania</h3>
            {ranked.length === 0 ? <p className="empty-state">Brak dowodów do wyświetlenia.</p> : <>
              {primary.length > 0 ? renderPairs(primary) : <p className="empty-state">Brak porównywalnych par dla zgłoszenia.</p>}
              {remaining.length > 0 && <MorePairs total={remaining.length} render={(count) => renderPairs(remaining.slice(0, count))} />}
            </>}
          </section>

          <div className="result-context">
            <section aria-labelledby="groups-h">
              <h3 id="groups-h" className="section-title">Grupy</h3>
              {result.clusters.length === 0 ? <p className="empty-state">Brak grup w tym wyniku.</p> : (
                result.clusters.map((c, index) => (
                  <article key={c.cluster_id} className="group-record" aria-label={`Grupa ${index + 1}`}>
                    <h4>Grupa {index + 1}</h4>
                    {c.summary && <p className="muted">{c.summary}</p>}
                    <ul>{c.material_ids.map((id) => <li key={id}>{label(id)}</li>)}</ul>
                  </article>
                ))
              )}
            </section>
            <section aria-labelledby="materials-h">
              <h3 id="materials-h" className="section-title">Porównywane materiały</h3>
              <ul className="materials">
                {result.materials.map((m) => <MaterialCard key={m.material_id} m={m} />)}
              </ul>
            </section>
          </div>
        </div>
      )}
    </>
  )
}

const PAGE = 30

function MorePairs({ total, render }: { total: number; render: (count: number) => React.ReactNode }) {
  const [open, setOpen] = useState(false)
  const [count, setCount] = useState(PAGE)
  return <details className="more-pairs" onToggle={(e) => setOpen(e.currentTarget.open)}>
    <summary>Pozostałe porównania ({total})</summary>
    {open && <>
      {render(count)}
      {count < total && <button type="button" className="outline more-button" onClick={() => setCount(count + PAGE)}>
        Pokaż więcej ({Math.min(PAGE, total - count)} z {total - count} pozostałych)
      </button>}
    </>}
  </details>
}

function MaterialCard({ m }: { m: Material }) {
  return (
    <li className="material">
      {m.thumbnail && <img src={m.thumbnail.href} alt={m.thumbnail.alt ?? m.label} width={70} loading="lazy" />}
      <div>
        <strong>{m.label}</strong>
        {m.display_url && <div className="muted">{m.display_url}</div>}
        <div className="muted">
          {m.available_evidence && m.available_evidence.length > 0
            ? `Ślady: ${m.available_evidence.map((family) => familyLabels[family] ?? family).join(', ')}`
            : m.input_kind === 'url_only' ? 'Tylko adres URL, bez śladów do porównania' : 'Brak odczytanych śladów'}
        </div>
      </div>
    </li>
  )
}

function PairRow({ p, label, selected, accepted, onOpen }: {
  p: PairEvidenceView; label: (id: string) => string; selected: boolean; accepted: boolean
  onOpen: (button: HTMLButtonElement) => void
}) {
  return (
    <li className={selected ? 'pair selected' : 'pair'}>
      <div>
        <strong>{label(p.pair[0])}</strong><span className="pair-arrow" aria-hidden="true">↔</span><strong>{label(p.pair[1])}</strong>
        <div className="pair-meta">
          {p.score !== null && <span className="pair-score">Podobieństwo {Math.round(p.score * 100)}%</span>}
          <span className={accepted ? 'edge-status accepted' : 'edge-status'}>
            {accepted ? 'Powiązanie w grupie' : p.score === null ? 'Brak porównywalnych danych' : 'Nie połączone'}
          </span>
        </div>
        <details className="pair-method">
          <summary>Uzasadnienie wyniku</summary>
          <p>{p.explanation}</p>
        </details>
      </div>
      <button type="button" className="outline" onClick={(e) => onOpen(e.currentTarget)}
        aria-label={`Pokaż dowody: ${label(p.pair[0])} i ${label(p.pair[1])}`}>Pokaż dowody</button>
    </li>
  )
}
