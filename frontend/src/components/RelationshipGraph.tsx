import { formatScore } from './formatScore'
import { useEffect, useId, useMemo, useState, type ReactNode } from 'react'
import { api } from '../api/client'
import type { ClusterRunView, SupplementalSignal } from '../api/types'
import GraphCanvas from './GraphCanvas'
import { SUGGESTED_COLOR, buildRelationshipNetwork, edgeOpacity, edgeThickness, focusGroup, matchesRelationship, relationshipColor } from './graphData'

import { familyLabels } from './SignalBreakdown'
import PairSignals from './PairSignals'
import SignalExplanation from './SignalExplanation'

export interface MaterialSource { label: string; date: string }

interface Props {
  result: ClusterRunView
  onEvidence: (id: string, opener?: HTMLElement) => void
  onPromoteEdge?: (evidenceId: string) => Promise<void>
  onUnpromoteEdge?: (evidenceId: string) => Promise<void>
  sources?: Record<string, MaterialSource[]>
  highlighted?: string[]
  initialMaterial?: string
  collectionRun?: string
  inspector?: ReactNode
}

export default function RelationshipGraph({
  result, onEvidence, onPromoteEdge, onUnpromoteEdge, sources, highlighted, initialMaterial, collectionRun, inspector,
}: Props) {
  const [promoting, setPromoting] = useState('')
  const manualEdgeIds = useMemo(() => new Set(result.investigation?.manual_edge_ids ?? []), [result.investigation?.manual_edge_ids])
  const phylo = result.investigation?.phylofake
  const patientZeroByMaterial = useMemo(() => {
    const map = new Map<string, { reason: string; mutation: string }>()
    for (const c of Object.values(phylo?.clusters ?? {})) {
      map.set(c.patient_zero, { reason: c.patient_zero_reason, mutation: c.dominant_mutation_label })
    }
    return map
  }, [phylo])
  const [query, setQuery] = useState('')
  const [group, setGroup] = useState('')
  const [families, setFamilies] = useState<string[]>([])
  const [threshold, setThreshold] = useState(0)
  const thresholdId = useId()
  const [selected, setSelected] = useState(initialMaterial ?? '')
  const [selectedEdge, setSelectedEdge] = useState('')
  const [showSuggested, setShowSuggested] = useState(true)
  const [fitRequest, setFitRequest] = useState(0)
  const [signals, setSignals] = useState<SupplementalSignal[]>()
  const [signalsError, setSignalsError] = useState(false)
  useEffect(() => {
    setSignals(undefined)
    setSignalsError(false)
    if (!selected || !collectionRun) return
    const request = new AbortController()
    api.getMaterialSignals(collectionRun, selected, request.signal).then(setSignals, () => {
      if (!request.signal.aborted) setSignalsError(true)
    })
    return () => request.abort()
  }, [collectionRun, selected])
  const { graph: network, groups, relationships, suggested, ungrouped } = useMemo(() => buildRelationshipNetwork(result), [result])
  const graph = useMemo(() => {
    const ids = groups.get(group)
    return ids ? focusGroup(network, ids) : network
  }, [group, groups, network])
  const materials = useMemo(() => new Map(result.materials.map((m) => [m.material_id, m])), [result.materials])
  const assignments = new Map(result.cluster_run.assignments?.map((a) => [a.material_id, a.status]))
  const edgeFamilies = result.investigation?.edge_families
  const scopedEdges = useMemo(() => relationships.filter((edge) => graph.hasEdge(edge.pair_evidence_id)), [graph, relationships])
  const visibleEdges = useMemo(() => scopedEdges.filter((edge) => matchesRelationship(edge, threshold, families)),
    [families, scopedEdges, threshold])
  const scopedSuggested = useMemo(() => suggested.filter((edge) => graph.hasEdge(edge.pair_evidence_id)), [graph, suggested])
  const visibleSuggested = useMemo(
    () => (showSuggested && families.length === 0
      ? scopedSuggested.filter((edge) => edge.score >= threshold / 100)
      : []),
    [families.length, scopedSuggested, showSuggested, threshold],
  )
  const normalizedQuery = query.trim().toLocaleLowerCase()
  const domains = result.investigation?.domains
  const matchingIds = useMemo(() => new Set(graph.filterNodes((id) => {
    const material = materials.get(id)
    return [id, material?.label, material?.display_url, ...(domains?.[id] ?? [])]
      .some((value) => value?.toLocaleLowerCase().includes(normalizedQuery))
  })), [domains, graph, materials, normalizedQuery])
  const current = graph.hasNode(selected) ? materials.get(selected) : undefined
  const edge = visibleEdges.find((item) => item.pair_evidence_id === selectedEdge)
  const suggestedEdge = visibleSuggested.find((item) => item.pair_evidence_id === selectedEdge)
  const pair = edge && result.pair_evidence.find((item) => item.evidence_id === edge.pair_evidence_id)
  const neighbours = visibleEdges.filter((e) => e.source === selected || e.target === selected)

  useEffect(() => {
    const recent = new Set(highlighted)
    const adjacent = new Set([
      ...visibleEdges.filter((e) => e.source === selected || e.target === selected).flatMap((e) => [e.source, e.target]),
      ...visibleSuggested.filter((e) => e.source === selected || e.target === selected).flatMap((e) => [e.source, e.target]),
    ])
    const permitted = new Map(visibleEdges.map((e) => [e.pair_evidence_id, e]))
    const permittedSuggested = new Map(visibleSuggested.map((e) => [e.pair_evidence_id, e]))
    const hasSelectedEdge = Boolean(pair || suggestedEdge)
    graph.updateEachNodeAttributes((id, attrs) => {
      const base = attrs.baseSize ?? (ungrouped.has(id) ? 10 : 16)
      const isRoot = patientZeroByMaterial.has(id)
      return {
        ...attrs,
        selected: id === selected,
        emphasized: recent.has(id) || isRoot,
        muted: id !== selected && (!matchingIds.has(id) || Boolean(current && !adjacent.has(id))),
        size: id === selected ? base + 4 : recent.has(id) ? base + 3 : base,
      }
    })
    graph.updateEachEdgeAttributes((key, attrs, source, target) => {
      const accepted = permitted.get(key)
      if (accepted) {
        const isSelected = accepted.pair_evidence_id === selectedEdge
        const isIncident = Boolean(current && (source === selected || target === selected))
        const baseThickness = edgeThickness(accepted.score)
        const color = relationshipColor(accepted, families)
        return {
          ...attrs,
          hidden: false,
          selected: isSelected,
          showLabel: isSelected || isIncident,
          size: isSelected ? Math.max(6, baseThickness + 2) : baseThickness,
          opacity: isSelected ? 1 : edgeOpacity(accepted.score),
          muted: Boolean(hasSelectedEdge ? key !== selectedEdge : current && !isIncident) ||
            (!matchingIds.has(source) && !matchingIds.has(target)),
          color,
          plainColor: color,
          dashSize: 0,
          gapSize: 0,
        }
      }
      const cand = permittedSuggested.get(key)
      if (cand) {
        const isSelected = cand.pair_evidence_id === selectedEdge
        const isIncident = Boolean(current && (source === selected || target === selected))
        const baseThickness = Math.min(3.4, Math.max(1.8, edgeThickness(cand.score) * 0.55))
        return {
          ...attrs,
          hidden: false,
          selected: isSelected,
          showLabel: isSelected || isIncident,
          size: isSelected ? 4.2 : baseThickness,
          opacity: isSelected ? 1 : 0.72,
          muted: Boolean(hasSelectedEdge ? key !== selectedEdge : current && !isIncident) ||
            (!matchingIds.has(source) && !matchingIds.has(target)),
          color: SUGGESTED_COLOR,
          plainColor: '#00000000',
          dashSize: 7,
          gapSize: 6,
        }
      }
      return { ...attrs, hidden: true, selected: false, showLabel: false }
    })
  }, [current, families, graph, highlighted, matchingIds, pair, selected, selectedEdge, suggestedEdge, ungrouped, visibleEdges, visibleSuggested])

  function selectMaterial(id: string) {
    setSelected(id)
    setSelectedEdge('')
  }

  const describe = (id: string) => {
    if (result.materials.length === 1) return 'Pierwszy materiał w bazie'
    if (assignments.get(id) === 'insufficient_data') {
      const missing = Object.keys(familyLabels).filter((f) => !materials.get(id)?.available_evidence?.includes(f))
      return missing.length ? 'Za mało danych — brak: ' + missing.map((f) => familyLabels[f]).join(', ')
        : 'Za mało danych do porównania'
    }
    return 'Bez powiązań'
  }

  return <section className="graph-workspace" aria-label="Graf powiązań">
    <div className="graph-tools">
      <label>Szukaj materiału<input type="search" value={query} onChange={(e) => setQuery(e.target.value)} /></label>
      {!collectionRun && <label>Grupa<select value={group} onChange={(e) => { setGroup(e.target.value); selectMaterial('') }}>
        <option value="">Wszystkie grupy</option>
        {result.clusters.map((c, i) => <option key={c.cluster_id} value={c.cluster_id}>Grupa {c.index ?? i + 1}</option>)}
      </select></label>}
      <button type="button" className="outline" onClick={() => {
        setGroup(''); setQuery(''); setFamilies([]); setThreshold(0); selectMaterial('')
        setFitRequest((value) => value + 1)
      }}>Resetuj widok</button>
    </div>
    <div className="graph-body">
      <div className="graph-area">
        <div className="graph-controls">
          <label htmlFor={thresholdId}>Minimalne podobieństwo <output aria-hidden="true">{threshold}%</output>
            <input id={thresholdId} type="range" min="0" max="100" value={threshold} aria-valuetext={`${threshold}%`}
              onChange={(e) => setThreshold(Number(e.target.value))} />
          </label>
          {edgeFamilies && <fieldset><legend>Typ śladu</legend>
            {Object.entries(familyLabels).map(([id, label]) => <label key={id}>
              <input type="checkbox" checked={families.includes(id)} onChange={(e) => setFamilies(
                e.target.checked ? [...families, id] : families.filter((name) => name !== id))} />
              <i className={`legend-dot ${id}`} />{label}
            </label>)}
          </fieldset>}
        </div>
        <div className="graph-caption">
          <h3>Mapa powiązań</h3>
          <span>
            {graph.order} materiałów · {visibleEdges.length} powiązań
            {visibleSuggested.length > 0 ? ` · ${visibleSuggested.length} sugerowanych` : ''}
          </span>
        </div>
        {graph.order > 0 ? <GraphCanvas
          graph={graph}
          label="Mapa powiązań materiałów"
          fitRequest={fitRequest}
          suggestedCount={scopedSuggested.length}
          showSuggested={showSuggested}
          onToggleSuggested={() => setShowSuggested((v) => !v)}
          onNode={selectMaterial}
          onEdge={collectionRun ? setSelectedEdge : onEvidence}
        >
          {() => <div className="network-list-columns">
            <section aria-label="Materiały w grafie">
              <h4>Materiały</h4>
              <ul>{Array.from(matchingIds, (id) => <li key={id}>
                <button type="button" className="outline" aria-pressed={selected === id} onClick={() => selectMaterial(id)}>
                  {materials.get(id)?.label}
                  {ungrouped.has(id) && <small>{describe(id)}</small>}
                </button>
              </li>)}</ul>
            </section>
            <section aria-label="Powiązania w grafie">
              <h4>Powiązania</h4>
              <ul>{visibleEdges.filter((edge) => matchingIds.has(edge.source) || matchingIds.has(edge.target)).map((edge) => <li key={edge.pair_evidence_id}>
                <button type="button" className="outline" onClick={(event) => {
                    if (collectionRun) setSelectedEdge(edge.pair_evidence_id)
                    else onEvidence(edge.pair_evidence_id, event.currentTarget)
                  }}>
                  {materials.get(edge.source)?.label} ↔ {materials.get(edge.target)?.label} · {formatScore(edge.score)}
                </button>
              </li>)}</ul>
            </section>
          </div>}
        </GraphCanvas> : null}
        {matchingIds.size === 0 && <p className="network-fallback muted" role="status">Żaden materiał nie pasuje do wybranych filtrów.</p>}
      </div>
      <aside className="graph-inspector" aria-label="Szczegóły materiału">
        {inspector || (edge && pair ? <>
          <div className="section-heading"><h3>Dowód powiązania</h3>
            <button type="button" className="outline" onClick={() => setSelectedEdge('')}>Wyczyść</button>
          </div>
          <div className="edge-preview">
            <div className="edge-score"><span>Podobieństwo pary</span><strong>{formatScore(edge.score)}</strong></div>
            <p className="edge-materials">
              {materials.get(pair.pair[0])?.label ?? pair.pair[0]} <span aria-hidden="true">↔</span> {materials.get(pair.pair[1])?.label ?? pair.pair[1]}
            </p>
          </div>
          {phylo?.edges[pair.evidence_id] && (() => {
            const pf = phylo.edges[pair.evidence_id]
            return <div className="phylo-box">
              <div className="phylo-head">
                <span className={`phylo-tag phylo-${pf.mutation.toLowerCase()}`}>{pf.mutation_label}</span>
                <span className="phylo-acig">ACIG <strong>{formatScore(pf.acig)}</strong></span>
              </div>
              <p className="muted">{pf.detail}</p>
              {pf.top_traces.length > 0 && <div className="signal-chips">
                {pf.top_traces.map((t) => <span key={t} className="signal-chip">{t}</span>)}
              </div>}
            </div>
          })()}
          {collectionRun && <>
            <h4>Wyniki sygnałów</h4>
            <PairSignals runId={collectionRun} evidenceId={pair.evidence_id} />
          </>}
          <details><summary>Uzasadnienie powiązania</summary>
            <SignalExplanation
              text={pair.explanation}
              pairLabels={[materials.get(pair.pair[0])?.label ?? pair.pair[0], materials.get(pair.pair[1])?.label ?? pair.pair[1]]}
            />
          </details>
          <button type="button" className="edge-open" onClick={(event) => onEvidence(pair.evidence_id, event.currentTarget)}>
            Zobacz dowody
          </button>
          {onUnpromoteEdge && manualEdgeIds.has(pair.evidence_id) && (
            <button
              type="button"
              className="outline edge-open"
              disabled={promoting === pair.evidence_id}
              onClick={async () => {
                setPromoting(pair.evidence_id)
                try {
                  await onUnpromoteEdge(pair.evidence_id)
                  setSelectedEdge('')
                } finally {
                  setPromoting('')
                }
              }}
            >
              {promoting === pair.evidence_id ? 'Cofanie…' : 'Cofnij zatwierdzenie połączenia'}
            </button>
          )}
        </> : suggestedEdge ? <>
          <div className="section-heading"><h3>Sugerowane powiązanie</h3>
            <button type="button" className="outline" onClick={() => setSelectedEdge('')}>Wyczyść</button>
          </div>
          <div className="edge-preview">
            <div className="edge-score"><span>{suggestedEdge.label}</span><strong>{formatScore(suggestedEdge.score)}</strong></div>
            <p className="edge-materials">
              {materials.get(suggestedEdge.source)?.label ?? suggestedEdge.source} <span aria-hidden="true">↔</span> {materials.get(suggestedEdge.target)?.label ?? suggestedEdge.target}
            </p>
          </div>
          <p className="muted">Potencjalne powiązanie poniżej pełnego progu fuzji — do ręcznego rozpatrzenia.</p>
          <details open><summary>Uzasadnienie sygnału</summary>
            <SignalExplanation
              text={suggestedEdge.explanation}
              pairLabels={[materials.get(suggestedEdge.source)?.label ?? suggestedEdge.source, materials.get(suggestedEdge.target)?.label ?? suggestedEdge.target]}
            />
          </details>
          {onPromoteEdge && (
            <button
              type="button"
              className="edge-open"
              disabled={promoting === suggestedEdge.pair_evidence_id}
              onClick={async () => {
                setPromoting(suggestedEdge.pair_evidence_id)
                try {
                  await onPromoteEdge(suggestedEdge.pair_evidence_id)
                } finally {
                  setPromoting('')
                }
              }}
            >
              {promoting === suggestedEdge.pair_evidence_id ? 'Zatwierdzanie…' : 'Zatwierdź połączenie na grafie'}
            </button>
          )}
          <button type="button" className="outline edge-open" onClick={(event) => onEvidence(suggestedEdge.pair_evidence_id, event.currentTarget)}>
            Zobacz dowody
          </button>
        </> : current ? <>
          <h3>{current.label}</h3>
          {patientZeroByMaterial.has(selected) && <div className="phylo-pz-badge">
            <strong>★ Szablon źródłowy (Patient Zero)</strong>
            <span>{patientZeroByMaterial.get(selected)!.reason}</span>
          </div>}
          {current.thumbnail && <img className="graph-preview" src={current.thumbnail.href} alt="" />}
          {current.display_url && <p className="muted">{current.display_url}</p>}
          {(() => {
            const host = domains?.[selected]?.[0]
            const meta = host ? result.investigation?.domain_meta?.[host] : undefined
            if (!meta || (!meta.ips?.length && !meta.registrar && !meta.tls_issuer && !meta.created)) return null
            return <div className="signal-chips">
              {meta.registrar && <span className="signal-chip">Rejestrator <strong>{meta.registrar}</strong></span>}
              {meta.created && <span className="signal-chip">Utworzono <strong>{meta.created.slice(0, 10)}</strong></span>}
              {meta.ips?.[0] && <span className="signal-chip">IP <strong>{meta.ips[0]}</strong></span>}
              {meta.tls_issuer && <span className="signal-chip">TLS <strong>{meta.tls_issuer}</strong></span>}
            </div>
          })()}
          {Boolean(sources?.[selected]?.length) && <details><summary>Źródła i daty importu</summary><ul>
            {sources?.[selected].map((s, i) => <li key={i}>{s.label} · {new Date(s.date).toLocaleString('pl-PL')}</li>)}
          </ul></details>}
          <p>{current.available_evidence?.map((f) => familyLabels[f] ?? f).join(' · ') || 'Brak odczytanych śladów'}</p>
          <h4>Powiązane materiały</h4>
          {neighbours.length === 0 && <p className="muted">{
            scopedEdges.some((edge) => edge.source === selected || edge.target === selected)
              ? 'Brak powiązań przy tych filtrach.' : describe(selected)
          }</p>}
          {neighbours.map((edge) => {
            const label = materials.get(edge.source === selected ? edge.target : edge.source)?.label
            return <button className="outline graph-neighbour" type="button" key={edge.pair_evidence_id}
              onClick={(e) => onEvidence(edge.pair_evidence_id, e.currentTarget)}>
              Dowody: {label} <span>{formatScore(edge.score)}</span>
            </button>
          })}
          {collectionRun && <details>
            <summary>Dodatkowe dopasowania</summary>
            {signalsError ? <p>Nie udało się pobrać śladów.</p> : !signals ? <p>Wczytywanie śladów…</p>
              : signals.length === 0 ? <p>Brak dodatkowych dopasowań.</p> : signals.map((signal) => {
                const other = signal.pair.find((id) => id !== selected)
                const alreadyLinked = scopedEdges.some((e) =>
                  (e.source === signal.pair[0] && e.target === signal.pair[1]) ||
                  (e.source === signal.pair[1] && e.target === signal.pair[0]),
                )
                return <div key={signal.evidence_id} className="signal-row-actions">
                  <button type="button" className="outline graph-neighbour"
                    onClick={(e) => onEvidence(signal.evidence_id, e.currentTarget)}>
                    {signal.label} · {other ? materials.get(other)?.label : ''}
                    {signal.score !== null && <span>{formatScore(signal.score)}</span>}
                  </button>
                  {onPromoteEdge && !alreadyLinked && (
                    <button
                      type="button"
                      className="outline promote-mini"
                      disabled={promoting === signal.evidence_id}
                      title="Włącz to dopasowanie jako potwierdzone połączenie na grafie"
                      onClick={async () => {
                        setPromoting(signal.evidence_id)
                        try {
                          await onPromoteEdge(signal.evidence_id)
                        } finally {
                          setPromoting('')
                        }
                      }}
                    >
                      {promoting === signal.evidence_id ? '…' : '+ Połącz'}
                    </button>
                  )}
                </div>
              })}
          </details>}
        </> : <>
          <h3>Szczegóły materiału</h3>
          <p className="muted">Wybierz materiał lub linię na grafie, aby zobaczyć powiązania i dowody.</p>
        </>)}
      </aside>
    </div>
  </section>
}
