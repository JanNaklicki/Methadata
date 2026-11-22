import { useEffect, useMemo, useState } from 'react'
import type { ClusterRunView, SharedArtifact } from '../api/types'
import GraphCanvas from './GraphCanvas'
import { artifactKey, artifactLabel, buildArtifactNetwork, materialKey } from './graphData'

const names: Record<SharedArtifact['kind'], string> = {
  domain: 'Domena',
  tracker: 'Tracker',
  image: 'Grafika',
  script: 'Skrypt',
  person: 'Osoba publiczna',
  brand: 'Marka / podmiot',
  contact: 'Kontakt',
  affiliate: 'Kod afiliacyjny',
  infrastructure: 'Infrastruktura (IP / rejestrator)',
}

const explanations: Record<SharedArtifact['kind'], string> = {
  image: 'Ten sam plik graficzny (identyczny skrót) występuje w każdym z tych materiałów.',
  script: 'Ten sam skrypt strony (identyczny skrót) występuje w każdym z tych materiałów.',
  domain: 'Wszystkie te materiały odwołują się do tej samej domeny.',
  tracker: 'Wszystkie te materiały korzystają z tego samego trackera.',
  person: 'W każdym z tych materiałów pojawia się ta sama osoba publiczna (tekst lub dopasowanie wizualne).',
  brand: 'W każdym z tych materiałów pojawia się ta sama marka, instytucja lub produkt.',
  contact: 'Te materiały współdzielą te same dane kontaktowe (np. telefon, e-mail lub kanał).',
  affiliate: 'Te materiały współdzielą ten sam identyfikator kampanii lub programu afiliacyjnego.',
  infrastructure: 'Domeny w tych materiałach współdzielą ten sam adres IP hostingu lub rejestratora.',
}
const hasHash = (kind: SharedArtifact['kind']) => kind === 'image' || kind === 'script'
function plural(n: number, one: string, few: string, many: string) {
  if (n === 1) return one
  const last = n % 10, tens = n % 100
  return last >= 2 && last <= 4 && (tens < 12 || tens > 14) ? few : many
}

const shortLabel = (item: SharedArtifact) =>
  item.kind === 'image' || item.kind === 'script' ? `${item.label.slice(0, 8)}…` : artifactLabel(item)

export default function ArtifactGraph({ result, materialIds, onMaterial }: {
  result: ClusterRunView
  materialIds: Set<string>
  onMaterial: (id: string) => void
}) {
  const [kind, setKind] = useState('')
  const [active, setActive] = useState('')
  const { graph, visible, materials, candidateCount } = useMemo(
    () => buildArtifactNetwork(result, materialIds, kind), [result, materialIds, kind],
  )
  const selected = visible.find((item) => artifactKey(item) === active)
  const sharing = selected ? materials.filter((m) => selected.material_ids.includes(m.material_id)) : []
  const groupOf = useMemo(() => new Map(result.clusters.flatMap((c, i) => c.material_ids.map((id) => [id, c.index ?? i + 1] as const))), [result.clusters])
  const groupsSpanned = new Set(sharing.map((m) => groupOf.get(m.material_id)).filter(Boolean)).size
  const keys = useMemo(() => new Map(materials.map((item) => [materialKey(item.material_id), item.material_id])), [materials])

  useEffect(() => {
    const linked = new Set(selected?.material_ids.map(materialKey))
    graph.updateEachNodeAttributes((key, attrs) => ({
      ...attrs, selected: key === active, muted: Boolean(selected && key !== active && !linked.has(key)),
    }))
    graph.updateEachEdgeAttributes((_key, attrs, source, target) => ({
      ...attrs, muted: Boolean(selected && source !== active && target !== active),
    }))
  }, [active, graph, selected])

  function selectNode(key: string) {
    const id = keys.get(key)
    if (id !== undefined) onMaterial(id)
    else setActive(key)
  }

  return <section aria-label="Graf dowodów" className="artifact-workspace">
    <div className="workbench-heading"><div><h3>Wspólne ślady</h3>
      <p className="muted">Linia łączy materiał ze śladem, który zawiera.</p></div>
      <label>Rodzaj śladu<select value={kind} onChange={(event) => { setKind(event.target.value); setActive('') }}>
        <option value="">Wszystkie</option>
        {Object.entries(names).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
      </select></label>
    </div>
    {visible.length ? <>
      <div className="artifact-network">
        <div className="network-key"><span>Materiały</span><span>Ślady</span></div>
        <GraphCanvas graph={graph} label="Mapa wspólnych śladów" onNode={selectNode}>
          {() => <div className="network-list-columns">
            <section aria-label="Materiały w grafie">
              <h4>Materiały</h4>
              <ul>{materials.map((material) => <li key={material.material_id}>
                <button type="button" className="outline" onClick={() => onMaterial(material.material_id)}>{material.label}</button>
              </li>)}</ul>
            </section>
            <section aria-label="Ślady w grafie">
              <h4>Ślady</h4>
              <ul>{visible.map((item) => <li key={artifactKey(item)}>
                <button type="button" className="outline" aria-pressed={active === artifactKey(item)} onClick={() => setActive(artifactKey(item))}>
                  {item.preview && <img className="artifact-thumb" src={item.preview.href} alt="" loading="lazy" />}
                  <span>{names[item.kind]} · {shortLabel(item)}</span>
                </button>
              </li>)}</ul>
            </section>
          </div>}
        </GraphCanvas>
      </div>
      {candidateCount > visible.length && <p className="muted">Pokazano {visible.length} z {candidateCount} najczęściej współdzielonych śladów.</p>}
      {selected ? <div className="artifact-detail">
        <h4>{names[selected.kind]} w {sharing.length} {plural(sharing.length, 'materiale', 'materiałach', 'materiałach')}</h4>
        {selected.kind === 'image' && (selected.preview
          ? <figure className="artifact-preview">
            <img src={selected.preview.href} alt={selected.preview.alt ?? 'Wspólna grafika'} />
            {selected.preview.width && selected.preview.height && <figcaption className="muted">{selected.preview.width}×{selected.preview.height} px</figcaption>}
          </figure>
          : <p className="artifact-note muted">Podgląd tej grafiki jest niedostępny (np. grafika wektorowa SVG albo brak pliku w magazynie).</p>)}
        <p className="muted">{explanations[selected.kind]}</p>
        <ul className="artifact-materials">{sharing.map((m) => <li key={m.material_id}>
          <button className="outline" type="button" onClick={() => onMaterial(m.material_id)}>
            {m.thumbnail && <img src={m.thumbnail.href} alt="" loading="lazy" />}
            <span>{m.label}</span>
            <small>{groupOf.get(m.material_id) ? `Grupa ${groupOf.get(m.material_id)}` : 'Bez grupy'}</small>
          </button>
        </li>)}</ul>
        {groupsSpanned > 1 && <p className="artifact-note">Ten ślad występuje w materiałach z {groupsSpanned} różnych grup. To powód do sprawdzenia, ale nie dowód wspólnego nadawcy.</p>}
        {hasHash(selected.kind)
          ? <details><summary>Pełny identyfikator (SHA-256)</summary><code>{selected.label}</code></details>
          : <p className="artifact-name"><code>{selected.label}</code></p>}
      </div> : <p className="artifact-hint muted">Wybierz ślad z listy, aby zobaczyć, w których materiałach występuje.</p>}
    </> : <p className="workbench-empty">Brak wspólnych śladów w wybranym zakresie.</p>}
  </section>
}
