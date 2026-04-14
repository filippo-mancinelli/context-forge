import { useEffect, useState } from 'react'
import { BrowserRouter, NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { GitBranch, Brain, Wrench, Activity, SlidersHorizontal, Menu, X } from 'lucide-react'
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

function NavItems({ onNavigate }: { onNavigate?: () => void }) {
  const logout = useAppStore((s) => s.logout)
  return (
    <>
      <nav className="flex-1 py-2">
        {navLinks.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            onClick={onNavigate}
            className={({ isActive }) =>
              [
                'flex items-center gap-2.5 px-4 py-2.5 text-sm transition-colors',
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
    </>
  )
}

function Sidebar() {
  return (
    <aside
      style={{ borderRight: '1px solid var(--border)', minWidth: '180px', width: '180px' }}
      className="hidden md:flex flex-col min-h-screen bg-surface flex-shrink-0"
    >
      <div
        style={{ borderBottom: '1px solid var(--border)' }}
        className="px-4 py-3"
      >
        <span className="font-semibold text-text text-sm tracking-tight">context-forge</span>
      </div>
      <NavItems />
    </aside>
  )
}

function MobileHeader({ onMenuOpen }: { onMenuOpen: () => void }) {
  return (
    <header
      style={{ borderBottom: '1px solid var(--border)' }}
      className="md:hidden flex items-center justify-between px-4 py-3 bg-surface sticky top-0 z-30"
    >
      <span className="font-semibold text-text text-sm tracking-tight">context-forge</span>
      <button
        onClick={onMenuOpen}
        className="text-muted hover:text-text transition-colors p-1"
        aria-label="Open menu"
      >
        <Menu className="w-5 h-5" />
      </button>
    </header>
  )
}

function Drawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  useEffect(() => {
    if (open) {
      document.body.style.overflow = 'hidden'
    } else {
      document.body.style.overflow = ''
    }
    return () => { document.body.style.overflow = '' }
  }, [open])

  if (!open) return null

  return (
    <>
      <div
        className="fixed inset-0 bg-black/40 z-40 md:hidden"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        style={{ borderRight: '1px solid var(--border)', width: '240px' }}
        className="fixed top-0 left-0 h-full bg-surface z-50 flex flex-col md:hidden"
      >
        <div
          style={{ borderBottom: '1px solid var(--border)' }}
          className="px-4 py-3 flex items-center justify-between"
        >
          <span className="font-semibold text-text text-sm tracking-tight">context-forge</span>
          <button
            onClick={onClose}
            className="text-muted hover:text-text transition-colors p-1"
            aria-label="Close menu"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <NavItems onNavigate={onClose} />
      </div>
    </>
  )
}

export default function App() {
  const authState = useAppStore((s) => s.authState)
  const setupMode = useAppStore((s) => s.setupMode)
  const setAuthState = useAppStore((s) => s.setAuthState)
  const bootstrap = useAppStore((s) => s.bootstrap)
  const [drawerOpen, setDrawerOpen] = useState(false)

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
        <div className="flex-1 flex flex-col min-w-0">
          <MobileHeader onMenuOpen={() => setDrawerOpen(true)} />
          <Drawer open={drawerOpen} onClose={() => setDrawerOpen(false)} />
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
      </div>
    </BrowserRouter>
  )
}
