import { useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { Evidence, EvidenceItem, TextExcerpt } from '../api/types'

export default function EvidencePanel({ runId, evidenceId, onClose }: { runId: string; evidenceId: string; onClose: () => void }) {
  const [evidence, setEvidence] = useState<Evidence | null>(null)
  const [error, setError] = useState<ApiError | null>(null)

  useEffect(() => {
    const ctrl = new AbortController()
    api.getEvidence(runId, evidenceId, ctrl.signal).then(setEvidence, (e) => {
      if (!ctrl.signal.aborted) setError(e instanceof ApiError ? e : null)
    })
    return () => ctrl.abort()
  }, [runId, evidenceId])

  return (
    <section className="evidence" aria-label="Dowód">
      <div className="row">
        <h2>Dowód</h2>
        <button type="button" onClick={onClose}>Zamknij</button>
      </div>
      {error && <div role="alert" className="error">Nie można pobrać dowodu. {error.message}</div>}
      {!evidence && !error && <p role="status">Pobieranie dowodu…</p>}
      {evidence && (
        <>
          <p>{evidence.explanation}</p>
          <p className="muted">
            {evidence.score == null ? 'wynik: niewykonalne' : `wynik: ${evidence.score.toFixed(2)}`} · metoda {evidence.method_id}
          </p>
          {!evidence.available && <p role="status"><strong>Porównanie niewykonalne.</strong></p>}
          {evidence.limitations?.map((l) => <p key={l} className="muted">Ograniczenie: {l}</p>)}
          <div className="sides">
            {evidence.sides.map((s) => (
              <div key={s.material_id} className="side" data-testid="evidence-side">
                <h3>{s.label}</h3>
                {s.display_url && <div className="muted">{s.display_url}</div>}
                {s.items.length === 0 && <p className="muted">Brak dowodów po tej stronie.</p>}
                {s.items.map((it) => <Item key={it.artifact_id} it={it} />)}
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  )
}

function Item({ it }: { it: EvidenceItem }) {
  return (
    <div className="item">
      <div className="muted">{it.role}{it.media_type ? ` · ${it.media_type}` : ''}</div>
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
