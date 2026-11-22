import { formatScore } from './formatScore'
import { useEffect, useRef } from 'react'
import type { Evidence, EvidenceItem, EvidenceSide, TextExcerpt } from '../api/types'
import SignalBreakdown from './SignalBreakdown'
import SignalExplanation from './SignalExplanation'
import { usePairEvidence } from './usePairEvidence'
import { downloadJson } from './downloadJson'
import WorkspaceIcon from './WorkspaceIcon'
import ScenarioPanel from '../counterfactual/ScenarioPanel'

interface Props {
  runId: string
  evidenceId: string
  onClose: () => void
  scenario?: { label: (id: string) => string }
}

export default function EvidencePanel({ runId, evidenceId, onClose, scenario }: Props) {
  const panel = useRef<HTMLElement>(null)
  const { evidence, error, retry } = usePairEvidence(runId, evidenceId)

  useEffect(() => {
    panel.current?.focus({ preventScroll: true })
    panel.current?.scrollIntoView?.({ block: 'nearest' })
  }, [runId, evidenceId])

  return (
    <section ref={panel} className="evidence" aria-label="Dowód" tabIndex={-1} onKeyDown={(event) => {
      if (event.key === 'Escape' && !event.defaultPrevented) onClose()
    }}>
      <div className="row">
        <h2>Dowód</h2>
        <div className="upload-actions">
          {evidence && <button type="button" className="outline icon-button"
            onClick={() => downloadJson(`methadata-${evidence.evidence_id}.json`, evidence)}>
            <WorkspaceIcon name="download" /> Pobierz dowód
          </button>}
          <button type="button" className="outline" onClick={onClose}>Zamknij</button>
        </div>
      </div>
      {error && <div className="error">
        <p role="alert">Nie można pobrać dowodu. {error}</p>
        <button type="button" className="outline" onClick={() => {
          panel.current?.focus({ preventScroll: true })
          retry()
        }}>Spróbuj ponownie</button>
      </div>}
      {!evidence && !error && <p role="status">Pobieranie dowodu…</p>}
      {evidence && (
        <>
          <div className="evidence-pair">
            <strong>{evidence.sides[0].label}</strong><span aria-hidden="true">↔</span><strong>{evidence.sides[1].label}</strong>
          </div>
          {!evidence.available && <p className="notice" role="status"><strong>Porównanie niewykonalne.</strong></p>}
          {evidence.score != null && <div className="evidence-score"><span>Wynik podobieństwa</span>
            <strong>{formatScore(evidence.score)}</strong></div>}
          <section className="capture-comparison" aria-label="Porównanie materiałów">
            <h3>Porównanie materiałów</h3>
            <div className="sides">{evidence.sides.map((side) =>
              <CaptureSide key={side.material_id} side={side} />)}</div>
          </section>
          {evidence.sides.some((side) => Boolean(firstExcerpt(side))) && (
            <section className="capture-comparison" aria-labelledby="text-excerpts">
              <h3 id="text-excerpts">Fragmenty tekstu źródłowego</h3>
              <div className="sides">{evidence.sides.map((side) => {
                const excerpt = firstExcerpt(side)
                return <div className="excerpt-side" key={side.material_id}>
                  <h4>{side.label}</h4>
                  {excerpt ? <Excerpt t={excerpt} /> : <p className="muted">Brak zapisanego fragmentu tekstu.</p>}
                </div>
              })}</div>
            </section>
          )}
          <section className="signal-comparison" aria-label="Wyniki poszczególnych sygnałów">
            <h3>Wyniki poszczególnych sygnałów</h3>
            {evidence.families?.length ? <SignalBreakdown families={evidence.families} /> : null}
            <details><summary>Jak obliczono wynik</summary>
              <SignalExplanation
                text={evidence.explanation}
                pairLabels={evidence.sides.length >= 2 ? [evidence.sides[0].label, evidence.sides[1].label] : undefined}
              />
            </details>
            {Boolean(evidence.signals?.length) && <details><summary>Dodatkowe porównania</summary>
              <ul>{evidence.signals?.map((signal) => <li key={signal.evidence_id}>
                <strong>{signal.label}</strong> · {signal.score === null ? 'Brak porównywalnych danych' : formatScore(signal.score)}
                <SignalExplanation
                  text={signal.explanation}
                  pairLabels={evidence.sides.length >= 2 ? [evidence.sides[0].label, evidence.sides[1].label] : undefined}
                />
              </li>)}</ul>
            </details>}
            {Boolean(evidence.inactive_signals?.length) && <details>
              <summary>Porównania pominięte w tej analizie</summary>
              <p>{evidence.inactive_signals?.join(' · ')}</p>
            </details>}
            {evidence.limitations?.map((l) => <p key={l} className="muted">Ograniczenie: {l}</p>)}
          </section>
          <ArtifactAudit evidence={evidence} />
          {scenario && <ScenarioPanel runId={runId} evidenceId={evidenceId} {...scenario} />}
        </>
      )}
    </section>
  )
}

const roleLabels: Record<string, string> = {
  resource: 'Zasób', landing_html: 'Strona docelowa', landing_dom: 'Struktura strony',
  creative_image: 'Grafika reklamy', caption: 'Opis', screenshot: 'Zrzut ekranu',
}
const formatLabels: Record<string, string> = {
  'image/jpeg': 'JPEG', 'image/png': 'PNG', 'image/webp': 'WebP', 'image/gif': 'GIF',
  'image/svg+xml': 'SVG', 'text/html': 'HTML', 'application/json': 'JSON',
}

function describeItem(item: EvidenceItem) {
  const role = roleLabels[item.role] ?? item.role
  const format = item.media_type ? formatLabels[item.media_type] ?? item.media_type : ''
  return format ? `${role} · ${format}` : role
}

function firstExcerpt(side: EvidenceSide) {
  return side.items.find((item) => item.text_excerpts?.length)?.text_excerpts?.[0]
}

function ArtifactAudit({ evidence }: { evidence: Evidence }) {
  const records = evidence.sides.flatMap((side) => [...new Map(side.items.map((item) => [item.artifact_id, item])).values()]
    .map((item) => ({ side, item })))
  return <section className="evidence-provenance" aria-label="Pochodzenie dowodu">
    <details className="audit-details">
    <summary id="audit-h">Szczegóły techniczne i skróty plików ({records.length})</summary>
    <dl className="audit-ids">
      <div><dt>Dowód</dt><dd><code>{evidence.evidence_id}</code></dd></div>
      <div><dt>Przebieg</dt><dd><code>{evidence.run_id}</code></dd></div>
      <div><dt>Metoda</dt><dd><code>{evidence.method_id}</code></dd></div>
    </dl>
    {records.length > 0 ? <div className="table-scroll" role="region" aria-label="Pliki materiałów" tabIndex={0}>
      <table className="forensic-table">
      <caption className="visually-hidden">Pliki obu materiałów</caption>
      <thead><tr><th scope="col">Materiał</th><th scope="col">Plik</th><th scope="col">Rodzaj</th><th scope="col">SHA-256</th></tr></thead>
      <tbody>{records.map(({ side, item }) => <tr key={JSON.stringify([side.material_id, item.artifact_id])}>
        <td><strong>{side.label}</strong><code>{side.material_id}</code></td>
        <td><code>{item.artifact_id}</code></td>
        <td>{describeItem(item)}</td>
        <td>{item.sha256 ? <code>{item.sha256}</code> : <span className="muted">Brak w przebiegu</span>}</td>
      </tr>)}</tbody>
    </table></div> : <p className="muted">Brak plików przypisanych do tej pary.</p>}
    </details>
  </section>
}

function CaptureSide({ side }: { side: EvidenceSide }) {
  const visual = side.items.find((item) => item.preview)
  const remaining = side.items.filter((item) => item !== visual)
  return <div className="side" data-testid="evidence-side">
    <h4>{side.label}</h4>
    {side.display_url && <p className="muted">{side.display_url}</p>}
    {visual ? <Item it={visual} /> : <p className="capture-placeholder muted">{side.items.length ? 'Brak zapisanego podglądu.' : 'Brak dowodów po tej stronie.'}</p>}
    {remaining.length > 0 && <details><summary>Pozostałe ślady ({remaining.length})</summary>
      {remaining.map((item) => <Item key={JSON.stringify([item.artifact_id, item.locator])} it={item} />)}
    </details>}
  </div>
}

function Item({ it }: { it: EvidenceItem }) {
  return (
    <div className="item">
      <div className="muted">{describeItem(it)}</div>
      {it.preview && <img src={it.preview.href} alt={it.preview.alt ?? ''} width={Math.min(it.preview.width ?? 320, 320)} />}
      {it.text_excerpts?.map((t, i) => <Excerpt key={i} t={t} />)}
    </div>
  )
}

/** Tekst wstawiany wyłącznie jako węzły tekstowe Reacta (nigdy jako HTML). */
function Excerpt({ t }: { t: TextExcerpt }) {
  const chars = Array.from(t.text)
  const s = t.highlight_start, e = t.highlight_end
  const ok = s != null && e != null && s < e && e <= chars.length
  return (
    <blockquote>
      {t.source_label && <div className="muted">{t.source_label}</div>}
      {ok ? (
        <>
          {chars.slice(0, s).join('')}
          <mark>{chars.slice(s, e).join('')}</mark>
          {chars.slice(e).join('')}
        </>
      ) : t.text}
    </blockquote>
  )
}
