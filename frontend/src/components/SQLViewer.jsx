import { useState } from 'react'
import { downloadSql } from '../services/api'

function sourceLine(provider, model, totalMs) {
  if (!provider) return ''
  const who = model ? `${provider} (${model})` : provider
  const took = totalMs ? `, ${(totalMs / 1000).toFixed(1)} s` : ''
  return `written by ${who}${took}`
}

export default function SQLViewer({ sql, validation, attempts, provider, model, totalMs }) {
  const [copied, setCopied] = useState(false)
  if (!sql) return null

  const copy = async () => {
    await navigator.clipboard.writeText(sql)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  const blocked = validation?.ok === false
  const note = [sourceLine(provider, model, totalMs), attempts > 1 ? `after ${attempts} attempts` : '']
    .filter(Boolean)
    .join(', ')

  return (
    <div className="panel overflow-hidden">
      <div className="flex items-center justify-between border-b border-line px-3 py-1.5">
        <span className="caption">{blocked ? 'rejected by the validator' : 'passed the read-only validator'}</span>
        <span className="flex gap-3">
          <button className="link" onClick={copy}>
            {copied ? 'copied' : 'copy'}
          </button>
          <button className="link" onClick={() => downloadSql(sql)}>
            save
          </button>
        </span>
      </div>
      <pre className="overflow-auto px-3 py-2 font-mono text-[13px] leading-6">{sql}</pre>
      {(validation?.warnings?.length > 0 || note) && (
        <div className="border-t border-line px-3 py-1.5">
          {note && <p className="caption">{note}</p>}
          {validation?.warnings?.map((warning) => (
            <p key={warning} className="caption">
              {warning}
            </p>
          ))}
        </div>
      )}
    </div>
  )
}
