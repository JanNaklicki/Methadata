import { formatScore } from './formatScore'
import { useMemo, useState } from 'react'
import type { ClusterRunView, Evidence, PairEvidenceView, Preview } from '../api/types'
import { CLUSTER_PALETTE } from './graphData'
import { familyLabels } from './SignalBreakdown'
import { usePairEvidence } from './usePairEvidence'
import { downloadJson } from './downloadJson'
import WorkspaceIcon from './WorkspaceIcon'

interface TimelineEvent {
  id: string
  label: string
  type: string
  date: string
  source?: string
  thumbnail?: Preview | null
}
interface Family { weight: number; origin: string; support_min: number }
interface Fusion {
  families: Record<string, Family>
  threshold: number
  coverage_min: number
  min_support: number
}

const DATA_EVENT_TYPE = 'Zapis materiału'

function fusionConfig(result: ClusterRunView) {
  const config = result.cluster_run.config as { fusion?: Fusion } | undefined
  const fusion = config?.fusion
  return fusion?.families && Object.keys(fusion.families).length > 0 ? fusion : undefined
}

function simulate(config: Fusion, evidence: Evidence, weights: Record<string, number>, threshold: number) {
  const scored = new Map(evidence.families?.map((item) => [item.name, item.score]))
  const origins = new Map<string, { weight: number; scoreWeight: number; weighted: number; support: number }>()
  for (const [name, family] of Object.entries(config.families)) {
    const weight = weights[name] ?? family.weight
    const origin = origins.get(family.origin) ?? { weight: 0, scoreWeight: 0, weighted: 0, support: 1 }
    const score = scored.get(name)
    if (score != null) {
      origin.scoreWeight += weight
      origin.weighted += score * weight
    }
    origin.weight = Math.max(weight, origin.weight)
    origin.support = Math.min(family.support_min, origin.support)
    origins.set(family.origin, origin)
  }
  let available = 0
  let total = 0
  let weighted = 0
  let support = 0
  for (const origin of origins.values()) {
    total += origin.weight
    if (!origin.scoreWeight) continue
    const score = origin.weighted / origin.scoreWeight
    weighted += score * origin.weight
    available += origin.weight
    if (score >= origin.support) support += 1
  }
  const score = available ? Math.round(weighted / available * 1e12) / 1e12 : null
  const coverage = total ? available / total : 0
  return { score, coverage, support, accepted: score !== null && score >= threshold &&
    coverage + 1e-12 >= config.coverage_min && support >= config.min_support }
}

const date = new Intl.DateTimeFormat('pl-PL', { dateStyle: 'medium', timeStyle: 'short' })
const timeOnly = new Intl.DateTimeFormat('pl-PL', { timeStyle: 'short' })
const day = new Intl.DateTimeFormat('pl-PL', { dateStyle: 'medium' })

export default function TimelineEngine({ events, result, runId, links, onMaterial }: {
  events: TimelineEvent[]
  result: ClusterRunView
  runId?: string | null
  links: PairEvidenceView[]
  onMaterial: (id: string) => void
}) {
  const validEvents = useMemo(
    () => events
      .map((event) => ({ ...event, ts: Date.parse(event.date) }))
      .filter((event) => Number.isFinite(event.ts)),
    [events],
  )
  const kinds = useMemo(() => [...new Set(validEvents.map((event) => event.type))], [validEvents])
  const hasDataDates = kinds.includes(DATA_EVENT_TYPE)
  // Default to actual data capture dates when available instead of mixing with upload timestamps.
  const [kind, setKind] = useState<string | null>(null)
  const [order, setOrder] = useState<'asc' | 'desc'>('asc')
  const activeKind = kind === null
    ? (hasDataDates ? DATA_EVENT_TYPE : '')
    : (kind === '' || kinds.includes(kind) ? kind : '')

  const clusterInfo = useMemo(() => {
    const map = new Map<string, { label: string; color: string }>()
    result.clusters.forEach((cluster, index) => {
      const color = CLUSTER_PALETTE[index % CLUSTER_PALETTE.length]
      const label = `Grupa ${cluster.index ?? index + 1}`
      cluster.material_ids.forEach((id) => map.set(id, { label, color }))
    })
    return map
  }, [result.clusters])

  const shown = useMemo(() => {
    const filtered = activeKind ? validEvents.filter((event) => event.type === activeKind) : validEvents
    return [...filtered].sort((a, b) => (order === 'asc' ? a.ts - b.ts : b.ts - a.ts))
  }, [activeKind, order, validEvents])

  const bounds = useMemo(() => {
    if (!shown.length) return null
    let min = Infinity
    let max = -Infinity
    for (const ev of shown) {
      if (ev.ts < min) min = ev.ts
      if (ev.ts > max) max = ev.ts
    }
    return { min, max, span: max - min }
  }, [shown])

  const groupedByDay = useMemo(() => {
    const groups: { dayKey: string; dayLabel: string; items: typeof shown }[] = []
    const index = new Map<string, number>()
    for (const ev of shown) {
      const d = new Date(ev.ts)
      const dayKey = `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`
      const existing = index.get(dayKey)
      if (existing !== undefined) {
        groups[existing].items.push(ev)
      } else {
        index.set(dayKey, groups.length)
        groups.push({ dayKey, dayLabel: day.format(d), items: [ev] })
      }
    }
    return groups
  }, [shown])

  const config = fusionConfig(result)

  return <div className="timeline-layout">
    <section className="timeline" aria-labelledby="timeline-h">
      <div className="section-heading">
        <div>
          <h2 id="timeline-h">Oś czasu</h2>
          <p className="muted">
            {activeKind === DATA_EVENT_TYPE
              ? 'Chronologia zarejestrowanych dat materiałów (zapisy stron, HAR, zrzuty).'
              : activeKind
                ? `Zdarzenia typu: ${activeKind}.`
                : 'Wszystkie dostępne daty materiałów i importów.'}
          </p>
        </div>
        <div className="timeline-controls">
          <label>Pokaż <select value={activeKind} onChange={(event) => setKind(event.target.value)}>
            <option value="">Wszystkie źródła</option>
            {kinds.map((type) => <option key={type} value={type}>{type}</option>)}
          </select></label>
          <button
            type="button"
            className="outline"
            onClick={() => setOrder((prev) => prev === 'asc' ? 'desc' : 'asc')}
          >
            {order === 'asc' ? 'Od najstarszych ↑' : 'Od najnowszych ↓'}
          </button>
        </div>
      </div>

      {bounds && <>
        <p className="timeline-range muted">
          {day.formatRange(new Date(bounds.min), new Date(bounds.max))} · {shown.length} {plural(shown.length, 'zdarzenie', 'zdarzenia', 'zdarzeń')} · {groupedByDay.length} {groupedByDay.length === 1 ? 'dzień' : 'dni'}
        </p>

        <div className="timeline-axis" role="group" aria-label="Rozkład zdarzeń na osi czasu">
          <div className="timeline-track">
            {shown.map((event, idx) => {
              const pct = bounds.span > 0
                ? ((event.ts - bounds.min) / bounds.span) * 100
                : (shown.length > 1 ? (idx / (shown.length - 1)) * 100 : 50)
              const cluster = clusterInfo.get(event.id)
              return <button
                key={JSON.stringify([event.id, event.type, event.date, idx])}
                type="button"
                className="timeline-marker"
                style={{ left: `${pct}%`, backgroundColor: cluster?.color ?? '#7ce2ff' }}
                title={`${event.label}${cluster ? ` (${cluster.label})` : ''} — ${date.format(new Date(event.ts))}`}
                aria-label={`${event.label}, ${date.format(new Date(event.ts))}`}
                onClick={() => onMaterial(event.id)}
              />
            })}
          </div>
          <div className="timeline-axis-ticks">
            <span>{day.format(new Date(bounds.min))}</span>
            {bounds.span > 86_400_000 && <span>{day.format(new Date(bounds.min + bounds.span / 2))}</span>}
            {day.format(new Date(bounds.min)) !== day.format(new Date(bounds.max)) && <span>{day.format(new Date(bounds.max))}</span>}
          </div>
        </div>
      </>}

      {groupedByDay.length ? <div className="timeline-days">
        {groupedByDay.map((group) => <div key={group.dayKey} className="timeline-day-group">
          <h3 className="timeline-day-header">{group.dayLabel} <span>{group.items.length}</span></h3>
          <ol>{group.items.map((event, index) => {
            const cluster = clusterInfo.get(event.id)
            return <li key={JSON.stringify([event.id, event.type, event.date, index])}>
              <div
                className={`timeline-dot${event.thumbnail ? ' capture-dot' : ''}`}
                style={cluster ? { borderColor: cluster.color } : undefined}
                aria-hidden="true"
              />
              <div className="timeline-event">
                <div className="timeline-event-heading">
                  <span className="timeline-badges">
                    <strong>{event.type}</strong>
                    {cluster && <span className="timeline-cluster-pill" style={{ borderColor: cluster.color, color: cluster.color }}>
                      {cluster.label}
                    </span>}
                  </span>
                  <time dateTime={event.date} title={date.format(new Date(event.ts))}>{timeOnly.format(new Date(event.ts))}</time>
                </div>
                <button type="button" className="text-button" onClick={() => onMaterial(event.id)}>{event.label}</button>
                {event.source && event.source !== event.label && <p className="muted">{event.source}</p>}
                {event.thumbnail && <img src={event.thumbnail.href} alt={event.thumbnail.alt ?? ''} loading="lazy" />}
              </div>
            </li>
          })}</ol>
        </div>)}
      </div> : <p className="workbench-empty">Brak zapisanych dat dla wybranych materiałów.</p>}
    </section>
    <section className="engine-workspace" aria-labelledby="engine-h">
      <div className="section-heading"><h2 id="engine-h">Symulator punktacji</h2></div>
      {config && runId && links.length > 0
        ? <ScoreEngine key={runId} config={config} runId={runId} links={links} result={result} />
        : <p className="empty-state">{links.length ? 'Brak zapisanej konfiguracji fuzji dla tego przebiegu.' :
          'W wybranym zakresie nie ma zapisanych powiązań do symulacji.'}</p>}
    </section>
  </div>
}

function ScoreEngine({ config, runId, links, result }: {
  config: Fusion; runId: string; links: PairEvidenceView[]; result: ClusterRunView
}) {
  const [selected, setSelected] = useState('')
  const [weights, setWeights] = useState<Record<string, number>>({})
  const [minimum, setMinimum] = useState<number>()
  const pair = links.find((link) => link.evidence_id === selected) ?? links[0]
  const id = pair.evidence_id
  const threshold = minimum ?? config.threshold
  const labels = new Map(result.materials.map((material) => [material.material_id, material.label]))

  const { evidence, error, retry } = usePairEvidence(runId, id)

  const preview = evidence?.evidence_id === id && evidence.families?.length
    ? simulate(config, evidence, weights, threshold) : undefined

  function download() {
    const draft = {
      ...config, threshold, dataset_version: result.cluster_run.dataset_version,
      families: Object.fromEntries(Object.entries(config.families)
        .map(([name, family]) => [name, { ...family, weight: weights[name] ?? family.weight }])),
    }
    downloadJson('methadata-fusion.json', draft)
  }

  return <>
    <p className="muted">Zmień wagi i próg dla pary z zapisanym powiązaniem. Zmiany dotyczą tylko symulacji.</p>
    <label className="engine-pair">Porównywana para
      <select value={id} onChange={(event) => setSelected(event.target.value)}>
        {links.map((link) => <option key={link.evidence_id} value={link.evidence_id}>
          {link.pair.map((item) => labels.get(item) ?? item).join(' ↔ ')}
        </option>)}
      </select>
    </label>
    <div className="engine-controls">
      {Object.entries(config.families).map(([name, family]) => {
        const weight = weights[name] ?? family.weight
        return <label key={name}>{familyLabels[name] ?? name}<output>{weight.toLocaleString('pl-PL')}</output>
          <input type="range" min={0.01} max={Math.max(1, family.weight * 2)} step="any" value={weight}
            onChange={(event) => setWeights({ ...weights, [name]: Math.round(event.target.valueAsNumber * 100) / 100 })} />
        </label>
      })}
      <label>Próg podobieństwa<output>{formatScore(threshold)}</output>
        <input type="range" min={0.01} max={1} step="any" value={threshold}
          onChange={(event) => setMinimum(Math.round(event.target.valueAsNumber * 100) / 100)} />
      </label>
    </div>
    <section className="engine-comparison" aria-labelledby="engine-comparison-h" aria-live="polite">
      <h3 id="engine-comparison-h">Porównanie wyników</h3>
      <div className="engine-comparison-grid">
        <ScoreReading label="Zapisany wynik" score={pair.score} />
        <ScoreReading label="Podobieństwo po zmianach" score={preview?.score ?? null} />
      </div>
      {preview && <dl className="engine-results">
        <div><dt>Pokrycie danych</dt><dd>{formatScore(preview.coverage)} (wymagane {formatScore(config.coverage_min)})</dd></div>
        <div><dt>Rodziny śladów wspierające</dt><dd>{preview.support} (wymagane {config.min_support})</dd></div>
        <div><dt>Wynik symulacji</dt><dd>{preview.accepted ? 'Materiały zostałyby połączone' : 'Materiały nie zostałyby połączone'}</dd></div>
      </dl>}
    </section>
    {!preview && (error
      ? <p role="alert">Nie udało się pobrać sygnałów. <button className="outline" type="button" onClick={retry}>Ponów</button></p>
      : <p role="status" className="muted">{evidence ? 'Brak danych o sygnałach tej pary.' : 'Wczytywanie sygnałów…'}</p>)}
    <div className="upload-actions">
      <button type="button" className="outline" onClick={() => { setWeights({}); setMinimum(undefined) }}>Przywróć wagi i próg</button>
      <button type="button" className="icon-button" onClick={download}><WorkspaceIcon name="download" /> Pobierz konfigurację</button>
    </div>
  </>
}

function ScoreReading({ label, score }: { label: string; score: number | null }) {
  return <div className="engine-reading">
    <span>{label}</span>
    <strong>{score === null ? '—' : formatScore(score)}</strong>
    {score !== null && <meter min={0} max={1} value={score} aria-label={label} />}
  </div>
}

function plural(n: number, one: string, few: string, many: string) {
  if (n === 1) return one
  const last = n % 10, tens = n % 100
  return last >= 2 && last <= 4 && (tens < 12 || tens > 14) ? few : many
}
