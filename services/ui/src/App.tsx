import { useEffect } from 'react'
import { BrowserRouter, NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { GitBranch, Brain, Wrench, Activity, SlidersHorizontal } from 'lucide-react'
import Repos from './pages/Repos'
import Memory from './pages/Memory'
import Tools from './pages/Tools'
import Jobs from './pages/Jobs'
import Search from './pages/Search'
import RepoDetail from './pages/RepoDetail'
import Settings from './pages/Settings'
import Setup from './pages/Setup'
import Login from './pages/Login'
import OAuth from './pages/OAuth'
import { useAppStore } from './store'

const navLinks = [
  { to: '/repos', icon: GitBranch, label: 'Repositories' },
  { to: '/memory', icon: Brain, label: 'Memory' },
  { to: '/settings', icon: SlidersHorizontal, label: 'Settings' },
  { to: '/tools', icon: Wrench, label: 'MCP Tools' },
  { to: '/jobs', icon: Activity, label: 'Async Jobs' },
]

function Sidebar() {
  const logout = useAppStore((s) => s.logout)

  return (
    <aside
      style={{ borderRight: '1px solid var(--border)', minWidth: '180px', width: '180px' }}
      className="flex flex-col min-h-screen bg-surface"
    >
      <div
        style={{ borderBottom: '1px solid var(--border)' }}
        className="px-4 py-3"
      >
        <span className="font-semibold text-text text-sm tracking-tight">context-forge</span>
      </div>

      <nav className="flex-1 py-2">
        {navLinks.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              [
                'flex items-center gap-2.5 px-4 py-2 text-sm transition-colors',
                isActive
                  ? 'text-accent font-medium border-l-2 border-accent bg-[#eaf4fb] pl-[14px]'
                  : 'text-muted hover:text-text border-l-2 border-transparent pl-[14px]',
              ].join(' ')
            }
          >
            <Icon className="w-3.5 h-3.5 flex-shrink-0" />
            {label}
          </NavLink>
        ))}
      </nav>

      <div style={{ borderTop: '1px solid var(--border)' }} className="px-4 py-3">
        <button
          onClick={logout}
          className="text-xs text-muted hover:text-text transition-colors w-full text-left"
        >
          Logout
        </button>
      </div>
    </aside>
  )
}

export default function App() {
  const authState = useAppStore((s) => s.authState)
  const setupMode = useAppStore((s) => s.setupMode)
  const setAuthState = useAppStore((s) => s.setAuthState)
  const bootstrap = useAppStore((s) => s.bootstrap)

  useEffect(() => {
    bootstrap()
  }, [bootstrap])

  if (authState === 'loading') {
    return (
      <div
        style={{ minHeight: '100vh', color: 'var(--muted)' }}
        className="flex items-center justify-center text-sm"
      >
        Loading...
      </div>
    )
  }

  if (authState === 'setup') {
    return <Setup mode={setupMode} onCompleted={() => setAuthState('login')} />
  }

  if (authState === 'login') {
    return <Login onLoggedIn={() => setAuthState('ready')} />
  }

  return (
    <BrowserRouter>
      <div className="flex min-h-screen">
        <Sidebar />
        <main className="flex-1 overflow-auto bg-bg">
          <Routes>
            <Route path="/" element={<Navigate to="/repos" replace />} />
            <Route path="/search" element={<Search />} />
            <Route path="/repos" element={<Repos />} />
            <Route path="/repos/:repoName" element={<RepoDetail />} />
            <Route path="/memory" element={<Memory />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/tools" element={<Tools />} />
            <Route path="/jobs" element={<Jobs />} />
            <Route path="/oauth" element={<OAuth />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
