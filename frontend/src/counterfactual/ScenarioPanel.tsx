import { useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { ScenarioComparison, ScenarioEdgeChange, ScenarioMetrics, ScenarioVariant } from './types'

const scoreFormatter = new Intl.NumberFormat('pl-PL', { maximumFractionDigits: 3 })
const percentFormatter = new Intl.NumberFormat('pl-PL', { style: 'percent', maximumFractionDigits: 1 })
const familyNames: Record<string, string> = { asset: 'Grafiki', dom: 'Struktura strony (DOM)', text: 'Tekst' }
const familyName = (family: string) => familyNames[family] ?? family
const score = (value: number | null) => value == null ? 'niewykonalny' : scoreFormatter.format(value)
const percent = (value: number) => percentFormatter.format(value)

const edgeReasons: Record<ScenarioEdgeChange['kind'], string> = {
  utrata_pokrycia: 'Utrata pokrycia',
  utrata_wsparcia: 'Za mało niezależnych źródeł',
  spadek_podobienstwa: 'Spadek podobieństwa poniżej progu',
  nowa_krawedz: 'Nowe bezpośrednie połączenie',
  zmiana_parametrow: 'Połączenie zachowane ze zmienionymi parametrami',
}

type Label = (id: string) => string

export default function ScenarioPanel({ runId, evidenceId, label }: {
  runId: string; evidenceId: string; label: Label
}) {
  const [materialId, setMaterialId] = useState<string>()
  const [disabled, setDisabled] = useState<string[]>([])
  const [baseline, setBaseline] = useState<ScenarioComparison | null>(null)
  const [response, setResponse] = useState<ScenarioComparison | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const ctrl = new AbortController()
    api.getScenario(runId, evidenceId, disabled, materialId, ctrl.signal).then((result) => {
      if (ctrl.signal.aborted) return
      if (result.disabled.length === 0) setBaseline(result)
      setResponse(result)
    }, (e: unknown) => {
      if (!ctrl.signal.aborted) setError(e instanceof ApiError ? e.message : 'Spróbuj ponownie.')
    })
    return () => ctrl.abort()
  }, [runId, evidenceId, disabled, materialId, attempt])

  function choose(mask: string[]) {
    setResponse(null)
    setError(null)
    setDisabled(mask.sort())
  }

  const data = baseline && (!materialId || baseline.target_id === materialId) ? baseline : response
  const comparison = response && disabled.length > 0 ? response : null

  return (
    <section className="scenarios" aria-labelledby="scenario-heading">
      <h3 id="scenario-heading">Wpływ rodzin śladów</h3>
      {baseline && (
        <label className="scenario-material">
          Materiał objęty wyłączeniem
          <select value={materialId ?? baseline.target_id} onChange={(event) => {
            setMaterialId(event.currentTarget.value)
            choose([])
          }}>
            {baseline.pair_ids.map((id) => <option key={id} value={id}>{label(id)}</option>)}
          </select>
        </label>
      )}
      {data && (
        <fieldset>
          <legend>Uwzględniane rodziny</legend>
          <div className="scenario-options">
            {data.families.map((family) => (
              <label key={family}>
                <input type="checkbox" checked={!disabled.includes(family)} onChange={() => choose(
                  disabled.includes(family) ? disabled.filter((name) => name !== family) : [...disabled, family],
                )} />
                {familyName(family)}
              </label>
            ))}
            <button type="button" aria-disabled={disabled.length === 0} onClick={() => {
              if (disabled.length) choose([])
            }}>Przywróć wynik bazowy</button>
          </div>
        </fieldset>
      )}
      <p role="status" className="muted">
        {response ? (response.disabled.length ? 'Pokazano przeliczenie.' : 'Pokazano wynik bazowy.') :
          error ? '' : 'Wczytywanie porównania…'}
      </p>
      {error && (
        <div role="alert" className="error">
          <p>{error}</p>
          <button type="button" onClick={() => { setError(null); setAttempt(attempt + 1) }}>Ponów porównanie</button>
        </div>
      )}
      {data && (
        <>
          <div className="scenario-columns">
            <Variant title="Wynik bazowy" variant={data.baseline} label={label} />
            {comparison && <Variant title="Po wyłączeniu" variant={comparison.scenario} label={label} />}
          </div>
          {comparison && <Changes data={comparison} label={label} />}
          <p className="muted">
            Próg podobieństwa {score(data.threshold)} · minimalne pokrycie {percent(data.coverage_min)} ·
            wymagane źródła wsparcia {data.min_support}
          </p>
          <p>
            {data.minimal_masks.length > 0 ? (
              <>Najmniej rodzin do wyłączenia, aby zmienić skład grupy: {data.minimal_masks
                .map((mask) => mask.map(familyName).join(' + ')).join(' lub ')}.</>
            ) : 'Żaden zestaw wyłączeń nie zmienia składu grupy tego materiału.'}
          </p>
        </>
      )}
    </section>
  )
}

function decision(metrics: ScenarioMetrics): string {
  switch (metrics.status) {
    case 'no_evidence': return 'Brak porównywalnych śladów.'
    case 'insufficient_coverage': return 'Pokrycie poniżej minimum.'
    case 'insufficient_support': return 'Za mało niezależnych źródeł wsparcia.'
    case 'ok': return metrics.accepted ? 'Podobieństwo osiąga próg połączenia.' : 'Podobieństwo poniżej progu połączenia.'
  }
}

function Variant({ title, variant, label }: { title: string; variant: ScenarioVariant; label: Label }) {
  const edge = variant.pair
  return (
    <div className="side">
      <h4>{title}</h4>
      <dl className="scenario-metrics">
        <dt>Pokrycie</dt><dd>{percent(edge.coverage)}</dd>
        <dt>Podobieństwo przed progami</dt><dd>{score(edge.fused)}</dd>
        <dt>Wynik decyzji</dt><dd>{score(edge.score)}</dd>
        <dt>Źródła wsparcia</dt><dd>{edge.supporting_origins}</dd>
        <dt>Bezpośrednie połączenie</dt><dd>{edge.accepted ? 'Tak' : 'Nie'}</dd>
        <dt>Przyczyna</dt><dd>{decision(edge)}</dd>
        <dt>Grupa</dt><dd>{variant.assignment_status === 'grouped' ? variant.group.map(label).join(', ') :
          variant.assignment_status === 'insufficient_data' ? 'Za mało danych' : 'Poza grupą'}</dd>
      </dl>
      {variant.indirect_paths.length > 0 && (
        <>
          <h5>Ścieżki pośrednie</h5>
          {variant.indirect_paths.map(({ member_id, hops }) => (
            <ul className="scenario-hops" key={member_id} aria-label={`Ścieżka do ${label(member_id)}`}>
              {hops.map((hop) => (
                <li key={hop.to_id}>{label(hop.from_id)} → {label(hop.to_id)} · {score(hop.score)}
                  <span className="muted"> · {hop.families.map(familyName).join(', ')}</span>
                </li>
              ))}
            </ul>
          ))}
        </>
      )}
    </div>
  )
}

function Changes({ data, label }: { data: ScenarioComparison; label: Label }) {
  const before = new Set(data.baseline.group)
  const after = new Set(data.scenario.group)
  const lost = data.baseline.group.filter((id) => !after.has(id))
  const gained = data.scenario.group.filter((id) => !before.has(id))
  return (
    <>
      <h4>Zmiany w grafie</h4>
      {lost.length > 0 && <p>Odłączono od grupy: {lost.map(label).join(', ')}.</p>}
      {gained.length > 0 && <p>Dołączono do grupy: {gained.map(label).join(', ')}.</p>}
      {lost.length === 0 && gained.length === 0 && <p>Skład grupy bez zmian.</p>}
      {data.edge_changes.length > 0 && (
        <ul>
          {data.edge_changes.map(({ pair_ids, kind }) => (
            <li key={pair_ids.join('/')}>{pair_ids.map(label).join(' ↔ ')} · {edgeReasons[kind]}</li>
          ))}
        </ul>
      )}
      {data.edge_changes.length === 0 && <p>Krawędzie grafu bez zmian.</p>}
    </>
  )
}
