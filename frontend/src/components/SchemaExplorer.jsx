import { useEffect, useState } from 'react'
import { fetchSchema } from '../services/api'

export default function SchemaExplorer() {
  const [tables, setTables] = useState([])
  const [open, setOpen] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    fetchSchema()
      .then(setTables)
      .catch((exc) => setError(exc.message))
  }, [])

  return (
    <div>
      <p className="heading">Schema</p>
      <p className="caption mt-1">The tables the agent is allowed to read. Click one for its columns.</p>
      {error && <p className="mt-2 text-sm text-danger-soft">{error}</p>}

      <div className="mt-3 space-y-1">
        {tables.map((table) => {
          const expanded = open === table.table
          return (
            <div key={table.table} className="panel">
              <button
                className="flex w-full items-center justify-between px-3 py-1.5 text-left text-sm"
                onClick={() => setOpen(expanded ? null : table.table)}
              >
                <span className="font-mono text-[13px]">{table.table}</span>
                <span className="caption">
                  {table.row_count ?? '?'} rows {expanded ? '−' : '+'}
                </span>
              </button>
              {expanded && (
                <table className="w-full border-t border-line text-sm">
                  <tbody>
                    {table.columns.map((column) => (
                      <tr key={column.name} className="border-b border-line last:border-b-0">
                        <td className="px-3 py-1 font-mono text-[13px]">
                          {column.name}
                          {column.primary_key ? ' (pk)' : ''}
                          {column.foreign_key ? ' (fk)' : ''}
                        </td>
                        <td className="px-3 py-1 caption">{column.type}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
