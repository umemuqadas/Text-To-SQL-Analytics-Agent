import { useEffect, useState } from 'react'
import ChartViewer from '../components/ChartViewer'
import { fetchHistory, fetchRun, validateSql } from '../services/api'

export default function Analytics() {
  const [runs, setRuns] = useState([])
  const [details, setDetails] = useState([])
  const [loading, setLoading] = useState(true)
  const [sql, setSql] = useState('SELECT city, COUNT(*) AS customers FROM customers GROUP BY city')
  const [verdict, setVerdict] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    fetchHistory()
      .then((list) => {
        if (cancelled) return
        setRuns(list)
        const executed = list.filter((run) => run.status === 'executed').slice(0, 6)
        return Promise.all(executed.map((run) => fetchRun(run.id)))
      })
      .then((payloads) => {
        if (cancelled || !payloads) return
        setDetails(payloads.filter(Boolean))
      })
      .catch((exc) => !cancelled && setError(exc.message))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [])

  const charts = details.filter((detail) => detail.result?.row_count > 1 && detail.visualization?.figure)

  return (
    <section className="space-y-6">
      {error && <p className="text-sm text-danger-soft">{error}</p>}
      {loading && <p className="caption">loading runs</p>}

      {!loading && runs.length === 0 && (
        <p className="caption">Nothing here yet. Ask a question first and its chart will show up on this page.</p>
      )}

      {!loading && runs.length > 0 && charts.length === 0 && (
        <p className="caption">Recent answers did not produce a chart.</p>
      )}

      {charts.length > 0 && (        <div className="grid gap-4 lg:grid-cols-2">
          {charts.map((detail) => (
            <div key={detail.id}>
              <p className="mb-2 text-sm">{detail.question}</p>
              <ChartViewer figure={detail.visualization.figure} chartType={detail.chart_type} />
            </div>
          ))}
        </div>
      )}

      <div className="panel p-3">
        <p className="heading">SQL editor</p>
        <p className="caption mt-1">Checked against the same rules as generated SQL. Nothing runs from here.</p>
        <textarea
          value={sql}
          onChange={(event) => setSql(event.target.value)}
          rows={4}
          className="field mt-3 font-mono text-[13px]"
        />
        <div className="mt-2 flex items-center gap-3">
          <button
            className="btn-primary"
            onClick={() =>
              validateSql(sql)
                .then((text) => {
                  setVerdict(text)
                  setError('')
                })
                .catch((exc) => {
                  setVerdict(null)
                  setError(exc.message)
                })
            }
          >
            Validate
          </button>
          {verdict && <span className="caption">accepted</span>}
          {error && <span className="caption text-danger-soft">{error}</span>}
        </div>
        {verdict && <pre className="mt-2 overflow-auto font-mono text-xs text-muted">{verdict}</pre>}
      </div>
    </section>
  )
}
