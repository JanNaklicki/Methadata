import { formatScore } from './formatScore'

const FIELD_NAMES: Record<string, string> = {
  body: 'Treść',
  title: 'Tytuł',
  cta: 'Wezwanie do działania',
  comments: 'Komentarze',
  ocr: 'Tekst z obrazu',
  asset: 'Grafiki',
  'content-text': 'Tekst',
  text: 'Tekst',
  'dom-structure': 'Struktura HTML',
  dom: 'Struktura HTML',
}

function fieldLabel(raw: string) {
  return FIELD_NAMES[raw.trim().toLowerCase()] ?? raw.trim()
}

function pct(value: string | number) {
  const n = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(n) ? formatScore(n) : String(value)
}

interface ComparisonRow {
  field: string
  score: number
  a: string
  b: string
}

function parseSemantic(text: string) {
  const head = text.match(/^Cosinus embeddingów akapitów\s+([0-9.]+)\s*\(model\s+([^;)]+)/i)
  if (!head) return null
  const score = Number(head[1])
  const model = head[2].replace(/@unpinned$/, '').split('/').pop() ?? head[2]

  const fields: { name: string; score: number }[] = []
  const fieldsMatch = text.match(/Pola:\s*([^.]+(?:\.[0-9]+[^.]*)*)\./)
  if (fieldsMatch) {
    for (const part of fieldsMatch[1].split(';')) {
      const m = part.trim().match(/^([a-zA-Z0-9_-]+):\s*([0-9.]+)$/)
      if (m) fields.push({ name: fieldLabel(m[1]), score: Number(m[2]) })
    }
  }

  const rows: ComparisonRow[] = []
  const rowRe = /\[([^\]]+)\]\s*cos\s+([0-9.]+):\s*A\s*«([\s\S]*?)»\s*\|\s*B\s*«([\s\S]*?)»(?=\.\s*\[|\.\s*$|$)/g
  let match: RegExpExecArray | null
  while ((match = rowRe.exec(text)) !== null) {
    rows.push({
      field: fieldLabel(match[1]),
      score: Number(match[2]),
      a: match[3].trim(),
      b: match[4].trim(),
    })
  }
  return { score, model, fields, rows }
}

function parseUrlHar(text: string) {
  const head = text.match(/^URL\/HAR\s+([0-9.]+)/i)
  if (!head) return null
  const score = Number(head[1])
  const integrations = text.match(/integration_ids:\s*([0-9.]+)\s*\(wspólne:\s*([^)]+)\)/i)
  const hosts = text.match(/host_order:\s*([0-9.]+)\s*\(([^)]+)\)/i)
  const sharedHosts = hosts
    ? [...new Set(hosts[2].split('|').flatMap((side) => side.trim().split(/\s+/)).filter(Boolean))]
    : []
  const sharedTrackers = integrations
    ? integrations[2].split(/,\s*/).map((s) => s.trim()).filter(Boolean)
    : []
  return {
    score,
    hostScore: hosts ? Number(hosts[1]) : null,
    trackerScore: integrations ? Number(integrations[1]) : null,
    sharedHosts,
    sharedTrackers,
  }
}

function parseFusion(text: string) {
  const head = text.match(
    /Fuzja\s+[^:]+:\s*wynik\s+([0-9.]+)\s*\([^)]*pokrycie\s+([0-9.]+)[^,]*,\s*wsparcie\s+(\d+)[^0-9]*(\d+)/i,
  )
  if (!head) return null
  const sources: { name: string; score: number; supported: boolean }[] = []
  const srcRe = /Źródło\s+([^\s[]+)(?:\s*\[[^\]]+\])?:\s*([0-9.]+)\s*\([^)]*wsparcie[^:]*:\s*(tak|nie)\)/gi
  let m: RegExpExecArray | null
  while ((m = srcRe.exec(text)) !== null) {
    sources.push({
      name: fieldLabel(m[1]),
      score: Number(m[2]),
      supported: m[3].toLowerCase() === 'tak',
    })
  }
  return {
    score: Number(head[1]),
    coverage: Number(head[2]),
    support: Number(head[3]),
    requiredSupport: Number(head[4]),
    sources,
  }
}

function parsePhash(text: string) {
  const m = text.match(/pHash\s+(\d+)\s*b[^:]*:\s*odległość Hamminga\s+(\d+)\/(\d+),\s*próg\s+(\d+)/i)
  const scoreM = text.match(/wynik\s+([0-9.]+)/i)
  if (!m) return null
  return {
    distance: Number(m[2]),
    bits: Number(m[3]),
    threshold: Number(m[4]),
    score: scoreM ? Number(scoreM[1]) : null,
  }
}

function parseNearCopy(text: string) {
  const m = text.match(
    /jaccard shingles po\s+(\d+)\s+słów\s*\(pole\s+([^)]+)\):\s*([0-9.]+);[^,]*,\s*zawieranie[^0-9]+([0-9.]+);\s*wspólnych shingles\s+(\d+)/i,
  )
  if (!m) return null
  return {
    ngram: Number(m[1]),
    field: fieldLabel(m[2]),
    jaccard: Number(m[3]),
    containment: Number(m[4]),
    shared: Number(m[5]),
  }
}

function fmtSec(ms: number) {
  return `${(ms / 1000).toFixed(1).replace('.', ',')} s`
}

function parseVoice(text: string) {
  const m = text.match(/kosinus[^=]*=\s*([-0-9.]+)\s*\(backend\s+([^,]+),\s*mowa A\s*([0-9.]+)\s*s,\s*B\s*([0-9.]+)\s*s\)/i)
  if (!m) return null
  return {
    cosine: Number(m[1]),
    backend: m[2].trim(),
    voicedA: Number(m[3]),
    voicedB: Number(m[4]),
    isSpeechBrain: m[2].toLowerCase().includes('speechbrain'),
  }
}

function parseTranscript(text: string) {
  const m = text.match(/Wspólny cytat\s*\((\d+)\s*słów\):\s*"([^"]+)";\s*A\s*(\d+)-(\d+)\s*ms,\s*B\s*(\d+)-(\d+)\s*ms.*=\s*([0-9.]+)/i)
  if (!m) return null
  return {
    words: Number(m[1]),
    quote: m[2],
    a: [Number(m[3]), Number(m[4])] as [number, number],
    b: [Number(m[5]), Number(m[6])] as [number, number],
    score: Number(m[7]),
  }
}

function parseAudioRun(text: string) {
  const m = text.match(/Wspólny odcinek audio:\s*A\s*(\d+)-(\d+)\s*ms odpowiada B\s*(\d+)-(\d+)\s*ms;\s*BER\s*([0-9.]+).*=\s*([0-9.]+)/i)
  if (!m) return null
  return {
    a: [Number(m[1]), Number(m[2])] as [number, number],
    b: [Number(m[3]), Number(m[4])] as [number, number],
    ber: Number(m[5]),
    score: Number(m[6]),
  }
}

function parseVideoRun(text: string) {
  const m = text.match(/Wspólny fragment wideo:\s*A\s*(\d+)-(\d+)\s*ms odpowiada B\s*(\d+)-(\d+)\s*ms;([^.]*)\.\s*Wynik\s*=\s*max\(\.\.\.\)\s*=\s*([0-9.]+)/i)
  if (!m) return null
  return {
    a: [Number(m[1]), Number(m[2])] as [number, number],
    b: [Number(m[3]), Number(m[4])] as [number, number],
    detail: m[5].trim(),
    score: Number(m[6]),
  }
}

export default function SignalExplanation({ text, pairLabels }: {
  text: string
  pairLabels?: [string, string]
}) {
  const labelA = pairLabels?.[0] || 'Materiał A'
  const labelB = pairLabels?.[1] || 'Materiał B'

  const semantic = parseSemantic(text)
  if (semantic) {
    return <div className="signal-explanation">
      <p className="signal-summary">
        Podobieństwo znaczeniowe tekstu: <strong>{pct(semantic.score)}</strong>
      </p>
      {semantic.fields.length > 0 && <div className="signal-chips">
        {semantic.fields.map((f) => <span key={f.name} className="signal-chip">
          {f.name} <strong>{pct(f.score)}</strong>
        </span>)}
      </div>}
      {semantic.rows.length > 0 && <div className="signal-matches">
        <p className="signal-subhead">Najbardziej podobne fragmenty:</p>
        {semantic.rows.map((row, idx) => <div key={idx} className="signal-match-card">
          <div className="signal-match-head">
            <span className="signal-chip">{row.field}</span>
            <strong>{pct(row.score)}</strong>
          </div>
          <dl className="signal-pair-quotes">
            <div><dt>{labelA}</dt><dd>„{row.a}”</dd></div>
            <div><dt>{labelB}</dt><dd>„{row.b}”</dd></div>
          </dl>
        </div>)}
      </div>}
      <p className="signal-meta muted">Model: {semantic.model}</p>
    </div>
  }

  const urlHar = parseUrlHar(text)
  if (urlHar) {
    return <div className="signal-explanation">
      <p className="signal-summary">
        Podobieństwo adresów i ruchu sieciowego: <strong>{pct(urlHar.score)}</strong>
      </p>
      <div className="signal-chips">
        {urlHar.trackerScore !== null && <span className="signal-chip">Trackery <strong>{pct(urlHar.trackerScore)}</strong></span>}
        {urlHar.hostScore !== null && <span className="signal-chip">Kolejność domen <strong>{pct(urlHar.hostScore)}</strong></span>}
      </div>
      {urlHar.sharedTrackers.length > 0 && <div className="signal-list-block">
        <p className="signal-subhead">Wspólne identyfikatory śledzące:</p>
        <ul>{urlHar.sharedTrackers.map((t) => <li key={t}><code>{t}</code></li>)}</ul>
      </div>}
      {urlHar.sharedHosts.length > 0 && <div className="signal-list-block">
        <p className="signal-subhead">Wspólne domeny w przekierowaniach:</p>
        <ul>{urlHar.sharedHosts.slice(0, 6).map((h) => <li key={h}><code>{h}</code></li>)}</ul>
      </div>}
    </div>
  }

  const fusion = parseFusion(text)
  if (fusion) {
    return <div className="signal-explanation">
      <p className="signal-summary">
        Wynik łączny: <strong>{pct(fusion.score)}</strong> · pokrycie <strong>{pct(fusion.coverage)}</strong> · wsparcie <strong>{fusion.support}/{fusion.requiredSupport}</strong> źródeł
      </p>
      {fusion.sources.length > 0 && <div className="signal-chips">
        {fusion.sources.map((s) => <span key={s.name} className={`signal-chip${s.supported ? ' is-supported' : ''}`}>
          {s.name} <strong>{pct(s.score)}</strong> {s.supported ? '✓' : '–'}
        </span>)}
      </div>}
    </div>
  }

  const phash = parsePhash(text)
  if (phash) {
    return <div className="signal-explanation">
      <p className="signal-summary">
        Podobieństwo wizualne (pHash): <strong>{phash.score !== null ? pct(phash.score) : 'trafienie'}</strong>
      </p>
      <p className="muted">
        Różnica odcisków grafiki: <strong>{phash.distance}/{phash.bits}</strong> bitów (próg dopasowania: {phash.threshold}).
      </p>
    </div>
  }

  const nearCopy = parseNearCopy(text)
  if (nearCopy) {
    return <div className="signal-explanation">
      <p className="signal-summary">
        Wspólne fragmenty tekstu ({nearCopy.field}): <strong>{pct(nearCopy.jaccard)}</strong>
      </p>
      <div className="signal-chips">
        <span className="signal-chip">Zawieranie <strong>{pct(nearCopy.containment)}</strong></span>
        <span className="signal-chip">Wspólne {nearCopy.ngram}-gramy <strong>{nearCopy.shared}</strong></span>
      </div>
    </div>
  }

  const voice = parseVoice(text)
  if (voice) {
    return <div className="signal-explanation">
      <p className="signal-summary">
        Podobieństwo głosu ({voice.isSpeechBrain ? 'SpeechBrain ECAPA-TDNN' : 'profil widmowy'}): <strong>{pct(Math.max(0, voice.cosine))}</strong>
      </p>
      <div className="signal-chips">
        <span className="signal-chip">{labelA} <strong>{voice.voicedA} s mowy</strong></span>
        <span className="signal-chip">{labelB} <strong>{voice.voicedB} s mowy</strong></span>
      </div>
      <p className="signal-meta muted">Sygnał pomocniczy — może wskazywać tego samego lektora lub syntezator głosu ({voice.backend}).</p>
    </div>
  }

  const transcript = parseTranscript(text)
  if (transcript) {
    return <div className="signal-explanation">
      <p className="signal-summary">
        Wspólny fragment mowy ({transcript.words} słów): <strong>{pct(transcript.score)}</strong>
      </p>
      <div className="signal-match-card">
        <div className="signal-match-head">
          <span className="signal-chip">Transkrypcja Whisper</span>
          <strong>{pct(transcript.score)}</strong>
        </div>
        <p className="signal-summary">„{transcript.quote}”</p>
        <div className="signal-chips">
          <span className="signal-chip">{labelA} <strong>{fmtSec(transcript.a[0])}–{fmtSec(transcript.a[1])}</strong></span>
          <span className="signal-chip">{labelB} <strong>{fmtSec(transcript.b[0])}–{fmtSec(transcript.b[1])}</strong></span>
        </div>
      </div>
    </div>
  }

  const audioRun = parseAudioRun(text)
  if (audioRun) {
    return <div className="signal-explanation">
      <p className="signal-summary">
        Wspólna ścieżka dźwiękowa: <strong>{pct(audioRun.score)}</strong>
      </p>
      <div className="signal-chips">
        <span className="signal-chip">{labelA} <strong>{fmtSec(audioRun.a[0])}–{fmtSec(audioRun.a[1])}</strong></span>
        <span className="signal-chip">{labelB} <strong>{fmtSec(audioRun.b[0])}–{fmtSec(audioRun.b[1])}</strong></span>
        <span className="signal-chip">Błąd bitowy (BER) <strong>{audioRun.ber}</strong></span>
      </div>
    </div>
  }

  const videoRun = parseVideoRun(text)
  if (videoRun) {
    return <div className="signal-explanation">
      <p className="signal-summary">
        Wspólny fragment wideo: <strong>{pct(videoRun.score)}</strong>
      </p>
      <div className="signal-chips">
        <span className="signal-chip">{labelA} <strong>{fmtSec(videoRun.a[0])}–{fmtSec(videoRun.a[1])}</strong></span>
        <span className="signal-chip">{labelB} <strong>{fmtSec(videoRun.b[0])}–{fmtSec(videoRun.b[1])}</strong></span>
      </div>
      {videoRun.detail && <p className="signal-meta muted">{videoRun.detail}</p>}
    </div>
  }

  return <p className="muted">{text}</p>
}
