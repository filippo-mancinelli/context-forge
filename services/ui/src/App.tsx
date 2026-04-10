import { useEffect, useState } from 'react'
import { BrowserRouter, NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { Database, Brain, Wrench, Activity, GitBranch, Sparkles, SlidersHorizontal, LogOut, Loader2 } from 'lucide-react'
import Repos from './pages/Repos'
import Memory from './pages/Memory'
import Tools from './pages/Tools'
import Jobs from './pages/Jobs'
import Search from './pages/Search'
import RepoDetail from './pages/RepoDetail'
import Settings from './pages/Settings'
import Setup, { type SetupMode } from './pages/Setup'
import Login from './pages/Login'
import { api, clearAuthToken, getAuthToken } from './lib/api'

function Sidebar({ onLogout }: { onLogout: () => void }) {
  const links: Array<{ to: string; icon: typeof GitBranch; label: string; end?: boolean }> = [
    { to: '/repos', icon: GitBranch, label: 'Repositories' },
    { to: '/memory', icon: Brain, label: 'Memory' },
    { to: '/settings', icon: SlidersHorizontal, label: 'Settings' },
    { to: '/tools', icon: Wrench, label: 'MCP Tools' },
    { to: '/jobs', icon: Activity, label: 'Async Jobs' },
  ]

  return (
    <aside className="w-48 bg-gray-900 border-r border-gray-800 flex flex-col min-h-screen">
      <div className="px-4 py-3 border-b border-gray-800">
        <div className="flex items-center gap-2">
          <Database className="w-4 h-4 text-indigo-400" />
          <span className="font-semibold text-white text-sm">context-forge</span>
        </div>
      </div>
      <nav className="flex-1 px-2 py-3 space-y-0.5">
        {links.map(({ to, icon: Icon, label, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-colors ${
                isActive
                  ? 'bg-indigo-500/15 text-indigo-400 font-medium'
                  : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800'
              }`
            }
          >
            <Icon className="w-4 h-4 flex-shrink-0" />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="px-4 py-3 border-t border-gray-800">
        <button
          onClick={onLogout}
          className="w-full inline-flex items-center justify-center gap-1.5 px-2 py-1.5 text-xs text-gray-400 hover:text-gray-200 hover:bg-gray-800 rounded-lg transition-colors"
        >
          <LogOut className="w-3.5 h-3.5" />
          Logout
        </button>
      </div>
    </aside>
  )
}

export default function App() {
  const [state, setState] = useState<'loading' | 'setup' | 'login' | 'ready'>('loading')
  const [setupMode, setSetupMode] = useState<SetupMode>('full')

  useEffect(() => {
    let mounted = true
    async function bootstrap() {
      try {
        const setup = await api.setup.status()
        if (!mounted) return
        if (!setup.is_configured) {
          setSetupMode(setup.mode === 'admin' ? 'admin' : 'full')
          setState('setup')
          return
        }
        if (!getAuthToken()) {
          setState('login')
          return
        }
        await api.auth.session()
        if (mounted) setState('ready')
      } catch {
        clearAuthToken()
        if (mounted) setState('login')
      }
    }
    bootstrap()
    return () => {
      mounted = false
    }
  }, [])

  const handleLogout = async () => {
    try {
      await api.auth.logout()
    } catch {
      // no-op
    } finally {
      clearAuthToken()
      setState('login')
    }
  }

  if (state === 'loading') {
    return (
      <div className="min-h-screen bg-gray-950 text-gray-400 flex items-center justify-center">
        <Loader2 className="w-5 h-5 animate-spin mr-2" />
        Loading...
      </div>
    )
  }

  if (state === 'setup') {
    return <Setup mode={setupMode} onCompleted={() => setState('login')} />
  }

  if (state === 'login') {
    return <Login onLoggedIn={() => setState('ready')} />
  }

  return (
    <BrowserRouter>
      <div className="flex min-h-screen">
        <Sidebar onLogout={handleLogout} />
        <main className="flex-1 overflow-auto">
          <Routes>
            <Route path="/" element={<Navigate to="/repos" replace />} />
            <Route path="/search" element={<Search />} />
            <Route path="/repos" element={<Repos />} />
            <Route path="/repos/:repoName" element={<RepoDetail />} />
            <Route path="/memory" element={<Memory />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/tools" element={<Tools />} />
            <Route path="/jobs" element={<Jobs />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
