import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ClusterRunView } from '../api/types'
import { ex } from '../test/mockApi'
import ArtifactGraph from './ArtifactGraph'

const base = ex.succeeded.result as unknown as ClusterRunView
const [a, b] = base.materials.map((m) => m.material_id)

function renderWith(artifact: object) {
  const result = { ...base, investigation: { ...(base.investigation ?? {}), artifacts: [artifact], domains: {}, captured_at: {} } } as unknown as ClusterRunView
  return render(<ArtifactGraph result={result} materialIds={new Set([a, b])} onMaterial={vi.fn()} />)
}

describe('wspólne ślady', () => {
  it('pokazuje podgląd grafiki i miniaturę na liście', async () => {
    renderWith({ kind: 'image', label: 'abcdef0123456789', material_ids: [a, b],
      preview: { href: '/static/previews/abcdef.png', media_type: 'image/png', width: 400, height: 300, alt: 'Wspólna grafika abcdef01' } })
    const trace = (await screen.findAllByRole('button', { name: /Grafika · abcdef01/ }))[0]
    expect(trace.querySelector('img')).toHaveAttribute('src', '/static/previews/abcdef.png')
    await userEvent.click(trace)
    const detail = document.querySelector('.artifact-detail') as HTMLElement
    expect(within(detail).getByRole('img', { name: 'Wspólna grafika abcdef01' })).toHaveAttribute('src', '/static/previews/abcdef.png')
    expect(within(detail).getByText('400×300 px')).toBeInTheDocument()
  })

  it('bez podglądu wyjaśnia brak i nie pokazuje obrazu', async () => {
    renderWith({ kind: 'image', label: 'abcdef0123456789', material_ids: [a, b] })
    await userEvent.click((await screen.findAllByRole('button', { name: /Grafika · abcdef01/ }))[0])
    const detail = document.querySelector('.artifact-detail') as HTMLElement
    expect(within(detail).getByText(/Podgląd tej grafiki jest niedostępny/)).toBeInTheDocument()
    expect(detail.querySelector('figure')).toBeNull()
  })
})
