import { useState } from 'react'
import PairSignals from './PairSignals'
import { formatScore } from './formatScore'
import type { ClusterView, Material, PairEvidenceView } from '../api/types'
import WorkspaceIcon from './WorkspaceIcon'

const shortDate = new Intl.DateTimeFormat('pl-PL', { day: 'numeric', month: 'long', year: 'numeric' })

type Group = ClusterView & { index: number }
type SourceDates = Record<string, { label: string; date: string }[]>

interface Props {
  groups: Group[]
  materials: Material[]
  domains?: Record<string, string[]>
  artifacts?: { material_ids: string[] }[]
  phylofake?: import('../api/types').PhyloFakeData
  edges: PairEvidenceView[]
  sources?: SourceDates
  runId?: string | null
  assignments?: { material_id: string; status: string }[]
  onGroup: (id: string) => void
  onMaterial: (id: string) => void
  onEvidence: (id: string, opener: HTMLElement) => void
}

export default function CampaignOverview({ groups, materials, domains, artifacts, phylofake, edges, sources, runId, assignments, onGroup, onMaterial, onEvidence }: Props) {
  const [recentSelection, setRecentSelection] = useState('')
  const byId = new Map(materials.map((item) => [item.material_id, item]))
  const assigned = new Set(groups.flatMap((group) => group.material_ids))
  const unassigned = materials.filter((item) => !assigned.has(item.material_id))
  const statusById = new Map(assignments?.map((item) => [item.material_id, item.status]))
  const recent = materials.flatMap((item) => {
    const date = sources?.[item.material_id]?.reduce((latest, source) =>
      Date.parse(source.date) > Date.parse(latest) ? source.date : latest, sources[item.material_id][0]?.date)
    return date ? [{ item, date }] : []
  }).sort((a, b) => Date.parse(b.date) - Date.parse(a.date)).slice(0, 4)

  const sameDay = new Set(recent.map(({ date }) => shortDate.format(new Date(date)))).size === 1
  const selected = recent.find(({ item }) => item.material_id === recentSelection) ?? recent[0]
  const selectedPair = selected && edges.reduce<PairEvidenceView | undefined>((match, edge) =>
    edge.pair.includes(selected.item.material_id) && (!match || (edge.score ?? -1) > (match.score ?? -1)) ? edge : match, undefined)

  return <div className="campaign-overview">
    <div className="campaign-feed">
      <div className="section-heading"><h2>Wykryte grupy</h2><span>{groups.length}</span></div>
      {groups.length === 0 && <p className="empty-state">Brak grup w wybranym zakresie.</p>}
      {groups.map((group) => {
        const ids = new Set(group.material_ids)
        const members = group.material_ids.flatMap((id) => byId.get(id) ?? [])
        const hosts = new Set(members.flatMap((item) => domains?.[item.material_id] ?? []))
        const links = edges.filter((edge) => edge.pair.every((id) => ids.has(id)))
        const shared = artifacts?.filter((artifact) => artifact.material_ids.filter((id) => ids.has(id)).length > 1).length ?? 0
        const best = links.reduce<PairEvidenceView | undefined>((match, edge) =>
          edge.score != null && (!match || edge.score > (match.score ?? -1)) ? edge : match, undefined)
        const samples = best ? best.pair.flatMap((id) => byId.get(id) ?? []) : members.slice(0, 2)
        const pf = phylofake?.clusters[group.cluster_id]
        return <article className="campaign-card" key={group.cluster_id}>
          <header>
            <div>
              <h3>Grupa {group.index}</h3>
              {pf && <div className="phylo-card-meta">
                <span className={`phylo-tag phylo-${pf.dominant_mutation.toLowerCase()}`}>{pf.dominant_mutation_label}</span>
                <span className="phylo-acig">★ Źródło: <strong>{pf.patient_zero_label}</strong> · ACIG <strong>{formatScore(pf.avg_acig)}</strong></span>
              </div>}
            </div>
            <button type="button" className="outline icon-button" onClick={() => onGroup(group.cluster_id)}>
              Otwórz graf <WorkspaceIcon name="arrow" />
            </button>
          </header>
          {group.summary && <p className="muted">{group.summary}</p>}
          <dl className="campaign-metrics">
            <div><dt>Materiały</dt><dd>{members.length}</dd></div>
            <div><dt>Domeny</dt><dd>{hosts.size}</dd></div>
            <div><dt>Wspólne ślady</dt><dd>{shared}</dd></div>
            <div><dt>Najwyższe podobieństwo</dt><dd>{best?.score == null ? '—' : formatScore(best.score)}</dd></div>
          </dl>
          <div className="campaign-samples">{samples.map((item) =>
            <button type="button" className="sample-button" key={item.material_id} onClick={() => onMaterial(item.material_id)}>
              {item.thumbnail && <img src={item.thumbnail.href} alt={item.thumbnail.alt ?? ''} loading="lazy" />}
              <span>{item.label}</span>
            </button>
          )}</div>
        </article>
      })}
      <section className="unassigned" aria-labelledby="unassigned-h">
        <div className="section-heading"><h2 id="unassigned-h">Materiały bez grupy</h2><span>{unassigned.length}</span></div>
        {unassigned.length ? <table className="forensic-table">
          <caption className="visually-hidden">Materiały bez przypisania do grupy</caption>
          <thead><tr><th scope="col">Materiał</th><th scope="col">Domena</th><th scope="col">Stan</th></tr></thead>
          <tbody>{unassigned.map((item) => <tr key={item.material_id}>
            <td><button type="button" className="text-button" onClick={() => onMaterial(item.material_id)}>{item.label}</button></td>
            <td>{domains?.[item.material_id]?.[0] ?? <span className="muted">—</span>}</td>
            <td>{statusById.get(item.material_id) === 'insufficient_data' ? 'Za mało danych' : 'Brak podobnych materiałów'}</td>
          </tr>)}</tbody>
        </table> : <p className="muted">Brak materiałów bez grupy w tym zakresie.</p>}
      </section>
    </div>
    <aside className="recent-evidence" aria-labelledby="recent-evidence-h">
      <div className="section-heading"><h2 id="recent-evidence-h">Ostatnio zaimportowane</h2></div>
      {!selected ? <p className="muted">Brak zapisanych dat importu.</p> : <>
        {selected.item.thumbnail && <img className="recent-preview" src={selected.item.thumbnail.href} alt={selected.item.thumbnail.alt ?? ''} />}
        <div className="recent-material">
          <h3>{selected.item.label}</h3>
          <time dateTime={selected.date}>{shortDate.format(new Date(selected.date))}</time>
          {selected.item.display_url && <p className="muted">{selected.item.display_url}</p>}
        </div>
        {selectedPair ? <>
          <p className="recent-pair">Najsilniejsze powiązanie: <strong>{selectedPair.pair.map((id) => byId.get(id)?.label ?? id).join(' ↔ ')}</strong></p>
          <div className="edge-score recent-score">
            <span>Podobieństwo pary</span>
            <strong>{selectedPair.score === null ? '—' : formatScore(selectedPair.score)}</strong>
          </div>
          {runId && <PairSignals runId={runId} evidenceId={selectedPair.evidence_id} />}
          <button type="button" className="icon-button edge-open" onClick={(e) => onEvidence(selectedPair.evidence_id, e.currentTarget)}>
            Otwórz dowód pary <WorkspaceIcon name="arrow" />
          </button>
        </> : <p className="muted">Brak zapisanego powiązania w tym zakresie.</p>}
        <button type="button" className="text-button" onClick={() => onMaterial(selected.item.material_id)}>Pokaż materiał w grafie</button>
        {recent.length > 1 && <ul aria-label="Ostatnie materiały">{recent.map(({ item, date }) => <li key={item.material_id}>
          <button type="button" className="recent-choice" aria-pressed={item.material_id === selected.item.material_id}
            onClick={() => setRecentSelection(item.material_id)}>
            <strong>{item.label}</strong>
            {!sameDay && <time dateTime={date}>{shortDate.format(new Date(date))}</time>}
          </button>
        </li>)}</ul>}
      </>}
    </aside>
  </div>
}
