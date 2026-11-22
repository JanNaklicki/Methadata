import { useState } from 'react'
import { api } from '../api/client'
import type { Upload } from '../api/types'
import WorkspaceIcon from './WorkspaceIcon'

type Changes = { label?: string; archived?: boolean }
type Change = (kind: 'uploads' | 'materials', id: string, changes: Changes) => Promise<boolean>

const statusLabels: Record<Upload['status'], string> = {
  queued: 'W kolejce', running: 'W toku', succeeded: 'Zakończony', failed: 'Błąd analizy',
}

function splitPath(path: string) {
  const clean = path.replace(/^ZIP!\//, '')
  const cut = clean.lastIndexOf('/')
  return cut < 0 ? { name: clean, folder: '' } : { name: clean.slice(cut + 1), folder: clean.slice(0, cut).replace(/!\//g, ' › ') }
}

function ItemActions({ label, archived, busy, onChange }: {
  label: string
  archived: boolean
  busy: boolean
  onChange: (changes: Changes) => Promise<boolean>
}) {
  const [editing, setEditing] = useState(false)

  return <div className="upload-actions row-actions">
    {editing ? <form onSubmit={async (event) => {
      event.preventDefault()
      const value = new FormData(event.currentTarget).get('label')
      if (typeof value === 'string' && await onChange({ label: value })) setEditing(false)
    }}>
      <input name="label" aria-label={`Nowa nazwa: ${label}`} defaultValue={label} required maxLength={200} disabled={busy} />
      <button type="submit" disabled={busy}>Zapisz</button>
      <button type="button" className="outline quiet" onClick={() => setEditing(false)}>Anuluj</button>
    </form> : <button type="button" className="outline quiet" disabled={busy} onClick={() => setEditing(true)}
      aria-label={`Zmień nazwę: ${label}`}>Zmień nazwę</button>}
    <button type="button" className="outline quiet" disabled={busy} onClick={() => void onChange({ archived: !archived })}
      aria-label={`${archived ? 'Przywróć' : 'Archiwizuj'}: ${label}`}>{archived ? 'Przywróć' : 'Archiwizuj'}</button>
  </div>
}

export default function UploadLibrary({ uploads, busy, change, openImport, inspect }: {
  uploads: Upload[]
  busy: boolean
  change: Change
  openImport: (id: string) => void
  inspect: (id: string) => void
}) {
  const [view, setView] = useState<'active' | 'archived'>('active')
  const [query, setQuery] = useState('')
  const names = query.trim().toLocaleLowerCase('pl')
  const items = uploads.filter((upload) => upload.archived === (view === 'archived') &&
    [upload.label, upload.filename, ...upload.materials.flatMap((material) => [material.label, ...material.source_labels])]
      .some((value) => value.toLocaleLowerCase('pl').includes(names)))

  return <section className="collection-library" aria-label="Pliki źródłowe">
    <div className="library-tools">
      <div className="view-tabs" role="group" aria-label="Zakres plików">
        <button type="button" aria-pressed={view === 'active'} onClick={() => setView('active')}>Aktywne</button>
        <button type="button" aria-pressed={view === 'archived'} onClick={() => setView('archived')}>Archiwum</button>
      </div>
      <input type="search" aria-label="Szukaj po nazwie" placeholder="Szukaj po nazwie" value={query}
        onChange={(event) => setQuery(event.target.value)} />
    </div>
    {items.length === 0 && <p className="muted">{names ? 'Brak wyników wyszukiwania.' : 'Brak plików w tym widoku.'}</p>}
    <ul className="upload-list">
      {items.map((upload) => <li key={upload.id} className="upload-record">
        <div className="upload-heading record-head">
          <div className="record-title">
            <strong>{upload.label}</strong>
            <div className="record-meta">
              <span className={`status-chip ${upload.status}`}>{statusLabels[upload.status]}</span>
              <span>{new Date(upload.created_at).toLocaleString('pl-PL', { dateStyle: 'medium', timeStyle: 'short' })}</span>
              {upload.size !== null && <span>{(upload.size / 1048576).toLocaleString('pl-PL', { maximumFractionDigits: 1 })} MiB</span>}
            </div>
          </div>
          <div className="upload-actions record-actions">
            {upload.downloadable ? <a className="button-link" href={api.downloadUpload(upload.id)}><WorkspaceIcon name="download" /> Pobierz oryginał</a> : <small>Oryginał niedostępny</small>}
            {upload.import_id && <button type="button" className="outline"
              onClick={() => openImport(upload.import_id!)}>Raport importu</button>}
            <ItemActions label={upload.label} archived={upload.archived} busy={busy}
              onChange={(changes) => change('uploads', upload.id, changes)} />
          </div>
        </div>
        {upload.materials.length > 0 && <details>
          <summary>Materiały <span className="count">{upload.materials.length}</span></summary>
          <ul className="materials">
            {upload.materials.map((material) => <li key={material.id} className="upload-heading">
              <div className="material-name"><strong>{material.label}</strong>{material.source_labels.some((name) => name !== material.label) &&
                <small> · {material.source_labels.filter((name) => name !== material.label).join(", ")}</small>}{material.archived && <small> · Zarchiwizowany</small>}
                {!material.analysable && <small> · Brak obsługiwanej analizy</small>}</div>
              <div className="upload-actions">
                {!upload.archived && !material.archived && material.analysable &&
                  <button type="button" className="outline" onClick={() => inspect(material.id)}>Pokaż w grafie</button>}
                <ItemActions label={material.label} archived={material.archived} busy={busy}
                  onChange={(changes) => change('materials', material.id, changes)} />
              </div>
            </li>)}
          </ul>
        </details>}
        {upload.members.length > 0 && <details>
          <summary>Pliki w archiwum <span className="count">{upload.members.length}</span></summary>
          <ul className="upload-members">
            {upload.members.map((member) => {
              const { name, folder } = splitPath(member.path)
              return <li key={member.id}>
                <a href={api.downloadUpload(upload.id, member.id)}>{name}</a>
                {folder && <small className="muted">{folder}</small>}
              </li>
            })}
          </ul>
        </details>}
      </li>)}
    </ul>
  </section>
}
