import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { MonitorScan, MonitorStatus } from '../api/types'
import WorkspaceIcon from './WorkspaceIcon'

const dateTime = new Intl.DateTimeFormat('pl-PL', { dateStyle: 'medium', timeStyle: 'short' })

/** Bonus: skan źródła reklam. Pokazuje, co jest nowe od poprzedniego skanu i ile grup płatników ma nowe reklamy. */
export default function MonitorPanel() {
  const [status, setStatus] = useState<MonitorStatus>()
  const [scan, setScan] = useState<MonitorScan>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => { api.getMonitor().then(setStatus).catch(() => setError('Nie udało się odczytać stanu monitora.')) }, [])

  async function run() {
    setBusy(true)
    setError('')
    try {
      const result = await api.scanMonitor()
      setScan(result)
      setStatus(await api.getMonitor())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Nie udało się wykonać skanu.')
    } finally {
      setBusy(false)
    }
  }

  const source = status?.source
  const lastRun = (scan?.state ?? status?.state)?.last_run
  const totals = (scan?.state ?? status?.state)?.totals

  return <section className="monitor-panel" aria-labelledby="monitor-h">
    <div className="monitor-head">
      <div>
        <h2 id="monitor-h">Skan źródła reklam</h2>
        <p className="muted">
          Pobiera z eksportu nowe lub zmienione reklamy i pokazuje, które są nowe od poprzedniego skanu. Nowe rekordy trafiają do osobnego
          magazynu monitora, a analiza płatnika wskazuje grupy reklam z nowymi wpisami.
          {source && <> Źródło: <strong>{source.name}</strong>{source.synthetic ? ' (syntetyczny przykład)' : ''}.</>}
        </p>
      </div>
      <button type="button" className="icon-button" disabled={busy || source?.available === false} onClick={() => void run()}>
        <WorkspaceIcon name="refresh" />{busy ? 'Skanowanie…' : 'Skanuj źródło'}
      </button>
    </div>
    {error && <p role="alert" className="error">{error}</p>}
    {scan && !scan.ok && <p role="alert" className="error">{scan.error ?? 'Skan nie powiódł się.'}</p>}
    {scan?.ok && <dl className="explorer-stats" aria-label="Wynik skanu" role="status">
      <div><dt>Nowe</dt><dd>{scan.new ?? 0}</dd></div>
      <div><dt>Zmienione</dt><dd>{scan.updated ?? 0}</dd></div>
      <div><dt>Bez zmian</dt><dd>{scan.duplicate ?? 0}</dd></div>
      <div><dt>Grupy płatników z nowymi</dt><dd>{scan.payer_groups_touched ?? 0}</dd></div>
    </dl>}
    {scan?.ok && (scan.new ?? 0) === 0 && (scan.updated ?? 0) === 0 &&
      <p className="muted">Nic nowego od ostatniego skanu. Dopisz rekord do pliku źródła i skanuj ponownie.</p>}
    {lastRun && <p className="muted monitor-meta">
      Ostatni skan: {dateTime.format(new Date(lastRun))}{totals ? ` · łącznie nowych: ${totals.new ?? 0}` : ''}
    </p>}
  </section>
}
