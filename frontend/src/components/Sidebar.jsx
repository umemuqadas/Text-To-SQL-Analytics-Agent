import { NavLink } from 'react-router-dom'

const NAV = [
  { to: '/', label: 'Ask' },
  { to: '/analytics', label: 'Analytics' },
  { to: '/history', label: 'History' },
  { to: '/sources', label: 'Sources' },
]

function llmLine(llm) {
  if (!llm) return 'checking the model'
  if (llm.status === 'ok') return `${llm.provider} is online`
  if (llm.status === 'no_model') return `ollama has no ${llm.expected} yet`
  return 'no model, using the rule-based generator'
}

export default function Sidebar({ health }) {
  const db = health?.database
  const source = health?.datasource

  return (
    <aside className="w-52 shrink-0 border-r border-line p-4">
      <p className="text-sm font-semibold">Analytics Agent</p>

      <nav className="mt-5 space-y-0.5 text-sm">
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) => `block rounded px-2 py-1 ${isActive ? 'bg-panel text-txt' : 'text-muted hover:text-txt'}`}
          >
            {item.label}
          </NavLink>
        ))}
      </nav>

      <div className="mt-6 space-y-1 caption">
        <p>{source ? `${source.name}, ${source.tables} tables` : 'connecting to the database'}</p>
        <p>{db?.database === 'ok' ? `${db.dialect} online` : 'database offline'}</p>
        <p>{llmLine(health?.llm)}</p>
      </div>
    </aside>
  )
}
