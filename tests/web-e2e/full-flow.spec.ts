import { test as base, expect, type Page } from '@playwright/test'

const API_URL = process.env.API_URL ?? 'http://127.0.0.1:8000'

/** Zbiera statusy odpowiedzi /api, logi konsoli i błędy strony; dołącza je do raportu. */
const test = base.extend<{ diag: string[] }>({
  diag: [
    async ({ page }, use, testInfo) => {
      const lines: string[] = []
      page.on('console', (m) => lines.push(`console.${m.type()}: ${m.text()}`))
      page.on('pageerror', (e) => lines.push(`pageerror: ${e.message}`))
      page.on('requestfailed', (r) => lines.push(`requestfailed: ${r.method()} ${r.url()} ${r.failure()?.errorText}`))
      page.on('response', (r) => {
        if (/\/(api|static)\//.test(r.url())) lines.push(`${r.status()} ${r.request().method()} ${r.url()}`)
      })
      await use(lines)
      await testInfo.attach('diagnostyka.txt', { body: lines.join('\n') || '(brak)', contentType: 'text/plain' })
      if (testInfo.status !== testInfo.expectedStatus) console.log(`--- diagnostyka: ${testInfo.title}\n${lines.join('\n')}`)
    },
    { auto: true },
  ],
})

async function startAndGetRunId(page: Page, action: () => Promise<void>): Promise<string> {
  const created = page.waitForResponse((r) => r.url().endsWith('/api/v1/runs') && r.request().method() === 'POST')
  await action()
  const res = await created
  expect(res.status(), 'POST /api/v1/runs').toBe(202)
  const { run_id } = await res.json()
  return run_id as string
}

test('przykład: od uruchomienia do dowodu obu stron, odświeżenie adresu przebiegu', async ({ page, request }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: /Sprawdź, czy materiały/ })).toBeVisible()

  const runId = await startAndGetRunId(page, () => page.getByRole('button', { name: 'Uruchom przykład' }).click())
  await expect(page).toHaveURL(new RegExp(`/runs/${runId}$`))

  // run_id widoczny w UI odpowiada wynikowi API
  await expect(page.getByRole('heading', { name: /Przebieg/ }).locator('code')).toHaveText(runId)
  await expect(page.getByTestId('summary')).toBeVisible()
  const api = await (await request.get(`${API_URL}/api/v1/runs/${runId}`)).json()
  expect(api.run_id).toBe(runId)
  expect(api.status).toBe('succeeded')

  // oczekiwany zestaw: A i C powiązane (wspólne grafiki), B pojedynczy
  const labels = Object.fromEntries(api.result.materials.map((m: any) => [m.material_id, m.label]))
  expect(api.result.clusters).toHaveLength(1)
  const grouped = api.result.clusters[0].material_ids.map((id: string) => labels[id]).sort()
  expect(grouped).toEqual(['Reklama A (przykład syntetyczny)', 'Reklama C (przykład syntetyczny)'])
  await expect(page.getByTestId('summary')).toContainText('Znaleziono grupy')
  const group = page.getByRole('article')
  await expect(group).toHaveCount(1)
  await expect(group).toContainText('Reklama A (przykład syntetyczny)')
  await expect(group).toContainText('Reklama C (przykład syntetyczny)')
  await expect(group).not.toContainText('Reklama B')
  await expect(page.locator('li.material')).toHaveCount(3)

  // dowód obu stron
  await page.getByRole('button', { name: /Pokaż dowody: .*Reklama A.*Reklama C|Pokaż dowody: .*Reklama C.*Reklama A/ }).click()
  await expect(page).toHaveURL(new RegExp(`/runs/${runId}/evidence/[^/]+$`))
  const sides = page.getByTestId('evidence-side')
  await expect(sides).toHaveCount(2)
  for (const side of await sides.all()) {
    await expect(side.locator('img').first()).toBeVisible()
    expect(await side.locator('img').first().evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth > 0)).toBe(true)
  }
  const evUrl = new URL(page.url()).pathname
  const evId = evUrl.split('/').pop()!
  const apiEv = await (await request.get(`${API_URL}/api/v1/runs/${runId}/evidence/${evId}`)).json()
  expect(apiEv.sides).toHaveLength(2)

  // odświeżenie adresu przebiegu (z dowodem) odtwarza ten sam widok
  await page.reload()
  await expect(page.getByRole('heading', { name: /Przebieg/ }).locator('code')).toHaveText(runId)
  await expect(page.getByTestId('evidence-side')).toHaveCount(2)
  await expect(page.getByTestId('summary')).toContainText('Znaleziono grupy')
})

test('drugi plik o dowolnej nazwie daje przebieg', async ({ page, request }) => {
  await page.goto('/')
  const runId = await startAndGetRunId(page, async () => {
    await page.locator('#file').setInputFiles({
      name: 'dowolna nazwa (1).dat',
      mimeType: 'application/octet-stream',
      buffer: Buffer.from(`<html><body><h1>Drugi plik ${Date.now()}</h1></body></html>`),
    })
    await page.getByRole('button', { name: 'Analizuj plik' }).click()
  })
  await expect(page.getByTestId('summary')).toBeVisible()
  const api = await (await request.get(`${API_URL}/api/v1/runs/${runId}`)).json()
  expect(api.status).toBe('succeeded')
  await expect(page.getByRole('heading', { name: /Przebieg/ }).locator('code')).toHaveText(runId)
  await expect(page.locator('li.material', { hasText: 'dowolna_nazwa' })).toHaveCount(1)
})

test('sam URL (skąpe dane): wynik bez dowodów, bez udawania powiązania', async ({ page, request }) => {
  await page.goto('/')
  await page.locator('#url').fill(`https://example.invalid/reklama-${Date.now()}`)
  const runId = await startAndGetRunId(page, () => page.getByRole('button', { name: 'Analizuj adres' }).click())
  await expect(page.getByTestId('summary')).toBeVisible()
  await expect(page.getByTestId('summary')).not.toContainText('Znaleziono grupy')
  await expect(page.getByText('Tylko adres URL, brak dowodów')).toBeVisible()
  const api = await (await request.get(`${API_URL}/api/v1/runs/${runId}`)).json()
  expect(api.result.clusters.length).toBe(0)
  await expect(page.getByRole('heading', { name: /Przebieg/ }).locator('code')).toHaveText(runId)
})

test('niedostępne API: czytelny komunikat zamiast pustej strony', async ({ page }) => {
  await page.goto('/')
  await page.route('**/api/**', (route) => route.abort('connectionrefused'))
  await page.getByRole('button', { name: 'Uruchom przykład' }).click()
  const alert = page.getByRole('alert')
  await expect(alert).toContainText('Brak połączenia z serwerem')
  await expect(alert).toContainText('Sprawdź, czy API działa')
})
