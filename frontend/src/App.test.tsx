import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import App from './App'
import { ex, mockFetch, standardApi } from './test/mockApi'

const renderApp = () => render(<App pollMs={10} />)
const calls = (fn: ReturnType<typeof mockFetch>) => fn.mock.calls.map(([u, i]) => `${(i as RequestInit | undefined)?.method ?? 'GET'} ${String(u)}`)

describe('ekran startowy', () => {
  it('demo: tworzy przebieg, zapisuje run_id w adresie i pokazuje wynik', async () => {
    const fetchMock = standardApi([ex.queued, ex.running, ex.succeeded])
    renderApp()
    await userEvent.click(screen.getByRole('button', { name: 'Uruchom przykład' }))
    await waitFor(() => expect(window.location.pathname).toBe('/runs/run-demo-0001'))
    expect(await screen.findByText(/Znaleziono grupy/)).toBeInTheDocument()
    expect(calls(fetchMock).slice(0, 3)).toEqual(['GET /api/v1/demo', 'POST /api/v1/runs', 'GET /api/v1/runs/run-demo-0001'])
    expect(JSON.parse(fetchMock.mock.calls[1][1]!.body as string)).toEqual({ observation_id: 'obs-9271e8a859000be5', profile: 'demo' })
  })

  it('URL: wysyła JSON z trybem offline', async () => {
    const fetchMock = standardApi()
    renderApp()
    await userEvent.type(screen.getByLabelText(/Adres strony/), 'https://example.invalid/reklama/1')
    await userEvent.click(screen.getByRole('button', { name: 'Analizuj adres' }))
    await screen.findByText(/Znaleziono grupy/)
    const post = fetchMock.mock.calls.find(([u]) => String(u).endsWith('/observations'))!
    expect(JSON.parse(post[1]!.body as string)).toEqual({ url: 'https://example.invalid/reklama/1', mode: 'offline' })
    expect(window.location.pathname).toBe('/runs/run-demo-0001')
  })

  it('URL: odrzuca adres bez http(s) bez zapytania do API', async () => {
    const fetchMock = standardApi()
    renderApp()
    await userEvent.type(screen.getByLabelText(/Adres strony/), 'example.com')
    await userEvent.click(screen.getByRole('button', { name: 'Analizuj adres' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('http://')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('plik: wysyła multipart z polem file', async () => {
    const fetchMock = standardApi()
    renderApp()
    const file = new File(['<html></html>'], 'strona.html', { type: 'text/html' })
    await userEvent.upload(screen.getByLabelText(/Zapis strony lub materiał/), file)
    await userEvent.click(screen.getByRole('button', { name: 'Analizuj plik' }))
    await screen.findByText(/Znaleziono grupy/)
    const post = fetchMock.mock.calls.find(([u]) => String(u).endsWith('/observations'))!
    const body = post[1]!.body as FormData
    expect(body).toBeInstanceOf(FormData)
    expect((body.get('file') as File).name).toBe('strona.html')
    expect((post[1]!.headers as Record<string, string> | undefined)?.['Content-Type']).toBeUndefined()
  })

  it('błąd API przy dodawaniu: pokazuje komunikat z kontraktu', async () => {
    mockFetch(({ url }) => (url.endsWith('/observations') ? { status: 422, body: ex.errValidation } : 'network'))
    renderApp()
    await userEvent.type(screen.getByLabelText(/Adres strony/), 'https://x.invalid/a')
    await userEvent.click(screen.getByRole('button', { name: 'Analizuj adres' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Podany adres jest niepoprawny')
    expect(window.location.pathname).toBe('/')
  })

  it('brak połączenia przy starcie: sygnalizuje utratę połączenia', async () => {
    mockFetch(() => 'network')
    renderApp()
    await userEvent.click(screen.getByRole('button', { name: 'Uruchom przykład' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Brak połączenia z serwerem')
  })
})

describe('widok przebiegu', () => {
  it('odświeżenie widoku: odtwarza przebieg z adresu i odpytuje do sukcesu', async () => {
    window.history.pushState(null, '', '/runs/run-demo-0001')
    const fetchMock = standardApi([ex.queued, ex.running, ex.succeeded])
    renderApp()
    expect(await screen.findByText(/Analiza czeka w kolejce/)).toBeInTheDocument()
    expect(await screen.findByText(/Porównywanie grafik/)).toBeInTheDocument()
    expect(await screen.findByText(/Znaleziono grupy/)).toBeInTheDocument()
    expect(calls(fetchMock).every((c) => c === 'GET /api/v1/runs/run-demo-0001')).toBe(true)
  })

  it('sukces: grupa, porównywane materiały i dowody', async () => {
    window.history.pushState(null, '', '/runs/run-demo-0001')
    standardApi()
    renderApp()
    const group = await screen.findByRole('article', { name: 'Grupa c1' })
    expect(within(group).getByText('Reklama p15 (zapis strony)')).toBeInTheDocument()
    expect(screen.getAllByRole('listitem').length).toBeGreaterThan(4)
    expect(screen.getAllByRole('button', { name: /Pokaż dowody/ })).toHaveLength(2)
    expect(screen.getAllByRole('img').length).toBe(4)
  })

  it('otwieranie dowodu: obie strony, tekst jako tekst, adres z evidence_id', async () => {
    window.history.pushState(null, '', '/runs/run-demo-0001')
    const evidence = structuredClone(ex.evidenceFull)
    evidence.sides[0].items[1].text_excerpts[0].text = '<img src=x onerror=alert(1)> Fragment'
    evidence.sides[0].items[1].text_excerpts[0].highlight_end = 0
    mockFetch(({ url }) =>
      /\/evidence\//.test(url) ? { body: evidence } : { body: ex.succeeded })
    const { container } = renderApp()
    await userEvent.click((await screen.findAllByRole('button', { name: /Pokaż dowody/ }))[0])
    const sides = await screen.findAllByTestId('evidence-side')
    expect(sides).toHaveLength(2)
    expect(window.location.pathname).toBe('/runs/run-demo-0001/evidence/pev-89be6f276d83bdae')
    expect(within(sides[0]).getByText(/<img src=x onerror=alert\(1\)>/, { exact: false })).toBeInTheDocument()
    expect(container.querySelector('.evidence img[src="x"]')).toBeNull()
    expect(within(sides[1]).getByRole('img')).toHaveAttribute('src', '/static/previews/c4d42b36d41f49b6-hero.webp')
    expect(within(sides[1]).getByText(/Fragment szablonu/)).toBeInTheDocument()
  })

  it('odświeżenie z otwartym dowodem odtwarza dowód', async () => {
    window.history.pushState(null, '', '/runs/run-demo-0001/evidence/pev-89be6f276d83bdae')
    standardApi()
    renderApp()
    expect(await screen.findAllByTestId('evidence-side')).toHaveLength(2)
  })

  it('niewystarczające dane: komunikat i niedostępny dowód', async () => {
    window.history.pushState(null, '', '/runs/run-demo-0002')
    standardApi([ex.noEvidence])
    renderApp()
    expect(await screen.findByText('Za mało danych do rozstrzygnięcia')).toBeInTheDocument()
    expect(screen.getByText(/Za mało dowodów: dodano sam adres URL/)).toBeInTheDocument()
    expect(screen.getByText('Brak grup w tym wyniku.')).toBeInTheDocument()
    expect(screen.getAllByText(/Tylko adres URL, brak dowodów/)).toHaveLength(2)
    await userEvent.click(screen.getByRole('button', { name: /Pokaż dowody/ }))
    expect(await screen.findByText('Porównanie niewykonalne.')).toBeInTheDocument()
    expect(screen.getAllByText('Brak dowodów po tej stronie.')).toHaveLength(2)
  })

  it('pusty wynik: brak grup i dowodów bez błędu', async () => {
    window.history.pushState(null, '', '/runs/run-demo-0001')
    const empty = structuredClone(ex.succeeded) as typeof ex.succeeded
    Object.assign(empty.result, { clusters: [], pair_evidence: [], materials: [] })
    Object.assign(empty.result.summary, { outcome: 'no_groups', grouped: 0, material_count: 0, message: 'Nic nie połączono.' })
    mockFetch(() => ({ body: empty }))
    renderApp()
    expect(await screen.findByText('Nie znaleziono grup')).toBeInTheDocument()
    expect(screen.getByText('Brak grup w tym wyniku.')).toBeInTheDocument()
    expect(screen.getByText('Brak dowodów do wyświetlenia.')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('przebieg zakończony błędem pokazuje komunikat', async () => {
    window.history.pushState(null, '', '/runs/run-demo-0003')
    standardApi([ex.running, ex.failed])
    renderApp()
    expect(await screen.findByRole('alert')).toHaveTextContent('Analiza nie powiodła się')
  })

  it('błąd API 404: komunikat i koniec odpytywania', async () => {
    window.history.pushState(null, '', '/runs/nieznany')
    const fetchMock = mockFetch(() => ({ status: 404, body: ex.errRunNotFound }))
    renderApp()
    expect(await screen.findByRole('alert')).toHaveTextContent(ex.errRunNotFound.error.message)
    await new Promise((r) => setTimeout(r, 60))
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('utrata połączenia: sygnalizuje i wznawia odpytywanie', async () => {
    window.history.pushState(null, '', '/runs/run-demo-0001')
    let n = 0
    mockFetch(() => (++n <= 2 ? 'network' : { body: ex.succeeded }))
    renderApp()
    expect(await screen.findByText(/Utracono połączenie z serwerem/)).toBeInTheDocument()
    expect(await screen.findByText(/Znaleziono grupy/)).toBeInTheDocument()
    expect(screen.queryByText(/Utracono połączenie/)).toBeNull()
  })

  it('zamknięcie widoku zatrzymuje odpytywanie', async () => {
    window.history.pushState(null, '', '/runs/run-demo-0001')
    const fetchMock = standardApi([ex.running])
    const { unmount } = renderApp()
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(1))
    unmount()
    const after = fetchMock.mock.calls.length
    await new Promise((r) => setTimeout(r, 80))
    expect(fetchMock.mock.calls.length).toBe(after)
  })

  it('odpytywanie kończy się na stanie końcowym', async () => {
    window.history.pushState(null, '', '/runs/run-demo-0001')
    const fetchMock = standardApi([ex.succeeded])
    renderApp()
    await screen.findByText(/Znaleziono grupy/)
    const n = fetchMock.mock.calls.length
    await new Promise((r) => setTimeout(r, 60))
    expect(fetchMock.mock.calls.length).toBe(n)
    vi.unstubAllGlobals()
  })
})
