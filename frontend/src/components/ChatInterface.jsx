import { useEffect, useRef, useState } from 'react'
import { askQuestion, exportUrl, fetchSuggestions } from '../services/api'
import SQLViewer from './SQLViewer'
import ChartViewer from './ChartViewer'
import ResultsTable from './ResultsTable'

export default function ChatInterface({ conversationId, onAnswer }) {
  const [turns, setTurns] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [suggestions, setSuggestions] = useState([])
  const bottomRef = useRef(null)
  // `busy` only blocks a second send after React re-renders, which is too late
  // for two clicks in the same tick. This ref is set synchronously.
  const busyRef = useRef(false)

  useEffect(() => {
    fetchSuggestions().then(setSuggestions).catch(() => setSuggestions([]))
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns, busy])

  const send = async (question) => {
    const text = (question ?? input).trim()
    if (!text || busyRef.current) return
    busyRef.current = true
    setBusy(true)
    setError('')
    setInput('')
    setTurns((current) => [...current, { question: text, pending: true }])
    try {
      const answer = await askQuestion(text, conversationId)
      setTurns((current) => [...current.slice(0, -1), { question: text, answer }])
      onAnswer?.(answer)
    } catch (exc) {
      setTurns((current) => current.slice(0, -1))
      setError(exc.message)
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-y-auto">
        {(turns.length === 0 || (busy && turns.length === 1)) && suggestions.length > 0 && (
          <div className="mb-4">
            <p className="caption">Questions this data can answer:</p>
            <ul className="mt-1 space-y-0.5">
              {suggestions.map((item) => {
                const running = busy && turns[0]?.question === item
                return (
                  <li key={item}>
                    <button
                      onClick={() => send(item)}
                      disabled={busy && !running}
                      className={`group flex w-full items-center gap-2 rounded border border-transparent px-2 py-1 text-left text-sm transition-colors hover:border-line hover:bg-panel hover:text-txt focus-visible:border-accent focus-visible:text-txt disabled:cursor-default disabled:opacity-40 ${
                        running ? 'text-txt' : 'text-muted'
                      }`}
                    >
                      <span className="w-3 shrink-0 opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
                        →
                      </span>
                      <span className="flex-1">{item}</span>
                      {running && <span className="caption">running</span>}
                    </button>
                  </li>
                )
              })}
            </ul>
          </div>
        )}

        {turns.map((turn, index) => (
          <div key={index} className="border-t border-line py-4">
            <p className="text-sm font-medium">{turn.question}</p>
            {turn.pending ? (
              <p className="caption mt-2">finding tables, writing SQL, checking it, running it</p>
            ) : (
              <AnswerTurn answer={turn.answer} />
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {error && <p className="mt-3 text-sm text-danger-soft">{error}</p>}

      <form
        className="mt-4 flex gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          send()
        }}
      >
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="Ask anything about the data"
          className="field"
        />
        <button type="submit" className="btn-primary shrink-0" disabled={busy || !input.trim()}>
          Ask
        </button>
      </form>
    </div>
  )
}

function AnswerTurn({ answer }) {
  const rejected = answer.status !== 'executed'
  const kpi = answer.visualization?.chart_type === 'kpi' ? answer.visualization.kpi : null
  const figure = answer.visualization?.figure

  return (
    <div className="mt-3 space-y-3">
      <p className="text-sm leading-6">{answer.answer}</p>

      {kpi?.label && (
        <p className="text-sm">
          <span className="text-muted">{kpi.label}:</span> {kpi.formatted ?? kpi.value}
        </p>
      )}

      {answer.note && <p className="caption">{answer.note}</p>}

      <SQLViewer
        sql={answer.sql || answer.generated_sql}
        validation={answer.validation}
        attempts={answer.attempts}
        provider={answer.provider}
        model={answer.model}
        totalMs={answer.total_ms}
      />

      {rejected ? (
        <div className="panel p-3 text-sm">
          <p className="text-danger-soft">Blocked before it reached the database.</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-muted">
            {(answer.validation?.errors || [answer.message]).filter(Boolean).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      ) : (
        <>
          {figure && <ChartViewer figure={figure} chartType={answer.visualization.chart_type} />}
          <ResultsTable
            columns={answer.result?.columns}
            rows={answer.result?.rows}
            truncated={answer.result?.truncated}
          />
          {answer.analysis?.insights?.length > 0 && (
            <ul className="list-disc space-y-0.5 pl-5 text-sm text-muted">
              {answer.analysis.insights.map((insight) => (
                <li key={insight}>{insight}</li>
              ))}
            </ul>
          )}
          <p className="caption">
            export:{' '}
            <a className="link" href={exportUrl(answer.run_id, 'csv')} download>
              csv
            </a>
            ,{' '}
            <a className="link" href={exportUrl(answer.run_id, 'xlsx')} download>
              excel
            </a>
          </p>
        </>
      )}
    </div>
  )
}
