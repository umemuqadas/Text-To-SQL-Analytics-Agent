import { useEffect, useState } from 'react'
import { Route, Routes } from 'react-router-dom'
import Sidebar from './components/Sidebar'
import Dashboard from './pages/Dashboard'
import Analytics from './pages/Analytics'
import History from './pages/History'
import DataSources from './pages/DataSources'
import { fetchHealth } from './services/api'

// A per-browser conversation id keeps follow-up questions coherent.
const conversationId = sessionStorage.getItem('conversationId') ?? crypto.randomUUID()
sessionStorage.setItem('conversationId', conversationId)

export default function App() {
  const [health, setHealth] = useState(null)

  const refreshHealth = () => fetchHealth().then(setHealth).catch(() => setHealth({}))

  useEffect(() => {
    refreshHealth()
    const timer = setInterval(refreshHealth, 20000)
    return () => clearInterval(timer)
  }, [])

  // Switching datasource invalidates the schema listing, so remount that page.
  const sourceId = health?.datasource?.id || 'demo'

  return (
    <div className="flex h-screen">
      <Sidebar health={health} />
      <main className="flex-1 overflow-y-auto p-6">
        <Routes>
          <Route path="/" element={<Dashboard conversationId={conversationId} />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/history" element={<History />} />
          <Route path="/sources" element={<DataSources key={sourceId} onChanged={refreshHealth} />} />
        </Routes>
      </main>
    </div>
  )
}
