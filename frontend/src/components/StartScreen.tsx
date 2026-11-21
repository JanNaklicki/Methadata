import { useRef, useState } from 'react'
import { api, ApiError } from '../api/client'

export default function StartScreen({ onRun }: { onRun: (runId: string) => void }) {
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  async function start(createObservationId: () => Promise<string>) {
    setBusy(true)
    setError(null)
    try {
      const observationId = await createObservationId()
      const run = await api.createRun(observationId)
      onRun(run.run_id)
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, { code: 'internal_error', message: 'Nieoczekiwany błąd.' }))
      setBusy(false)
    }
  }

  const submitUrl = (e: React.FormEvent) => {
    e.preventDefault()
    const value = url.trim()
    if (!/^https?:\/\//i.test(value)) {
      setError(new ApiError(0, { code: 'validation_failed', message: 'Wpisz pełny adres zaczynający się od http:// lub https://.' }))
      return
    }
    void start(async () => (await api.createObservationUrl(value)).observation_id)
  }

  const submitFile = (e: React.FormEvent) => {
    e.preventDefault()
    const file = fileRef.current?.files?.[0]
    if (!file) {
      setError(new ApiError(0, { code: 'validation_failed', message: 'Wybierz plik do analizy.' }))
      return
    }
    void start(async () => (await api.createObservationFile(file)).observation_id)
  }

  return (
    <section aria-labelledby="start-h">
      <h1 id="start-h">Sprawdź, czy materiały są ze sobą powiązane</h1>
      <div className="cards">
        <div className="card">
          <h2>Gotowy przykład</h2>
          <p>Uruchom analizę na małym zestawie dostarczanym z projektem.</p>
          <button type="button" disabled={busy} onClick={() => void start(async () => (await api.getDemo()).observation_id)}>
            Uruchom przykład
          </button>
        </div>
        <form className="card" onSubmit={submitUrl}>
          <h2>Adres URL</h2>
          <label htmlFor="url">Adres strony lub reklamy</label>
          <input id="url" type="text" inputMode="url" placeholder="https://..." value={url} onChange={(e) => setUrl(e.target.value)} />
          <p className="muted">Adres nie jest pobierany; analizowany jest tylko jego zapis, więc część porównań może być niewykonalna.</p>
          <button type="submit" disabled={busy}>Analizuj adres</button>
        </form>
        <form className="card" onSubmit={submitFile}>
          <h2>Plik</h2>
          <label htmlFor="file">Zapis strony lub materiał (ZIP, HAR, HTML, obraz, MP4, MP3; do 50 MiB)</label>
          <input id="file" type="file" ref={fileRef} />
          <button type="submit" disabled={busy}>Analizuj plik</button>
        </form>
      </div>
      {busy && <p role="status">Przesyłanie i uruchamianie analizy…</p>}
      {error && (
        <div role="alert" className="error">
          <strong>{error.isNetwork ? 'Brak połączenia z serwerem.' : 'Nie udało się uruchomić analizy.'}</strong> {error.message}
          {error.details?.map((d) => <div key={d.field + d.message}>{d.message}</div>)}
        </div>
      )}
    </section>
  )
}
