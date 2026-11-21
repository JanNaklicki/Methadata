import { readFileSync, writeFileSync } from 'node:fs'
import { test, expect, type Page } from '@playwright/test'
import type { Evidence, ObservationCreated, Run, RunAccepted } from '../../frontend/src/api/types'

const packagePath = process.env.JURY_UPLOAD
const snapshotPath = process.env.JURY_SNAPSHOT
const replay = process.env.JURY_REPLAY === '1'
const indexLabels = Array.from({ length: 13 }, (_, i) => `p${i + 2}`).sort()

async function submit(page: Page, button: string) {
  const [observation, run] = await Promise.all([
    page.waitForResponse((r) => new URL(r.url()).pathname === '/api/v1/observations' && r.request().method() === 'POST')
      .then(async (r) => {
        expect([200, 201]).toContain(r.status())
        return r.json() as Promise<ObservationCreated>
      }),
    page.waitForResponse((r) => new URL(r.url()).pathname === '/api/v1/runs' && r.request().method() === 'POST')
      .then(async (r) => {
        expect(r.status()).toBe(202)
        return r.json() as Promise<RunAccepted>
      }),
    page.getByRole('button', { name: button }).click(),
  ])
  expect(run.observation_id).toBe(observation.observation_id)
  await expect(page).toHaveURL(new RegExp(`/runs/${run.run_id}$`))
  await expect(page.getByTestId('summary')).toBeVisible()
  const response = await page.request.get(run.status_url)
  expect(response.ok()).toBe(true)
  const state = await response.json() as Run
  if (state.status !== 'succeeded') throw new Error(`Przebieg ma status ${state.status}`)
  const result = state.result
  expect(result.cluster_run).toMatchObject({
    run_id: run.run_id, relation: 'campaign_candidate', config: { fusion: { min_support: 2 } },
  })
  expect(result.materials).toHaveLength(14)
  expect(result.pair_evidence).toHaveLength(91)
  expect(result.pair_evidence.every((p) => p.method_id === 'fusion-v1')).toBe(true)
  return { run, observation, state, result }
}

async function checkEvidenceSides(page: Page, labels: string[]) {
  const sides = page.getByTestId('evidence-side')
  await expect(sides).toHaveCount(2)
  for (const label of labels) {
    const side = sides.filter({ has: page.getByRole('heading', { name: label, exact: true }) })
    const image = side.locator('img').first()
    await expect(image).toBeVisible()
    await expect.poll(() => image.evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth > 0)).toBe(true)
  }
}

test('jury: pakiet p15 prowadzi do dowodów po obu stronach połączeń z p2 i p7', async ({ page }) => {
  test.skip(!packagePath || !snapshotPath || replay, 'Wymaga przygotowanego profilu jury i ścieżki zapisu')
  await page.goto('/')
  await page.getByLabel(/Zapis strony lub materiał/).setInputFiles(packagePath!)
  const { run, observation, state, result } = await submit(page, 'Analizuj plik')
  expect(observation.artifacts?.some((a) => a.role === 'resource' && a.media_type?.startsWith('image/'))).toBe(true)

  const target = result.materials.find((m) => m.observation_id === run.observation_id)!
  expect(target.available_evidence).toEqual(['asset', 'dom', 'text'])
  expect(result.materials.filter((m) => m !== target).map((m) => m.label).sort()).toEqual(indexLabels)
  const labels = new Map(result.materials.map(({ material_id, label }) => [material_id, label]))
  const group = result.clusters.find((c) => c.material_ids.includes(target.material_id))
  expect(group?.material_ids.map((id) => labels.get(id)).sort()).toEqual([target.label, 'p2', 'p7'].sort())
  const visibleGroup = page.getByRole('article').filter({ has: page.getByText(target.label, { exact: true }) })
  await expect(visibleGroup).toContainText('Hipoteza kampanii')
  for (const label of ['p2', 'p7']) {
    await expect(visibleGroup.getByText(label, { exact: true })).toBeVisible()
  }

  const savedEvidence: Evidence[] = []
  const edges = result.cluster_run.edges as { pair_evidence_id: string }[]
  for (const label of ['p2', 'p7']) {
    const match = result.pair_evidence.find((p) =>
      p.pair.includes(target.material_id) && p.pair.some((id) => labels.get(id) === label))!
    expect(edges.some((edge) => edge.pair_evidence_id === match.evidence_id)).toBe(true)
    const names = match.pair.map((id) => labels.get(id))
    await page.getByRole('button', { name: `Pokaż dowody: ${names.join(' i ')}`, exact: true }).click()
    await checkEvidenceSides(page, [label, target.label])
    const response = await page.request.get(match.evidence_url)
    expect(response.ok()).toBe(true)
    const evidence = await response.json() as Evidence
    expect(evidence).toMatchObject({ available: true, run_id: run.run_id, evidence_id: match.evidence_id })
    savedEvidence.push(evidence)
    await page.getByRole('button', { name: 'Zamknij' }).click()
  }

  const narrative = result.pair_evidence.find((p) => p.pair.map((id) => labels.get(id)).sort().join('/') === 'p3/p4')
  expect(narrative?.explanation).toContain('Podobieństwo narracji i szablonu')
  console.log(result.cluster_run.dataset_version, run.run_id, savedEvidence.map((e) => [e.sides.map((s) => s.label), e.score]))
  writeFileSync(snapshotPath!, JSON.stringify({ state, evidence: savedEvidence }))
})

test('jury: sam URL nie ma ocenionych porównań, a indeks pomija p15', async ({ page }) => {
  test.skip(!packagePath || replay, 'Wymaga przygotowanego profilu jury')
  const url = 'https://example.invalid/r02-url-only'
  await page.goto('/')
  await page.getByLabel('Adres strony lub reklamy').fill(url)
  const { observation, result } = await submit(page, 'Analizuj adres')
  expect(observation.input_kind).toBe('url_only')
  const id = observation.canonical_id
  expect(result.materials.filter((m) => m.material_id !== id).map((m) => m.label).sort()).toEqual(indexLabels)
  const pairs = result.pair_evidence.filter((p) => p.pair.includes(id))
  expect(pairs).toHaveLength(13)
  expect(pairs.every((p) => p.score === null)).toBe(true)
  expect(result.clusters.every((c) => !c.material_ids.includes(id))).toBe(true)
  await expect(page.getByRole('heading', { name: 'Za mało danych do rozstrzygnięcia' })).toBeVisible()
})

test('jury: po restarcie odtwarza identyczny przebieg i oba dowody', async ({ page }) => {
  test.skip(!snapshotPath || !replay, 'Wymaga migawki zapisanej przed restartem i JURY_REPLAY=1')
  const snapshot = JSON.parse(readFileSync(snapshotPath!, 'utf-8')) as { state: Run; evidence: Evidence[] }
  const runPath = `/runs/${snapshot.state.run_id}`
  await page.goto(runPath)
  await expect(page.getByTestId('summary')).toBeVisible()
  const state = await page.request.get(`/api/v1${runPath}`)
  expect(state.ok()).toBe(true)
  expect(await state.json()).toEqual(snapshot.state)
  for (const saved of snapshot.evidence) {
    const path = `${runPath}/evidence/${saved.evidence_id}`
    await page.goto(path)
    await checkEvidenceSides(page, saved.sides.map((side) => side.label))
    const evidence = await page.request.get(`/api/v1${path}`)
    expect(evidence.ok()).toBe(true)
    expect(await evidence.json()).toEqual(saved)
  }
})
