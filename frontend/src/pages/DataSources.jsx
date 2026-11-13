import { useEffect, useState } from 'react'
import UploadPanel from '../components/UploadPanel'
import SchemaExplorer from '../components/SchemaExplorer'
import { addConnection, deleteSource, fetchSources, setActiveSource } from '../services/api'

const KIND_LABEL = {
  builtin: 'sample warehouse',
  sqlite: 'SQLite file',
  tables: 'CSV / Excel',
  dump: 'SQL dump',
  connection: 'external server',
}

export default function DataSources({ onChanged }) {
  const [registry, setRegistry] = useState({ active_id: 'demo', sources: [] })
  const [notice, setNotice] = useState(null)
  const [connection, setConnection] = useState({ name: '', url: '' })
  const [busy, setBusy] = useState(false)

  const load = () =>
    fetchSources().then(setRegistry).catch((exc) => setNotice({ tone: 'err', text: exc.message, notes: [] }))

  useEffect(() => {
    load()
  }, [])

  const changed = () => {
    load()
    onChanged?.()
  }

  const use = (id) => {
    setBusy(true)
    setActiveSource(id)
      .then((summary) => {
        setNotice({ tone: 'ok', text: `Now answering from ${summary.name}, ${summary.tables} tables.`, notes: [] })
        changed()
      })
      .catch((exc) => setNotice({ tone: 'err', text: exc.message, notes: [] }))
      .finally(() => setBusy(false))
  }

  const remove = (id) => {
    setBusy(true)
    deleteSource(id)
      .then(() => {
        setNotice({ tone: 'ok', text: 'Source removed and its files deleted.', notes: [] })
        changed()
      })
      .catch((exc) => setNotice({ tone: 'err', text: exc.message, notes: [] }))
      .finally(() => setBusy(false))
  }

  const connect = () => {
    setBusy(true)
    addConnection(connection.name, connection.url)
      .then((result) => {
        setConnection({ name: '', url: '' })
        setNotice({
          tone: 'ok',
          text: `Registered ${result.created.name}. Choose use to point the agent at it.`,
          notes: result.created.notes || [],
        })
        changed()
      })
      .catch((exc) => setNotice({ tone: 'err', text: exc.message, notes: [] }))
      .finally(() => setBusy(false))
  }

  return (
    <section className="space-y-5">
      {notice && (
        <div className={`panel p-3 text-sm ${notice.tone === 'ok' ? 'text-muted' : 'text-danger-soft'}`}>
          <p>{notice.text}</p>
          {notice.notes.length > 0 && (
            <ul className="mt-1 list-disc space-y-0.5 pl-5 caption">
              {notice.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <UploadPanel
          onCreated={(result) => {
            setNotice({
              tone: 'ok',
              text: `Registered ${result.created.name}, ${result.created.tables.length} tables. Choose use to start querying it.`,
              notes: result.created.notes || [],
            })
            changed()
          }}
          onError={(message) => setNotice({ tone: 'err', text: message, notes: [] })}
        />

        <div className="panel p-3">
          <p className="heading">Connect a database</p>
          <div className="mt-3 space-y-2">
            <input
              value={connection.name}
              onChange={(event) => setConnection({ ...connection, name: event.target.value })}
              placeholder="Label, e.g. Shop replica"
              className="field"
            />
            <input
              value={connection.url}
              onChange={(event) => setConnection({ ...connection, url: event.target.value })}
              placeholder="postgresql://analyst@localhost:5432/shop"
              className="field font-mono text-[13px]"
            />
          </div>
          <button className="btn-primary mt-3" onClick={connect} disabled={busy || connection.url.length < 8}>
            Test and register
          </button>
          <p className="mt-2 caption">
            A SELECT-only role is best. The string, password included, is saved in
            backend/data/datasources.json.
          </p>
        </div>
      </div>

      <div className="panel overflow-hidden">
        {registry.sources.length === 0 && (
          <p className="px-3 py-3 caption">
            Only the built-in sample warehouse so far. Upload a database to add another source.
          </p>
        )}
        {registry.sources.length > 0 && (
          <table className="w-full text-sm">
            <thead>
            <tr className="border-b border-line text-left">
              <th className="px-3 py-1.5 font-medium">Source</th>
              <th className="px-3 py-1.5 font-medium">Kind</th>
              <th className="px-3 py-1.5 font-medium">Tables</th>
              <th className="px-3 py-1.5 font-medium">Added</th>
              <th className="px-3 py-1.5 text-right font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {registry.sources.map((source) => {
              const isActive = source.id === registry.active_id
              return (
                <tr key={source.id} className="border-b border-line last:border-b-0">
                  <td className="px-3 py-1.5">
                    <p>
                      {source.name}
                      {isActive && <span className="ml-2 caption">in use</span>}
                    </p>
                    <p className="max-w-md truncate caption">{source.origin}</p>
                  </td>
                  <td className="px-3 py-1.5 caption">{KIND_LABEL[source.kind] || source.kind}</td>
                  <td className="px-3 py-1.5 caption">
                    {source.tables.slice(0, 3).join(', ')}
                    {source.tables.length > 3 ? ' and more' : ''}
                  </td>
                  <td className="px-3 py-1.5 caption">
                    {source.created_at ? new Date(source.created_at).toLocaleString() : ''}
                  </td>
                  <td className="px-3 py-1.5 text-right">
                    {!isActive && (
                      <button className="link mr-3" onClick={() => use(source.id)} disabled={busy}>
                        use
                      </button>
                    )}
                    {!source.built_in && (
                      <button className="link" onClick={() => remove(source.id)} disabled={busy}>
                        delete
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
          </table>
        )}
      </div>

      <SchemaExplorer />
    </section>
  )
}
