import { useEffect, useState } from 'react'
import { clearHistory, exportUrl, fetchHistory, fetchRun } from '../services/api'
import ResultsTable from '../components/ResultsTable'
import SQLViewer from '../components/SQLViewer'
import ChartViewer from '../components/ChartViewer'

export default function History() {
  const [runs, setRuns] = useState([])
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = () => {
    setLoading(true)
    fetchHistory()
      .then(setRuns)
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
  }, [])

  const open = (id) => fetchRun(id).then(setDetail).catch(() => setDetail(null))

  return (
    <section className="space-y-5">
      <div className="flex items-center justify-between">
        <p className="heading">Query history</p>
        <button className="link" onClick={() => clearHistory().then(load)}>
          clear
        </button>
      </div>

      <div className="panel overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left">
              <th className="px-3 py-1.5 font-medium">Question</th>
              <th className="px-3 py-1.5 font-medium">Status</th>
              <th className="px-3 py-1.5 font-medium">Rows</th>
              <th className="px-3 py-1.5 font-medium">When</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr
                key={run.id}
                onClick={() => open(run.id)}
                className="cursor-pointer border-b border-line last:border-b-0 hover:bg-page"
              >
                <td className="px-3 py-1.5">{run.question}</td>
                <td className={`px-3 py-1.5 ${run.status === 'executed' ? '' : 'text-danger-soft'}`}>{run.status}</td>
                <td className="px-3 py-1.5 caption">{run.row_count}</td>
                <td className="px-3 py-1.5 caption">{new Date(run.created_at).toLocaleString()}</td>
              </tr>
            ))}
            {!loading && runs.length === 0 && (
              <tr>
                <td colSpan={4} className="px-3 py-6 text-center caption">
                  No questions asked yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {detail && (
        <div className="space-y-3 border-t border-line pt-4">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium">{detail.question}</p>
            <p className="caption">
              export:{' '}
              <a className="link" href={exportUrl(detail.id, 'csv')} download>
                csv
              </a>
              ,{' '}
              <a className="link" href={exportUrl(detail.id, 'xlsx')} download>
                excel
              </a>
            </p>
          </div>
          <p className="text-sm leading-6">{detail.summary}</p>
          <SQLViewer sql={detail.sql} provider={detail.provider} />
          {detail.visualization?.figure && (
            <ChartViewer figure={detail.visualization.figure} chartType={detail.chart_type} />
          )}
          <ResultsTable
            columns={detail.result?.columns}
            rows={detail.result?.rows}
            truncated={detail.result?.truncated}
          />
        </div>
      )}
    </section>
  )
}
