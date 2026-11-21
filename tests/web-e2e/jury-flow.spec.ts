import { readFileSync, writeFileSync } from 'node:fs'
import { test, expect, type Page } from '@playwright/test'
import type { Evidence, ObservationCreated, Run, RunAccepted } from '../../frontend/src/api/types'
import type { ScenarioComparison } from '../../frontend/src/counterfactual/types'

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

async function checkScenarios(page: Page, path: string, targetLabel: string, labels: Map<string, string>) {
  const panel = page.getByRole('region', { name: 'Wpływ rodzin śladów' })
  const baselineColumn = panel.locator('.side').filter({ has: page.getByRole('heading', { name: 'Wynik bazowy' }) })
  const scenarioColumn = panel.locator('.side').filter({ has: page.getByRole('heading', { name: 'Po wyłączeniu' }) })
  const names = { asset: 'Grafiki', dom: 'Struktura strony (DOM)', text: 'Tekst' }
  const checkboxes = Object.values(names).map((name) => panel.getByRole('checkbox', { name }))
  const get = async (requestPath: string) => {
    const response = await page.request.get(requestPath)
    expect(response.status()).toBe(200)
    return response.json() as Promise<ScenarioComparison>
  }
  const apply = async (action: () => Promise<void>, disabled: string[]) => {
    const [response] = await Promise.all([
      page.waitForResponse((r) => {
        const url = new URL(r.url())
        return url.pathname === path && url.searchParams.getAll('disabled').join(',') === disabled.join(',')
      }),
      action(),
    ])
    expect(response.status()).toBe(200)
    const comparison = await response.json() as ScenarioComparison
    expect(comparison.disabled).toEqual(disabled)
    await expect(panel.getByRole('status')).toHaveText(disabled.length ? 'Pokazano przeliczenie.' : 'Pokazano wynik bazowy.')
    await expect(baselineColumn).toContainText('100%')
    return comparison
  }

  await expect(panel.getByRole('status')).toHaveText('Pokazano wynik bazowy.')
  const baseline = await get(path)
  expect(baseline.families).toEqual(Object.keys(names))
  expect(baseline.baseline).toEqual(baseline.scenario)
  expect(baseline.baseline.group.map((id) => labels.get(id)).sort()).toEqual(['p2', 'p7', targetLabel].sort())
  expect(baseline.baseline.indirect_paths).toEqual([])
  const snapshots = [{ path, comparison: baseline }]
  for (const [family, name] of Object.entries(names)) {
    const checkbox = panel.getByRole('checkbox', { name })
    const single = await apply(() => checkbox.uncheck(), [family])
    expect(single.scenario.pair.coverage).toBeCloseTo(2 / 3)
    expect(single.scenario.group).toEqual(baseline.baseline.group)
    expect(single.edge_changes.map((edge) => edge.kind)).toEqual(['zmiana_parametrow', 'zmiana_parametrow'])
    await expect(scenarioColumn).toContainText(/66,7\s*%/)
    snapshots.push({ path: `${path}?disabled=${family}`, comparison: single })
    await apply(() => checkbox.check(), [])
  }

  await apply(() => checkboxes[0].uncheck(), ['asset'])
  const double = await apply(() => checkboxes[1].uncheck(), ['asset', 'dom'])
  expect(double.scenario.pair.status).toBe('insufficient_coverage')
  expect(double.scenario.pair.coverage).toBeCloseTo(1 / 3)
  expect(double.scenario.group).toEqual([baseline.target_id])
  expect(double.edge_changes.map((edge) => edge.kind)).toEqual(['utrata_pokrycia', 'utrata_pokrycia'])
  await expect(scenarioColumn).toContainText('Pokrycie poniżej minimum.')
  await expect(panel.getByText(/Odłączono od grupy/)).toContainText('p2')
  await expect(panel.getByText(/Odłączono od grupy/)).toContainText('p7')
  snapshots.push({ path: `${path}?disabled=asset&disabled=dom`, comparison: double })

  const all = await apply(() => checkboxes[2].uncheck(), ['asset', 'dom', 'text'])
  expect(all.scenario.pair.status).toBe('no_evidence')
  await expect(scenarioColumn).toContainText('Brak porównywalnych śladów.')
  snapshots.push({ path: `${path}?disabled=asset&disabled=dom&disabled=text`, comparison: all })

  const restored = await apply(() => panel.getByRole('button', { name: 'Przywróć wynik bazowy' }).click(), [])
  expect(restored).toEqual(baseline)
  for (const checkbox of checkboxes) await expect(checkbox).toBeChecked()
  await expect(scenarioColumn).toHaveCount(0)
  return snapshots
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
  const savedScenarios: { path: string; comparison: ScenarioComparison }[] = []
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
    if (label === 'p2') {
      savedScenarios.push(...await checkScenarios(page, `${match.evidence_url}/scenarios`, target.label, labels))
    }
    await page.getByRole('button', { name: 'Zamknij' }).click()
  }

  const narrative = result.pair_evidence.find((p) => p.pair.map((id) => labels.get(id)).sort().join('/') === 'p3/p4')
  expect(narrative?.explanation).toContain('Podobieństwo narracji i szablonu')
  const indexPair = narrative!
  const pairLabels = indexPair.pair.map((id) => labels.get(id))
  await page.getByRole('button', { name: `Pokaż dowody: ${pairLabels.join(' i ')}`, exact: true }).click()
  const panel = page.getByRole('region', { name: 'Wpływ rodzin śladów' })
  const material = panel.getByRole('combobox', { name: 'Materiał objęty wyłączeniem' })
  await expect(material).toHaveValue(indexPair.pair[0])
  await material.selectOption(indexPair.pair[1])
  await expect(material).toHaveValue(indexPair.pair[1])
  const indexChange = page.waitForResponse((response) => {
    const url = new URL(response.url())
    return url.pathname === `${indexPair.evidence_url}/scenarios` &&
      url.searchParams.get('material_id') === indexPair.pair[1] && url.searchParams.get('disabled') === 'dom'
  })
  await panel.getByRole('checkbox', { name: 'Struktura strony (DOM)' }).uncheck()
  const indexComparison = await (await indexChange).json() as ScenarioComparison
  expect(indexComparison.target_id).toBe(indexPair.pair[1])
  expect(indexComparison.edge_changes.every((edge) => edge.pair_ids.includes(indexPair.pair[1]))).toBe(true)
  await expect(panel.getByRole('status')).toHaveText('Pokazano przeliczenie.')
  console.log(result.cluster_run.dataset_version, run.run_id, savedEvidence.map((e) => [e.sides.map((s) => s.label), e.score]))
  writeFileSync(snapshotPath!, JSON.stringify({ state, evidence: savedEvidence, scenarios: savedScenarios }))
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

test('jury: po restarcie odtwarza przebieg, dowody i scenariusze', async ({ page }) => {
  test.skip(!snapshotPath || !replay, 'Wymaga migawki zapisanej przed restartem i JURY_REPLAY=1')
  const snapshot = JSON.parse(readFileSync(snapshotPath!, 'utf-8')) as {
    state: Run; evidence: Evidence[]; scenarios: { path: string; comparison: ScenarioComparison }[]
  }
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
  expect(snapshot.scenarios).toHaveLength(6)
  for (const { path, comparison } of snapshot.scenarios) {
    const response = await page.request.get(path)
    expect(response.status()).toBe(200)
    expect(await response.json()).toEqual(comparison)
  }
})
