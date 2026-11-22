import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import { collectionPath, withParams, type CollectionView, type Navigate } from '../routes'
import type { CollectionData, ImportReport, Upload } from '../api/types'
import { downloadJson } from './downloadJson'
import CampaignOverview from './CampaignOverview'
import TimelineEngine from './TimelineEngine'
import WorkspaceIcon from './WorkspaceIcon'
import ArtifactGraph from './ArtifactGraph'
import EvidencePanel from './EvidencePanel'
import RelationshipGraph from './RelationshipGraph'
import UploadLibrary from './UploadLibrary'
import MonitorPanel from './MonitorPanel'

function plural(n: number, one: string, few: string, many: string) {
  if (n === 1) return one
  const last = n % 10, tens = n % 100
  return last >= 2 && last <= 4 && (tens < 12 || tens > 14) ? few : many
}

/** Co oznacza dla nowego materiału jego grupa: dołączenie do znanej kampanii czy potencjalnie nowa kampania. */
function groupNote(status: string, index: number, members: string[], added: string[] | undefined) {
  if (status !== 'added') return `Grupa ${index + 1}`
  const fresh = new Set(added)
  return members.every((id) => fresh.has(id))
    ? `Nowa grupa ${index + 1}: potencjalna nowa kampania`
    : `Dołączył do istniejącej grupy ${index + 1}`
}

const statuses = { added: 'Dodano', duplicate: 'Duplikat', skipped: 'Pominięto' }

function message(error: unknown) {
  if (error instanceof ApiError && error.status === 404)
    return 'Uruchomiony backend nie obsługuje bazy materiałów. Uruchom aktualną wersję backendu.'
  return error instanceof Error ? error.message : 'Nie udało się wczytać bazy.'
}

const views = [
  ['explorer', 'Baza materiałów'],
  ['graph', 'Graf powiązań'],
  ['evidence', 'Dowody'],
  ['timeline', 'Oś czasu'],
  ['library', 'Pliki źródłowe'],
] as const

type View = CollectionView
const reportLabels = { queued: 'W kolejce', running: 'W toku', succeeded: 'Zakończony', failed: 'Błąd analizy' }

export default function CollectionScreen({ view: mode, params, navigate }: { view: View; params: URLSearchParams; navigate: Navigate }) {
  const [collection, setCollection] = useState<CollectionData>()
  const [uploads, setUploads] = useState<Upload[]>([])
  const [refreshing, setRefreshing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [dismissed, setDismissed] = useState('')
  const [reportRefresh, setReportRefresh] = useState(0)
  const [report, setReport] = useState<ImportReport>()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [scanning, setScanning] = useState(false)
  const [monitorNote, setMonitorNote] = useState('')
  // Stan widoku żyje w adresie (zob. routes.ts), więc „wstecz”, odświeżenie i linki działają.
  const reportId = params.get('import') ?? undefined
  const graphMode: 'graph' | 'artifacts' = params.get('graph') === 'artifacts' ? 'artifacts' : 'graph'
  const campaign = params.get('group') ?? ''
  const query = params.get('q') ?? ''
  const domain = params.get('domain') ?? ''
  const period = params.get('period') ?? ''
  const strength = params.get('min') ?? ''
  const materialParam = params.get('material') ?? ''
  const evidence = params.get('evidence') ?? undefined
  const go = (view: View, changes: Record<string, string | null> = {}, replace = false) =>
    navigate(collectionPath(view, withParams(params, changes)), { replace })
  /** Zmiana w obrębie tej samej strony (filtr, zakładka): bez nowego wpisu w historii. */
  const update = (changes: Record<string, string | null>, replace = true) => go(mode, changes, replace)
  const clearSelection = { group: null, evidence: null, material: null }
  const openReport = (id: string) => { setDismissed(''); pushedReport.current = true; update({ import: id }, false) }
  const [uploadOpen, setUploadOpen] = useState(false)
  const returnView = useRef<View>('graph')
  const returnTo = useRef<string | undefined>(undefined)
  const pushedEvidence = useRef(false)
  const pushedReport = useRef(false)
  const evidenceOpener = useRef<HTMLElement | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const viewToggle = useRef<HTMLElement>(null)
  const loadVersion = useRef(0)
  const load = useCallback(async () => {
    const version = ++loadVersion.current
    const [data, files] = await Promise.all([api.getCollection(), api.getUploads()])
    if (version === loadVersion.current) {
      setCollection(data)
      setUploads(files)
    }
  }, [])
  const refresh = useCallback(async () => {
    setError('')
    setRefreshing(true)
    try {
      await load()
    } catch (e) {
      setError(message(e))
    } finally {
      setRefreshing(false)
    }
  }, [load])
  useEffect(() => { void refresh() }, [refresh])
  const pending = collection?.imports.find((item) => item.status === 'queued' || item.status === 'running')?.id
  const followedImport = reportId ?? pending

  useEffect(() => {
    if (!followedImport) return
    let active = true
    let timer: ReturnType<typeof setTimeout>
    async function follow() {
      try {
        const current = await api.getImport(followedImport!)
        if (!active) return
        setReport(current)
        if (current.status === 'queued' || current.status === 'running') {
          timer = setTimeout(() => void follow(), 1000)
        } else {
          await load()
          setError('')
        }
      } catch (e) {
        if (active) {
          setError(e instanceof Error ? e.message : 'Nie udało się wczytać raportu.')
          timer = setTimeout(() => void follow(), 3000)
        }
      }
    }
    void follow()
    return () => { active = false; clearTimeout(timer) }
  }, [followedImport, load, reportRefresh])

  async function change(kind: 'uploads' | 'materials', id: string, changes: { label?: string; archived?: boolean }) {
    setSaving(true)
    setError('')
    try {
      await api.updateCollectionItem(kind, id, changes)
      await load()
      return true
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Nie udało się zapisać zmian.')
      return false
    } finally {
      setSaving(false)
    }
  }

  async function retryImport() {
    if (!report) return
    setBusy(true)
    try {
      const current = await api.retryImport(report.id)
      setReport(current)
      setDismissed('')
      update({ import: current.id })
      setReportRefresh((count) => count + 1)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Nie udało się ponowić importu.')
    } finally {
      setBusy(false)
    }
  }

  async function onImport(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const input = form.elements.namedItem('files') as HTMLInputElement
    if (!input.files?.length) return
    setBusy(true)
    setError('')
    try {
      const current = await api.importCollection(input.files)
      setReport(current)
      setDismissed('')
      pushedReport.current = true
      update({ import: current.id }, false)
      await load()
      form.reset()
      setUploadOpen(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Nie udało się zaimportować plików.')
    } finally {
      setBusy(false)
    }
  }

  async function onUrlImport(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const urlField = form.elements.namedItem('url') as HTMLInputElement
    const fetchField = form.elements.namedItem('fetch') as HTMLInputElement
    const value = urlField.value.trim()
    if (!value) return
    setBusy(true)
    setError('')
    try {
      const current = await api.importCollectionUrl(value, fetchField.checked)
      setReport(current)
      setDismissed('')
      pushedReport.current = true
      update({ import: current.id }, false)
      await load()
      form.reset()
      setUploadOpen(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Nie udało się zaimportować adresu URL.')
    } finally {
      setBusy(false)
    }
  }

  async function onMonitorScan() {
    setScanning(true)
    setError('')
    setMonitorNote('')
    try {
      const res = await api.runMonitorScan()
      const srcName = typeof res.source === 'string' ? res.source : res.source?.name ?? 'monitor'
      const cursor = res.state?.cursor ?? res.cursor ?? '—'
      if (!res.ok) {
        setError(res.error ?? 'Nie udało się przeskanować źródła.')
      } else if (res.new > 0) {
        setMonitorNote(
          `Monitor (${srcName}): przeskanowano ${res.new} ${plural(res.new, 'nowy rekord', 'nowe rekordy', 'nowych rekordów')} · kursor: ${cursor}.`,
        )
        await load()
      } else {
        setMonitorNote(
          `Monitor (${srcName}): brak nowych rekordów (kursor: ${cursor}).`,
        )
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Nie udało się uruchomić monitora źródeł.')
    } finally {
      setScanning(false)
    }
  }

  const result = collection?.graph
  const metadata = result?.investigation
  const sourceDates = collection?.sources
  const { explorerMaterials, groups, activeGroup, ids, displayed, acceptedEdges } = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase('pl-PL')
    const artifactMatches = needle ? new Set(metadata?.artifacts.flatMap((artifact) =>
      artifact.label.toLocaleLowerCase('pl-PL').includes(needle) ? artifact.material_ids : [])) : undefined
    const scopedMaterials = result?.materials.filter((m) => {
      const id = m.material_id
      const newest = sourceDates?.[id]?.reduce((latest, source) => Math.max(latest, Date.parse(source.date) || 0), 0)
      const domains = metadata?.domains[id] ?? []
      const matches = !needle || artifactMatches?.has(id) || [id, m.label, m.display_url, metadata?.search_text?.[id], ...domains]
        .some((value) => value?.toLocaleLowerCase('pl-PL').includes(needle))
      return matches && (!domain || domains.includes(domain)) &&
        (!period || Boolean(newest && newest >= Date.now() - Number(period) * 86400000))
    }) ?? []
    const scopedIds = new Set(scopedMaterials.map((m) => m.material_id))
    const acceptedIds = new Set(result?.cluster_run.edges?.map((edge) => edge.pair_evidence_id))
    const scopedEdges = result?.pair_evidence.filter((p) => p.pair.every((id) => scopedIds.has(id)) &&
      (!strength || (p.score ?? 0) >= Number(strength))) ?? []
    const linkedIds = new Set(scopedEdges.flatMap((p) => p.pair))
    const explorerMaterials = strength ? scopedMaterials.filter((m) => linkedIds.has(m.material_id)) : scopedMaterials
    const explorerIds = new Set(explorerMaterials.map((m) => m.material_id))
    const groups = (result?.clusters ?? []).map((c, i) => ({
      ...c, index: i + 1, material_ids: c.material_ids.filter((id) => explorerIds.has(id)),
    })).filter((c) => c.material_ids.length > 0)
    const activeGroup = groups.find((c) => c.cluster_id === campaign)
    const ids = new Set(activeGroup?.material_ids ?? explorerIds)
    const displayed = result && {
      ...result,
      materials: explorerMaterials.filter((m) => ids.has(m.material_id)),
      clusters: activeGroup ? [activeGroup] : groups,
      pair_evidence: scopedEdges.filter((p) => p.pair.every((id) => ids.has(id))),
    }
    const acceptedEdges = scopedEdges.filter((edge) => acceptedIds.has(edge.evidence_id))
    return { explorerMaterials, groups, activeGroup, ids, displayed, acceptedEdges }
  }, [campaign, domain, metadata, period, query, result, sourceDates, strength])
  const allDomains = Array.from(new Set(Object.values(metadata?.domains ?? {}).flat())).sort()
  const scopedRelationships = acceptedEdges.filter((edge) => edge.pair.every((id) => ids.has(id)))
  const firstPair = scopedRelationships[0] ?? displayed?.pair_evidence[0]
  const firstEvidence = firstPair?.evidence_id
  // Wejście na /collection/evidence bez wskazanej pary: otwórz pierwszą (bez nowego wpisu w historii).
  useEffect(() => {
    if (mode === 'evidence' && !evidence && firstEvidence && collection?.run_id)
      navigate(collectionPath('evidence', withParams(params, { evidence: firstEvidence })), { replace: true })
  }, [mode, evidence, firstEvidence, collection?.run_id]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (mode !== 'evidence') pushedEvidence.current = false }, [mode])
  useEffect(() => { if (!reportId) pushedReport.current = false }, [reportId])
  // Po przejściu do innego widoku, grupy lub materiału przenieś fokus na nagłówek (czytniki ekranu, klawiatura).
  const firstRender = useRef(true)
  useEffect(() => {
    if (firstRender.current) { firstRender.current = false; return }
    requestAnimationFrame(() => {
      heading.current?.scrollIntoView?.({ block: 'start' })
      heading.current?.focus({ preventScroll: true })
    })
  }, [mode, campaign, materialParam])
  const domainCount = new Set(explorerMaterials.flatMap((m) => metadata?.domains[m.material_id] ?? [])).size
  const timeline = displayed?.materials.flatMap((m) => [
    ...(metadata?.captured_at[m.material_id] ? [{ id: m.material_id, label: m.label, thumbnail: m.thumbnail, type: 'Zapis materiału', date: metadata.captured_at[m.material_id] }] : []),
    ...(sourceDates?.[m.material_id]?.map((source) => ({ id: m.material_id, label: m.label, source: source.label, type: 'Import do bazy', date: source.date })) ?? []),
  ]).sort((a, b) => Date.parse(b.date) - Date.parse(a.date)) ?? []
  const labels = new Map(result?.materials.map((m) => [m.material_id, m.label]))
  const newMaterials = useMemo(() => report?.entries.flatMap((entry) =>
    entry.status === 'added' && entry.material_id ? [entry.material_id] : []), [report])
  const inspect = (material: string) => {
    const reset: Record<string, string | null> = ids.has(material) ? {} : { group: null, q: null, domain: null, period: null, min: null }
    go('graph', { ...reset, material, graph: null, evidence: null })
  }
  const openEvidence = (id: string, opener?: HTMLElement) => {
    if (mode !== 'evidence') {
      returnView.current = mode
      returnTo.current = collectionPath(mode, params)
      evidenceOpener.current = opener ?? document.activeElement as HTMLElement | null
      pushedEvidence.current = true
    }
    go('evidence', { evidence: id }, mode === 'evidence')
  }
  const closeEvidence = () => {
    if (pushedEvidence.current) {
      pushedEvidence.current = false
      window.history.back()
    } else {
      navigate(returnTo.current ?? collectionPath('graph', withParams(params, { evidence: null })), { replace: true })
    }
    requestAnimationFrame(() => {
      const target = evidenceOpener.current?.isConnected ? evidenceOpener.current
        : viewToggle.current?.querySelector<HTMLElement>('button[aria-current="page"]')
      target?.focus()
    })
  }
  const showGroup = (id: string) => go('graph', { group: id, graph: null, evidence: null, material: null })
  const closeReport = () => {
    if (report) setDismissed(report.id)
    if (pushedReport.current && reportId) {
      pushedReport.current = false
      window.history.back()
    } else update({ import: null })
  }
  const railHref = (id: View) => {
    const keep = withParams(params, { evidence: null, material: null, import: id === 'explorer' || id === 'library' ? params.get('import') : null })
    if (id === 'evidence') {
      const target = evidence ?? firstPair?.evidence_id
      return collectionPath('evidence', target ? withParams(keep, { evidence: target }) : keep)
    }
    return collectionPath(id, keep)
  }
  const title = views.find(([id]) => id === mode)?.[1]
  const filters = <div className="campaign-filters">
    <label>Szukaj<input type="search" value={query} placeholder="Nazwa, treść, domena lub ślad"
      onChange={(event) => update({ q: event.target.value, ...clearSelection })} /></label>
    <label>Domena<select value={domain} onChange={(e) => update({ domain: e.target.value, ...clearSelection })}>
      <option value="">Wszystkie</option>{allDomains.map((host) => <option key={host}>{host}</option>)}
    </select></label>
    <label>Data importu<select value={period} onChange={(e) => update({ period: e.target.value, ...clearSelection })}>
      <option value="">Cały okres</option><option value="7">Ostatnie 7 dni</option><option value="30">Ostatnie 30 dni</option>
    </select></label>
    <label>Podobieństwo<select value={strength} onChange={(e) => update({ min: e.target.value, ...clearSelection })}>
      <option value="">Dowolne</option><option value=".65">Co najmniej 65%</option><option value=".85">Co najmniej 85%</option>
    </select></label>
  </div>

  return <section className="collection" aria-labelledby="collection-h">
    <nav ref={viewToggle} className="workspace-rail" aria-label="Widoki bazy materiałów">
      {views.map(([id, label]) => <button key={id} type="button" aria-current={mode === id ? 'page' : undefined}
        onClick={() => navigate(railHref(id))}><WorkspaceIcon name={id} />{label}</button>)}
    </nav>
    <div className="collection-content">
    <header className="explorer-header">
      <div><h1 id="collection-h" ref={heading} tabIndex={-1}>{title}</h1>
        {mode === 'explorer' && <p>Przeglądaj grupy materiałów i ślady, które je łączą.</p>}
        {(mode === 'graph' || mode === 'timeline') && displayed && <p>
          {activeGroup ? `Grupa ${activeGroup.index}` : 'Cała kolekcja'} · {displayed.materials.length} {plural(displayed.materials.length, 'materiał', 'materiały', 'materiałów')} · {scopedRelationships.length} {plural(scopedRelationships.length, 'powiązanie', 'powiązania', 'powiązań')}
        </p>}
      </div>
      {mode === 'explorer' && <div className="upload-actions">
        <button type="button" className="outline icon-button" disabled={scanning || busy || saving} onClick={() => void onMonitorScan()}>
          <WorkspaceIcon name="refresh" />{scanning ? 'Skanowanie…' : 'Skanuj źródło (Monitor)'}
        </button>
        <button type="button" className="outline icon-button" disabled={refreshing || saving} onClick={() => {
          setReportRefresh((count) => count + 1)
          void refresh()
        }}><WorkspaceIcon name="refresh" />Odśwież bazę</button>
        {result && <button type="button" className="outline icon-button" onClick={() =>
          downloadJson('methadata-kolekcja.json', {
            run_id: collection?.run_id,
            filters: { query: query.trim(), domain, period_days: period ? Number(period) : null, minimum_score: strength ? Number(strength) : null },
            materials: explorerMaterials,
            clusters: groups,
            pair_evidence: acceptedEdges,
          })}>
          <WorkspaceIcon name="download" /> Eksportuj raport
        </button>}
        {result && <button type="button" className="icon-button" onClick={() => setUploadOpen(!uploadOpen)} aria-expanded={uploadOpen}>
          {!uploadOpen && <WorkspaceIcon name="plus" />}{uploadOpen ? 'Zamknij import' : 'Dodaj materiały'}
        </button>}
      </div>}
    </header>
    {mode === 'explorer' && (uploadOpen || !result) && <section className="collection-upload" aria-label="Import materiałów">
      <h2>Import materiałów</h2>
      <form onSubmit={(e) => void onImport(e)}>
        <label htmlFor="collection-files">Dodaj zapis strony, plik (HTML, PNG, PDF, MP4, MP3) lub backup ZIP</label>
        <div className="collection-upload-row">
          <input id="collection-files" name="files" type="file" multiple required disabled={busy} aria-describedby={collection ? "collection-limit" : undefined} />
          <button type="submit" disabled={busy || !collection}>Dodaj do bazy</button>
        </div>
        {collection && <small id="collection-limit">Limit na plik {collection.max_upload_bytes / 1048576} MiB</small>}
      </form>
      <form className="collection-upload-url" onSubmit={(e) => void onUrlImport(e)}>
        <label htmlFor="collection-url">Lub dodaj adres URL / domenę</label>
        <div className="collection-upload-row">
          <input id="collection-url" name="url" type="text" placeholder="np. example.com lub https://example.com/oferta" required disabled={busy} />
          <button type="submit" className="outline" disabled={busy || !collection}>Dodaj adres</button>
        </div>
        <label className="collection-upload-check">
          <input name="fetch" type="checkbox" defaultChecked disabled={busy} />
          <span>Pobierz treść strony i przekierowania (w razie błędu zapisze sam adres URL)</span>
        </label>
      </form>
    </section>}
    {!collection && !error && <p className="notice" role="status">Wczytywanie bazy…</p>}
    {busy && <p className="notice" role="status">Przesyłanie plików…</p>}
    {monitorNote && <p className="notice" role="status">{monitorNote}</p>}
    {saving && <p className="notice" role="status">Zapisywanie zmian w bazie…</p>}
    {error && <p role="alert" className="error">{error}</p>}
    {mode === 'library' && Boolean(collection?.imports?.length) && <details className="collection-history" open>
      <summary>Historia importów</summary>
      <ul>{collection?.imports.map((item) => <li key={item.id}>
        <button type="button" className="outline" onClick={() => openReport(item.id)}>
          {new Date(item.created_at).toLocaleString('pl-PL')} · {reportLabels[item.status]}
        </button>
      </li>)}</ul>
    </details>}
    {report && report.id === followedImport && report.id !== dismissed && (mode === 'explorer' || mode === 'library') && <section className="collection-report" aria-label="Raport importu">
      <div className="section-heading"><h2>Raport importu</h2>
        <button type="button" className="outline" onClick={closeReport}>Zamknij raport</button></div>
      {(report.status === 'queued' || report.status === 'running') &&
        <p className="notice" role="status">{report.stage ?? reportLabels[report.status]}</p>}
      {report.archive && <p>Wczytane wpisy ZIP: {report.archive.loaded} · Pominięte: {report.archive.skipped}
        {' '}· Metadane systemowe: {report.archive.metadata}</p>}
      {report.error && <p role="alert" className="error">{report.error}</p>}
      {report.status === 'failed' && Boolean(report.upload_ids?.length) &&
        <button type="button" className="outline" disabled={busy} onClick={() => void retryImport()}>Ponów import</button>}
      {report.first_material && <p>Dodano pierwszy materiał.</p>}
      <p>{Object.entries(statuses).map(([status, label]) => `${label}: ${report.entries.filter((e) => e.status === status).length}`).join(' · ')}</p>
      <ul className="collection-entries">
        {report.entries.map((entry, i) => {
          const best = result?.pair_evidence.filter((pair) => pair.pair.includes(entry.material_id ?? ''))
            .sort((a, b) => (b.score ?? 0) - (a.score ?? 0))[0]
          const cluster = result?.clusters.findIndex((c) => c.material_ids.includes(entry.material_id ?? '')) ?? -1
          return <li key={i}>
            <span><strong>{entry.label}</strong> · {statuses[entry.status]} {entry.reason && <small>{entry.reason}</small>}
              {cluster >= 0 && <small>{groupNote(entry.status, cluster, result?.clusters[cluster]?.material_ids ?? [], newMaterials)}</small>}</span>
            {entry.material_id && labels.has(entry.material_id) && <button type="button" className="outline" onClick={() => inspect(entry.material_id!)}>
              {entry.status === 'duplicate' ? 'Otwórz oryginał' : 'Pokaż w grafie'}
            </button>}
            {best && <button type="button" className="outline" onClick={(e) => openEvidence(best.evidence_id, e.currentTarget)}>
              Dowód: {labels.get(best.pair.find((id) => id !== entry.material_id) ?? '')}
            </button>}
          </li>
        })}
      </ul>
      {report.merges.map((merge) => <p key={merge.cluster_id}>
        Połączono grupy {merge.previous.map((group) => group.map((id) => labels.get(id)).join(', ')).join(' oraz ')}.
        {' '}<button type="button" className="outline" onClick={() => inspect(
          merge.material_ids.find((id) => newMaterials?.includes(id)) ?? merge.material_ids[0])}>Zobacz powiązania</button>
      </p>)}
      {report.issues.length > 0 && <details><summary>Szczegóły importu ({report.issues.length})</summary>
        <ul>{report.issues.map((issue, i) => <li key={i}>{issue.label} · {issue.reason}</li>)}</ul>
      </details>}
    </section>}
    {mode === 'library' && <MonitorPanel />}
    {mode === 'library' && <UploadLibrary uploads={uploads} busy={saving} change={change}
      openImport={openReport} inspect={inspect} />}
    {mode === 'evidence' && evidence && collection?.run_id && <>
      {displayed && displayed.pair_evidence.some((pair) => pair.evidence_id === evidence) &&
        <label className="evidence-select">Porównywana para
          <select value={evidence} onChange={(event) => openEvidence(event.target.value)}>
            {displayed.pair_evidence.map((pair) => <option key={pair.evidence_id} value={pair.evidence_id}>
              {pair.pair.map((id) => labels.get(id) ?? id).join(' ↔ ')}
            </option>)}
          </select>
        </label>}
      <EvidencePanel
        key={evidence}
        evidenceId={evidence}
        runId={collection.run_id}
        onClose={closeEvidence}
        scenario={{ label: (id) => labels.get(id) ?? id }}
      />
    </>}
    {mode === 'evidence' && collection && (!evidence || !collection.run_id) &&
      <p className="empty-state">Brak dowodów do wyświetlenia. Zaimportuj materiały w bazie.</p>}
    {(mode === 'explorer' || mode === 'graph' || mode === 'timeline') && collection && !result &&
      <section className="collection-empty">
        <h2>Brak aktywnych materiałów do analizy</h2>
        <p>Po imporcie zobaczysz tu materiały i ich powiązania.</p>
      </section>}
    {mode === 'explorer' && result && <>
      <dl className="explorer-stats" aria-label="Podsumowanie filtrowanej bazy">
        <div><dt>Materiały</dt><dd>{explorerMaterials.length}</dd></div>
        <div><dt>Domeny</dt><dd>{domainCount}</dd></div>
        <div><dt>Grupy</dt><dd>{groups.length}</dd></div>
        <div><dt>Powiązania</dt><dd>{acceptedEdges.length}</dd></div>
      </dl>
      {filters}
      <CampaignOverview groups={groups} materials={explorerMaterials} domains={metadata?.domains}
        artifacts={metadata?.artifacts} phylofake={metadata?.phylofake} edges={acceptedEdges} sources={sourceDates} runId={collection?.run_id}
        assignments={result.cluster_run.assignments} onGroup={showGroup}
        onMaterial={inspect} onEvidence={openEvidence} />
    </>}
    {(mode === 'graph' || mode === 'timeline' || mode === 'evidence' && returnView.current === 'graph') && displayed &&
      <div hidden={mode === 'evidence'}>
      <div className="investigation-toolbar">
        <label>Zakres <select value={activeGroup?.cluster_id ?? ''} onChange={(e) =>
          update({ group: e.target.value || null, evidence: null, material: null })}>
          <option value="">Cała kolekcja</option>
          {groups.map((group) => <option key={group.cluster_id} value={group.cluster_id}>Grupa {group.index}</option>)}
        </select></label>
        {mode === 'graph' && <div className="view-tabs" role="group" aria-label="Widok powiązań">
          {([['graph', 'Podobieństwa'], ['artifacts', 'Wspólne ślady']] as const).map(([id, label]) =>
            <button type="button" key={id} aria-pressed={graphMode === id} onClick={() => update({ graph: id === 'graph' ? null : 'artifacts' })}>{label}</button>)}
        </div>}
      </div>
      {mode === 'timeline' ? <TimelineEngine events={timeline} result={displayed} runId={collection?.run_id}
        links={scopedRelationships} onMaterial={inspect} /> : null}
      {mode !== 'timeline' && graphMode === 'graph' && <RelationshipGraph key={materialParam} result={displayed}
        onEvidence={openEvidence}
        onPromoteEdge={collection?.run_id ? async (eid) => {
          await api.promoteEdge(collection.run_id!, eid)
          await load()
        } : undefined}
        onUnpromoteEdge={async (eid) => {
          await api.unpromoteEdge(eid)
          await load()
        }}
        sources={sourceDates} initialMaterial={materialParam} highlighted={newMaterials}
        collectionRun={collection?.run_id ?? undefined} />}
      {mode !== 'timeline' && graphMode === 'artifacts' &&
        <ArtifactGraph result={displayed} materialIds={ids} onMaterial={inspect} />}
    </div>}
    </div>
  </section>
}
