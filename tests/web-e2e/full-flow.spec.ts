import { test as base, expect, type APIRequestContext, type Page } from '@playwright/test'
import type { Demo, Evidence, ObservationCreated, Run, RunAccepted } from '../../frontend/src/api/types'

const API_URL = process.env.API_URL ?? 'http://127.0.0.1:8000'
const labelA = 'Reklama A (przykład syntetyczny)'
const labelB = 'Reklama B (przykład syntetyczny)'
const labelC = 'Reklama C (przykład syntetyczny)'

const test = base.extend<{ diagnostics: string[] }>({
  diagnostics: [
    async ({ page }, use, testInfo) => {
      const lines: string[] = []
      page.on('console', (m) => lines.push(`console.${m.type()}: ${m.text()}`))
      page.on('pageerror', (e) => lines.push(`pageerror: ${e.message}`))
      page.on('requestfailed', (r) => lines.push(`requestfailed: ${r.method()} ${r.url()} ${r.failure()?.errorText}`))
      page.on('response', (r) => {
        if (/^\/(api|static)\//.test(new URL(r.url()).pathname)) lines.push(`${r.status()} ${r.request().method()} ${r.url()}`)
      })
      await use(lines)
      await testInfo.attach('diagnostyka.txt', { body: lines.join('\n') || '(brak)', contentType: 'text/plain' })
      if (testInfo.status !== testInfo.expectedStatus) console.log(`--- diagnostyka: ${testInfo.title}\n${lines.join('\n')}`)
    },
    { auto: true },
  ],
})

async function startRun(page: Page, source: 'demo' | 'observations', action: () => Promise<void>) {
  const method = source === 'demo' ? 'GET' : 'POST'
  const path = `/api/v1/${source}`
  const [observation, run] = await Promise.all([
    page.waitForResponse((r) => new URL(r.url()).pathname === path && r.request().method() === method)
      .then(async (r) => {
        expect(source === 'demo' ? [200] : [200, 201], `${method} ${path} zwrócił HTTP ${r.status()}`).toContain(r.status())
        return await r.json() as Demo | ObservationCreated
      }),
    page.waitForResponse((r) => new URL(r.url()).pathname === '/api/v1/runs' && r.request().method() === 'POST')
      .then(async (r) => {
        expect(r.status(), 'POST /api/v1/runs').toBe(202)
        return await r.json() as RunAccepted
      }),
    action(),
  ])
  expect(run.observation_id).toBe(observation.observation_id)
  return { run, observation }
}

async function readApi<T>(request: APIRequestContext, path: string, diagnostics: string[]): Promise<T> {
  const url = new URL(path, API_URL).href
  let response
  try {
    response = await request.get(url, { timeout: 10_000 })
  } catch (error) {
    diagnostics.push(`requestfailed: GET ${url} ${error}`)
    throw error
  }
  diagnostics.push(`${response.status()} GET ${url}`)
  expect(response.status(), `GET ${url}`).toBe(200)
  return response.json()
}

async function checkRun(page: Page, request: APIRequestContext, accepted: RunAccepted, diagnostics: string[]) {
  const path = `/runs/${accepted.run_id}`
  await expect(page).toHaveURL((url) => url.pathname === path)
  await expect(page.locator('.run-id code')).toHaveText(accepted.run_id)
  await expect(page.getByTestId('summary')).toBeVisible()

  const run = await readApi<Run>(request, `/api/v1${path}`, diagnostics)
  expect(run).toMatchObject({
    run_id: accepted.run_id,
    observation_id: accepted.observation_id,
    status: 'succeeded',
    result: { cluster_run: { run_id: accepted.run_id } },
  })
  return (run as Extract<Run, { status: 'succeeded' }>).result
}

test('przykład pokazuje grupę i dowód obu stron oraz odtwarza widok po odświeżeniu', async ({ page, request, diagnostics }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: /Sprawdź, czy materiały/ })).toBeVisible()

  const { run } = await startRun(page, 'demo', () => page.getByRole('button', { name: 'Uruchom przykład' }).click())
  const result = await checkRun(page, request, run, diagnostics)

  expect(result.materials.map(({ label }) => label).sort()).toEqual([labelA, labelB, labelC])
  const labels = new Map(result.materials.map(({ material_id, label }) => [material_id, label]))
  expect(result.clusters).toHaveLength(1)
  expect(result.clusters[0].material_ids.map((id) => labels.get(id)).sort()).toEqual([labelA, labelC])

  const summary = page.getByTestId('summary')
  await expect(summary.getByRole('heading')).toHaveText('Znaleziono grupy')
  const group = page.getByRole('article').getByRole('listitem')
  await expect(group).toHaveCount(2)
  for (const label of [labelA, labelC]) await expect(group.getByText(label, { exact: true })).toBeVisible()
  await expect(page.locator('li.material')).toHaveCount(3)
  await expect(page.locator('li.material').getByText(labelB, { exact: true })).toBeVisible()

  const evidence = result.pair_evidence.find(({ pair }) => {
    const [first, second] = pair.map((id) => labels.get(id)).sort()
    return first === labelA && second === labelC
  })
  expect(evidence, 'Wynik API zawiera dowód dla A i C').toBeDefined()
  const evidenceId = evidence!.evidence_id
  const runPath = `/runs/${run.run_id}`
  const evidencePath = `${runPath}/evidence/${evidenceId}`

  await page.getByRole('button', { name: /Pokaż dowody: .*Reklama A.*Reklama C|Pokaż dowody: .*Reklama C.*Reklama A/ }).click()
  await expect(page).toHaveURL((url) => url.pathname === evidencePath)
  const apiEvidence = await readApi<Evidence>(request, `/api/v1${evidencePath}`, diagnostics)
  expect(apiEvidence).toMatchObject({ run_id: run.run_id, evidence_id: evidenceId, available: true })
  expect(apiEvidence.sides.map(({ label }) => label).sort()).toEqual([labelA, labelC])

  const sides = page.getByTestId('evidence-side')
  await expect(sides).toHaveCount(2)
  for (const label of [labelA, labelC]) {
    const side = sides.filter({ has: page.getByRole('heading', { name: label, exact: true }) })
    const image = side.locator('img').first()
    await expect(image).toBeVisible()
    await expect.poll(() => image.evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth > 0)).toBe(true)
  }

  await page.reload()
  await expect(page).toHaveURL((url) => url.pathname === evidencePath)
  await expect(sides).toHaveCount(2)

  await page.getByRole('button', { name: 'Zamknij' }).click()
  await expect(page).toHaveURL((url) => url.pathname === runPath)
  await page.reload()
  await expect(page).toHaveURL((url) => url.pathname === runPath)
  await expect(page.locator('.run-id code')).toHaveText(run.run_id)
  await expect(summary.getByRole('heading')).toHaveText('Znaleziono grupy')
})

test('drugi materiał z pliku o dowolnej nazwie daje przebieg', async ({ page, request, diagnostics }) => {
  await page.goto('/')
  await page.getByLabel(/Zapis strony lub materiał/).setInputFiles({
    name: 'dowolna nazwa (1).dat',
    mimeType: 'application/octet-stream',
    buffer: Buffer.from('<html><body><h1>Syntetyczny materiał testowy W04</h1></body></html>'),
  })
  const { run } = await startRun(page, 'observations', () => page.getByRole('button', { name: 'Analizuj plik' }).click())
  const result = await checkRun(page, request, run, diagnostics)
  const material = result.materials.find(({ observation_id }) => observation_id === run.observation_id)
  expect(material?.label).toContain('dowolna_nazwa')
  await expect(page.locator('li.material').getByText(material!.label, { exact: true })).toBeVisible()
})

test('sam URL daje wynik ze skąpymi danymi bez grup i dowodów', async ({ page, request, diagnostics }) => {
  const url = 'https://example.invalid/w04-url-only'
  await page.goto('/')
  await page.getByLabel('Adres strony lub reklamy').fill(url)
  await page.getByLabel(/Pobierz stronę/).uncheck()   // tryb offline: zapis samego adresu
  const { run, observation } = await startRun(page, 'observations', () => page.getByRole('button', { name: 'Analizuj adres' }).click())
  expect(observation.input_kind).toBe('url_only')
  const result = await checkRun(page, request, run, diagnostics)
  expect(result.summary.outcome).toBe('insufficient_evidence')
  expect(result.clusters).toEqual([])
  const urlMaterial = result.materials.find(({ observation_id }) => observation_id === run.observation_id)
  expect(urlMaterial).toMatchObject({ input_kind: 'url_only', display_url: url, available_evidence: [] })
  const urlPairs = result.pair_evidence.filter(({ pair }) => pair.includes(urlMaterial!.material_id))
  expect(urlPairs.every(({ score }) => score === null)).toBe(true)
  await expect(page.getByTestId('summary').getByRole('heading')).toHaveText('Za mało danych do rozstrzygnięcia')
  const material = page.locator('li.material').filter({ hasText: url })
  await expect(material).toHaveCount(1)
  await expect(material.getByText('Tylko adres URL, bez śladów do porównania')).toBeVisible()
  const urlComparisons = page.getByRole('listitem').filter({ has: page.getByRole('button', { name: /Pokaż dowody: .*example\.invalid/ }) })
  await expect(urlComparisons).toHaveCount(urlPairs.length)
  for (const comparison of await urlComparisons.all()) {
    await expect(comparison).toContainText('Brak porównywalnych danych')
  }
})

test('niedostępne API pokazuje błąd z proxy przy zatrzymanym backendzie', async ({ page }) => {
  test.skip(process.env.EXPECT_BACKEND_DOWN !== '1', 'Wymaga zatrzymanego backendu Compose')
  await page.goto('/')
  const button = page.getByRole('button', { name: 'Uruchom przykład' })
  const [response] = await Promise.all([
    page.waitForResponse((r) => new URL(r.url()).pathname === '/api/v1/demo' && r.request().method() === 'GET'),
    button.click(),
  ])
  const status = response.status()
  expect(String(status), 'GET /api/v1/demo przez Vite').toMatch(/^5[0-9]{2}$/)
  const alert = page.getByRole('alert')
  await expect(alert).toContainText('Nie udało się uruchomić analizy.')
  await expect(alert).toContainText(`Serwer zwrócił błąd (HTTP ${status}).`)
  await expect(button).toBeEnabled()
  await expect(page).toHaveURL((url) => url.pathname === '/')
})

test('martwa domena przy domyślnym pobieraniu nie przerywa analizy', async ({ page, request, diagnostics }) => {
  await page.goto('/')
  await page.getByLabel('Adres strony lub reklamy').fill('https://example.invalid/martwa-domena')
  const { run } = await startRun(page, 'observations', () => page.getByRole('button', { name: 'Analizuj adres' }).click())
  const result = await checkRun(page, request, run, diagnostics)
  expect(result.summary.outcome).toBe('insufficient_evidence')   // nic do porównania, ale bez błędu
  await expect(page.getByTestId('summary').getByRole('heading')).toHaveText('Za mało danych do rozstrzygnięcia')
})
