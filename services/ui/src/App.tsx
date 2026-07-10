import { useEffect, useState } from 'react'
import { BrowserRouter, NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { GitBranch, Brain, Wrench, SlidersHorizontal, Menu, X, Building2, Library, MessagesSquare, Database, Braces, Globe } from 'lucide-react'
import Repos from './pages/Repos'
import Chat from './pages/Chat'
import DataSources from './pages/DataSources'
import DataSourceDetail from './pages/DataSourceDetail'
import ApiContracts from './pages/ApiContracts'
import Memory from './pages/Memory'
import Knowledge from './pages/Knowledge'
import WebPages from './pages/WebPages'
import Tools from './pages/Tools'
import Jobs from './pages/Jobs'
import Search from './pages/Search'
import RepoDetail from './pages/RepoDetail'
import Settings from './pages/Settings'
import Organization from './pages/Organization'
import Setup from './pages/Setup'
import Login from './pages/Login'
import AcceptInvite from './pages/AcceptInvite'
import SharedChat from './pages/SharedChat'
import OAuth from './pages/OAuth'
import { api } from './lib/api'
import { useAppStore } from './store'
import { Logo } from './components/Logo'
import { Button, Dialog, DialogFooter, Input, useToast } from './components/ui'

const navLinks = [
  { to: '/chat', icon: MessagesSquare, label: 'Agent Chat' },
  { to: '/repos', icon: GitBranch, label: 'Repositories' },
  { to: '/datasources', icon: Database, label: 'Data Sources' },
  { to: '/contracts', icon: Braces, label: 'API Contracts' },
  { to: '/knowledge', icon: Library, label: 'Knowledge Base' },
  { to: '/web', icon: Globe, label: 'Web Pages' },
  { to: '/memory', icon: Brain, label: 'Memory' },
  { to: '/settings', icon: SlidersHorizontal, label: 'Settings' },
  { to: '/organization', icon: Building2, label: 'Organization' },
  { to: '/tools', icon: Wrench, label: 'MCP Tools' },
  //{ to: '/jobs', icon: Activity, label: 'Async Jobs' },
]

function NewOrgDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const setActiveOrg = useAppStore((s) => s.setActiveOrg)
  const toast = useToast()
  const [name, setName] = useState('')
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    if (open) setName('')
  }, [open])

  const create = async () => {
    setCreating(true)
    try {
      const res = await api.organizations.create(name.trim())
      setActiveOrg(res.organization.id)
      // Reload so every page refetches with the new organization scope.
      window.location.reload()
    } catch (e) {
      toast.error(String(e))
      setCreating(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose() }} title="New organization">
      <Input
        label="Organization name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="e.g. My Team"
      />
      <DialogFooter>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" onClick={create} loading={creating} disabled={!name.trim()}>
          Create
        </Button>
      </DialogFooter>
    </Dialog>
  )
}

function OrgSwitcher() {
  const organizations = useAppStore((s) => s.organizations)
  const activeOrgId = useAppStore((s) => s.activeOrgId)
  const setActiveOrg = useAppStore((s) => s.setActiveOrg)
  const [showNewOrg, setShowNewOrg] = useState(false)

  if (organizations.length === 0) return null

  const onSwitch = (value: string) => {
    if (value === '__new__') {
      setShowNewOrg(true)
      return
    }
    const id = Number(value)
    if (id !== activeOrgId) {
      setActiveOrg(id)
      // Reload so every page refetches with the new organization scope.
      window.location.reload()
    }
  }

  return (
    <div className="flex items-center gap-1">
      <Building2 className="w-3.5 h-3.5 text-muted flex-shrink-0" />
      <select
        value={activeOrgId ?? ''}
        onChange={(e) => onSwitch(e.target.value)}
        className="flex-1 min-w-0 text-xs bg-bg text-text border border-border rounded px-1.5 py-1 focus:outline-none focus:border-accent"
        aria-label="Active organization"
      >
        {organizations.map((o) => (
          <option key={o.id} value={o.id}>
            {o.name}
          </option>
        ))}
        <option value="__new__">+ New organization…</option>
      </select>
      <NewOrgDialog open={showNewOrg} onClose={() => setShowNewOrg(false)} />
    </div>
  )
}

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
      className="hidden md:flex flex-col h-full bg-surface flex-shrink-0"
    >
      <div
        style={{ borderBottom: '1px solid var(--border)' }}
        className="px-4 py-3 space-y-2"
      >
        <Logo />
        <OrgSwitcher />
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
      <Logo />
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
          className="px-4 py-3 space-y-2"
        >
          <div className="flex items-center justify-between">
            <Logo />
            <button
              onClick={onClose}
              className="text-muted hover:text-text transition-colors p-1"
              aria-label="Close menu"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
          <OrgSwitcher />
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
  const completeLogin = useAppStore((s) => s.completeLogin)
  const bootstrap = useAppStore((s) => s.bootstrap)
  const [drawerOpen, setDrawerOpen] = useState(false)

  useEffect(() => {
    bootstrap()
  }, [bootstrap])

  // Public shared-chat link, reachable without an existing session.
  const shareMatch = window.location.pathname.match(/^\/share\/chat\/(.+)$/)
  if (shareMatch) {
    return <SharedChat token={decodeURIComponent(shareMatch[1])} />
  }

  // Public invite-acceptance link, reachable without an existing session.
  const inviteMatch = window.location.pathname.match(/^\/invite\/(.+)$/)
  if (inviteMatch) {
    return (
      <AcceptInvite
        token={decodeURIComponent(inviteMatch[1])}
        onAccepted={() => {
          window.location.href = '/'
        }}
      />
    )
  }

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
    return <Login onLoggedIn={() => completeLogin()} />
  }

  return (
    <BrowserRouter>
      {/* 100dvh tracks the real visible viewport on mobile (h-screen is the fallback). */}
      <div className="flex h-screen overflow-hidden" style={{ height: '100dvh' }}>
        <Sidebar />
        <div className="flex-1 flex flex-col min-w-0">
          <MobileHeader onMenuOpen={() => setDrawerOpen(true)} />
          <Drawer open={drawerOpen} onClose={() => setDrawerOpen(false)} />
          <main className="flex-1 min-h-0 overflow-auto bg-bg">
            <Routes>
              <Route path="/" element={<Navigate to="/repos" replace />} />
              <Route path="/search" element={<Search />} />
              <Route path="/chat" element={<Chat />} />
              <Route path="/repos" element={<Repos />} />
              <Route path="/repos/:repoName" element={<RepoDetail />} />
              <Route path="/datasources" element={<DataSources />} />
              <Route path="/datasources/:connectionId" element={<DataSourceDetail />} />
              <Route path="/contracts" element={<ApiContracts />} />
              <Route path="/knowledge" element={<Knowledge />} />
              <Route path="/web" element={<WebPages />} />
              <Route path="/memory" element={<Memory />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/organization" element={<Organization />} />
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
