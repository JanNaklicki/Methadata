import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { mockFetch } from '../test/mockApi'
import MonitorPanel from './MonitorPanel'

const source = { kind: 'json-export', name: 'sample_feed.jsonl', available: true, synthetic: true }

describe('skan źródła reklam', () => {
  it('uruchamia skan i pokazuje, co jest nowe', async () => {
    const fetchMock = mockFetch(({ url, method }) => {
      if (url.endsWith('/monitor/scan') && method === 'POST') {
        return { body: { ok: true, source, new: 4, updated: 0, duplicate: 0, rejected: 0, payer_groups_touched: 1,
          state: { last_run: '2026-11-22T10:00:00Z', totals: { new: 4 } } } }
      }
      return { body: { source, state: {} } }
    })
    render(<MonitorPanel />)
    expect(await screen.findByText(/syntetyczny przykład/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Skanuj źródło' }))
    const result = await screen.findByRole('status', { name: 'Wynik skanu' })
    expect(result).toHaveTextContent('Nowe4')
    expect(result).toHaveTextContent('Grupy płatników z nowymi1')
    expect(fetchMock.mock.calls.some(([u, i]) => String(u).endsWith('/monitor/scan') && (i as RequestInit).method === 'POST')).toBe(true)
  })

  it('błąd skanu pokazuje komunikat zamiast pustego wyniku', async () => {
    mockFetch(({ url, method }) => url.endsWith('/monitor/scan') && method === 'POST'
      ? { body: { ok: false, source, error: 'Nie znaleziono pliku źródła (METHADATA_MONITOR_FEED).' } }
      : { body: { source, state: {} } })
    render(<MonitorPanel />)
    await userEvent.click(await screen.findByRole('button', { name: 'Skanuj źródło' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Nie znaleziono pliku źródła')
  })
})
