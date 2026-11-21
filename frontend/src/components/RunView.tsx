import { useRunPolling } from '../usePolling'
import type { ClusterRunView, Material, PairEvidenceView } from '../api/types'
import EvidencePanel from './EvidencePanel'

interface Props {
  runId: string
  evidenceId: string | null
  navigate: (path: string) => void
  pollMs?: number
}

export default function RunView({ runId, evidenceId, navigate, pollMs }: Props) {
  const { run, error, connectionLost } = useRunPolling(runId, pollMs)

  return (
    <section aria-labelledby="run-h">
      <h1 id="run-h">Przebieg <code>{runId}</code></h1>
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
      {!run && !error && !connectionLost && <p role="status">Pobieranie stanu przebiegu…</p>}
      {run?.status === 'queued' && <p role="status">Analiza czeka w kolejce…</p>}
      {run?.status === 'running' && (
        <p role="status">
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
        <Result runId={runId} result={run.result} evidenceId={evidenceId} navigate={navigate} />
      )}
      <p><a href="/" onClick={(e) => { e.preventDefault(); navigate('/') }}>Nowa analiza</a></p>
    </section>
  )
}

function Result({ runId, result, evidenceId, navigate }: { runId: string; result: ClusterRunView; evidenceId: string | null; navigate: (p: string) => void }) {
  const byId = new Map(result.materials.map((m) => [m.material_id, m]))
  const label = (id: string) => byId.get(id)?.label ?? id
  const { summary } = result

  return (
    <>
      <div className={`summary ${summary.outcome}`} data-testid="summary">
        <h2>
          {summary.outcome === 'grouped' && 'Znaleziono grupy'}
          {summary.outcome === 'no_groups' && 'Nie znaleziono grup'}
          {summary.outcome === 'insufficient_evidence' && 'Za mało danych do rozstrzygnięcia'}
        </h2>
        <p>{summary.message}</p>
        <p className="muted">
          Materiały: {summary.material_count} · w grupach: {summary.grouped} · pojedyncze: {summary.singleton} · szum: {summary.noise} · za mało danych: {summary.insufficient_data}
        </p>
      </div>

      <h2>Grupy</h2>
      {result.clusters.length === 0 ? (
        <p>Brak grup w tym wyniku.</p>
      ) : (
        result.clusters.map((c) => (
          <article key={c.cluster_id} className="card" aria-label={`Grupa ${c.cluster_id}`}>
            <h3>Grupa {c.cluster_id}</h3>
            {c.summary && <p>{c.summary}</p>}
            <ul>{c.material_ids.map((id) => <li key={id}>{label(id)}</li>)}</ul>
          </article>
        ))
      )}

      <h2>Porównywane materiały</h2>
      <ul className="materials">
        {result.materials.map((m) => <MaterialCard key={m.material_id} m={m} />)}
      </ul>

      <h2>Dowody</h2>
      {evidenceId && (
        <EvidencePanel key={evidenceId} runId={runId} evidenceId={evidenceId}
          onClose={() => navigate(`/runs/${encodeURIComponent(runId)}`)} />
      )}
      {result.pair_evidence.length === 0 ? (
        <p>Brak dowodów do wyświetlenia.</p>
      ) : (
        <ul className="pairs">
          {result.pair_evidence.map((p) => (
            <PairRow key={p.evidence_id} p={p} label={label} selected={p.evidence_id === evidenceId}
              onOpen={() => navigate(`/runs/${encodeURIComponent(runId)}/evidence/${encodeURIComponent(p.evidence_id)}`)} />
          ))}
        </ul>
      )}
    </>
  )
}

function MaterialCard({ m }: { m: Material }) {
  return (
    <li className="material">
      {m.thumbnail && <img src={m.thumbnail.href} alt={m.thumbnail.alt ?? m.label} width={120} />}
      <div>
        <strong>{m.label}</strong>
        {m.display_url && <div className="muted">{m.display_url}</div>}
        <div className="muted">
          {m.available_evidence && m.available_evidence.length > 0
            ? `Dostępne dowody: ${m.available_evidence.join(', ')}`
            : m.input_kind === 'url_only' ? 'Tylko adres URL, brak dowodów' : 'Brak dostępnych dowodów'}
        </div>
      </div>
    </li>
  )
}

function PairRow({ p, label, selected, onOpen }: { p: PairEvidenceView; label: (id: string) => string; selected: boolean; onOpen: () => void }) {
  return (
    <li className={selected ? 'pair selected' : 'pair'}>
      <div>
        <strong>{label(p.pair[0])}</strong> ↔ <strong>{label(p.pair[1])}</strong>
        <div>{p.explanation}</div>
        <div className="muted">
          {p.score == null ? 'wynik: niewykonalne' : `wynik: ${p.score.toFixed(2)}`} · metoda {p.method_id}
        </div>
      </div>
      <button type="button" onClick={onOpen} aria-label={`Pokaż dowody: ${label(p.pair[0])} i ${label(p.pair[1])}`}>
        Pokaż dowody
      </button>
    </li>
  )
}
