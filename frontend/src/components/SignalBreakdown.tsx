import type { Evidence } from '../api/types'
import { formatScore } from './formatScore'

export const familyLabels: Record<string, string> = { asset: 'Grafiki', text: 'Tekst', dom: 'Struktura HTML' }

export default function SignalBreakdown({ families }: { families: NonNullable<Evidence['families']> }) {
  return <dl className="signal-breakdown">
    {families.map(({ name, score, supports }) => {
      const label = familyLabels[name] ?? name
      return <div className="signal-row" key={name}>
        <dt><i className={`legend-dot ${name}`} aria-hidden="true" />{label}</dt>
        <dd>
          {score === null ? <span className="muted">Brak porównywalnych danych</span> : <>
            <div className="signal-value">
              <span className="muted">{supports ? 'Wspiera powiązanie' : 'Poniżej progu wsparcia'}</span>
              <strong>{formatScore(score)}</strong>
            </div>
            <meter min={0} max={1} value={score} aria-label={label} />
          </>}
        </dd>
      </div>
    })}
  </dl>
}
