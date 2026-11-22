import { useRef, useState } from 'react'
import { api, ApiError } from '../api/client'

export default function StartScreen({ onRun, onCollection, onHint }: {
  onRun: (runId: string) => void
  onCollection: (importId?: string) => void
  onHint: (query: string) => void
}) {
  const [hint, setHint] = useState('')
  const [url, setUrl] = useState('')
  const [fetchLive, setFetchLive] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  async function start(createObservationId: () => Promise<string>, profile?: 'demo') {
    setBusy(true)
    setError(null)
    try {
      const observationId = await createObservationId()
      const run = await api.createRun(observationId, profile)
      onRun(run.run_id)
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, { code: 'internal_error', message: 'Nieoczekiwany błąd.' }))
      setBusy(false)
    }
  }

  const submitUrl = (e: React.FormEvent) => {
    e.preventDefault()
    let value = url.trim()
    if (!value || /\s/.test(value)) {
      setError(new ApiError(0, { code: 'validation_failed', message: 'Wpisz poprawną domenę lub pełny adres http(s)://.' }))
      return
    }
    if (!value.includes('://')) {
      if (!value.includes('.')) {
        setError(new ApiError(0, { code: 'validation_failed', message: 'Wpisz poprawną domenę (np. example.com) lub pełny adres http(s)://.' }))
        return
      }
      value = `https://${value}`
    } else if (!/^https?:\/\//i.test(value)) {
      setError(new ApiError(0, { code: 'validation_failed', message: 'Obsługiwane są wyłącznie adresy http:// i https://.' }))
      return
    }
    void start(async () => (await api.createObservationUrl(value, undefined, fetchLive ? 'fetch' : 'offline')).observation_id)
  }

  const submitFile = async (e: React.FormEvent) => {
    e.preventDefault()
    const file = fileRef.current?.files?.[0]
    if (!file) {
      setError(new ApiError(0, { code: 'validation_failed', message: 'Wybierz plik do analizy.' }))
      return
    }
    setBusy(true)
    setError(null)
    try {
      const zipName = /\.zip$/i.test(file.name)
      const magic = !zipName && typeof file.arrayBuffer === 'function' ? new Uint8Array(await file.slice(0, 4).arrayBuffer()) : []
      const zipHeader = magic[0] === 0x50 && magic[1] === 0x4b &&
        ((magic[2] === 3 && magic[3] === 4) || (magic[2] === 5 && magic[3] === 6))
      if (!zipName && !zipHeader) {
        await start(async () => (await api.createObservationFile(file)).observation_id)
        return
      }
      const report = await api.importCollection([file])
      onCollection(report.id)
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, { code: 'internal_error', message: 'Nieoczekiwany błąd.' }))
      setBusy(false)
    }
  }

  return (
    <section className="start-screen" aria-labelledby="start-h">
      <div className="start-header">
        <h1 id="start-h">Sprawdź, czy materiały są ze sobą powiązane</h1>
        <p>Analizuj zapisy stron, kreacje graficzne, PDF, wideo/audio (MP4/MP3) oraz adresy URL i odkrywaj powiązane kampanie.</p>
      </div>
      <div className="intake-layout">
        <div className="intake">
          <form className="file-form" onSubmit={submitFile}>
            <h2>Analiza pliku</h2>
            <label htmlFor="file">Zapis strony lub materiał</label>
            <input id="file" type="file" ref={fileRef} disabled={busy} aria-describedby="file-help" />
            <p id="file-help" className="muted">Obsługiwane: HAR, HTML, PDF, obrazy, MP4/WebM, MP3/WAV/OGG oraz archiwa ZIP.</p>
            <button type="submit" disabled={busy}>Analizuj plik <span aria-hidden="true">→</span></button>
          </form>
          <form className="url-form" onSubmit={submitUrl}>
            <h2>Analiza adresu URL lub domeny</h2>
            <label htmlFor="url">Adres strony lub reklamy</label>
            <div className="url-controls">
              <input id="url" type="text" inputMode="url" placeholder="np. example.com lub https://…" value={url}
                aria-describedby="url-help" disabled={busy} onChange={(e) => setUrl(e.target.value)} />
              <button type="submit" className="outline" disabled={busy}>Analizuj adres</button>
            </div>
            <label className="collection-upload-check">
              <input type="checkbox" checked={fetchLive} disabled={busy} onChange={(e) => setFetchLive(e.target.checked)} />
              <span>Pobierz stronę (bezpieczny fetch + przekierowania)</span>
            </label>
            <p id="url-help" className="muted">Obsługuje długie linki reklamowe z trackerami. Gdy strona nie odpowiada, zapisuje sam adres i domenę.</p>
          </form>
          <form className="url-form hint-form" role="search" onSubmit={(e) => { e.preventDefault(); if (hint.trim()) onHint(hint.trim()) }}>
            <h2>Wskazówka</h2>
            <label htmlFor="hint">Nazwa produktu, osoba, marka lub słowa kluczowe</label>
            <div className="url-controls">
              <input id="hint" type="search" placeholder="np. nazwa preparatu lub nazwisko" value={hint}
                aria-describedby="hint-help" onChange={(e) => setHint(e.target.value)} />
              <button type="submit" className="outline" disabled={!hint.trim()}>Szukaj w bazie</button>
            </div>
            <p id="hint-help" className="muted">Przeszukuje treść zaimportowanych materiałów, domeny i wspólne ślady. Wynik pojawia się w bazie materiałów.</p>
          </form>
          {busy && <p className="notice" role="status">Przesyłanie i uruchamianie analizy…</p>}
          {error && (
            <div role="alert" className="error">
              <strong>{error.isNetwork ? 'Brak połączenia z serwerem.' : 'Nie udało się uruchomić analizy.'}</strong> {error.message}
              {error.details?.map((d) => <div key={d.field + d.message}>{d.message}</div>)}
            </div>
          )}
        </div>
        <aside className="intake-guide">
          <div className="trace-diagram" aria-hidden="true">
            <div className="trace-source"><span /><span /><span /></div>
            <div className="trace-lines"><i /><i /><i /></div>
            <div className="trace-signals"><span>Grafiki</span><span>Tekst</span><span>Struktura HTML</span></div>
          </div>
          <div className="guide-content">
            <h2>Zobacz analizę na przykładzie</h2>
            <p>Otwórz gotowy wynik na jawnie syntetycznym zestawie: zobaczysz grupę wariantów jednej kampanii, osobny materiał i dowody powiązania.</p>
            <button type="button" disabled={busy} onClick={() => void start(async () => (await api.getDemo()).observation_id, 'demo')}>
              Uruchom przykład
            </button>
          </div>
          <div className="guide-content">
            <h2>Baza materiałów</h2>
            <p>Importuj paczki plików, archiwa ZIP i adresy URL, przeglądaj graf powiązań, oś czasu danych, wspólne ślady i scenariusze co-jeśli.</p>
            <button type="button" disabled={busy} onClick={() => onCollection()}>
              Otwórz bazę <span aria-hidden="true">→</span>
            </button>
          </div>
        </aside>
      </div>
    </section>
  )
}
