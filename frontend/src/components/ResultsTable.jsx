export default function ResultsTable({ columns = [], rows = [], truncated, maxRows = 25 }) {
  if (!columns.length) return null

  const visible = rows.slice(0, maxRows)

  return (
    <div className="panel overflow-hidden">
      <div className="max-h-96 overflow-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left">
              {columns.map((column) => (
                <th key={column} className="whitespace-nowrap px-3 py-1.5 font-medium">
                  {column.replace(/_/g, ' ')}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((row, index) => (
              <tr key={index} className="border-b border-line last:border-b-0">
                {row.map((cell, cellIndex) => (
                  <td key={cellIndex} className="whitespace-nowrap px-3 py-1.5">
                    {cell === null ? <span className="text-muted">NULL</span> : String(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="border-t border-line px-3 py-1.5 caption">
        {rows.length} row{rows.length === 1 ? '' : 's'}
        {rows.length > maxRows ? `, first ${maxRows} shown` : ''}
        {truncated ? ', capped by the backend row limit' : ''}
      </p>
    </div>
  )
}
