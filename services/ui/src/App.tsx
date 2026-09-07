import { useEffect, useRef, useState } from 'react'
import { BrowserRouter, Link, NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { GitBranch, Brain, Wrench, SlidersHorizontal, Menu, X, Building2, Library, MessagesSquare, Database, Braces, Globe, Server, Boxes, ChevronDown, LogOut, TerminalSquare, UserRound, ShieldCheck } from 'lucide-react'
import Repos from './pages/Repos'
import Chat from './pages/Chat'
import DataSources from './pages/DataSources'
import SshSources from './pages/SshSources'
import Approvals from './pages/Approvals'
import DataSourceDetail from './pages/DataSourceDetail'
import ApiContracts from './pages/ApiContracts'
import Memory from './pages/Memory'
import Knowledge from './pages/Knowledge'
import WebPages from './pages/WebPages'
import Environments from './pages/Environments'
import Tools from './pages/Tools'
import Jobs from './pages/Jobs'
import Search from './pages/Search'
import RepoDetail from './pages/RepoDetail'
import Settings from './pages/Settings'
import Organization from './pages/Organization'
import Setup from './pages/Setup'
import Login from './pages/Login'
import SharedChat from './pages/SharedChat'
import { api, setAuthToken } from './lib/api'
import { pendingBadge } from './lib/approvals'
import { useAppStore } from './store'
import { Logo } from './components/Logo'
import { Button, Dialog, DialogFooter, Input, useToast } from './components/ui'
import NewProjectDialog from './components/NewProjectDialog'

const navLinks: { to: string; icon: typeof GitBranch; label: string; adminOnly?: boolean }[] = [
  { to: '/chat', icon: MessagesSquare, label: 'Agent Chat' },
  { to: '/repos', icon: GitBranch, label: 'Repositories' },
  { to: '/datasources', icon: Database, label: 'Data Sources' },
  { to: '/ssh-sources', icon: TerminalSquare, label: 'SSH Files' },
  { to: '/approvals', icon: ShieldCheck, label: 'Approvals', adminOnly: true },
  { to: '/contracts', icon: Braces, label: 'API Contracts' },
  { to: '/knowledge', icon: Library, label: 'Knowledge Base' },
  { to: '/web', icon: Globe, label: 'Web Pages' },
  { to: '/memory', icon: Brain, label: 'Memory' },
  { to: '/environments', icon: Server, label: 'Environments' },
  { to: '/settings', icon: SlidersHorizontal, label: 'Settings' },
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
      <Building2 className="w-3.5 h-3.5 text-sidebar-text flex-shrink-0" />
      <select
        value={activeOrgId ?? ''}
        onChange={(e) => onSwitch(e.target.value)}
        className="flex-1 min-w-0 text-xs bg-transparent text-[var(--sidebar-text-muted)] border border-[var(--sidebar-control-border)] rounded px-1.5 py-1 focus:outline-none focus:border-primary [&>option]:bg-bg [&>option]:text-text"
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

function ProjectSwitcher() {
  const projects = useAppStore((s) => s.projects)
  const activeProjectId = useAppStore((s) => s.activeProjectId)
  const setActiveProject = useAppStore((s) => s.setActiveProject)
  const [showNew, setShowNew] = useState(false)

  if (projects.length === 0) return null

  const onSwitch = (value: string) => {
    if (value === '__new__') {
      setShowNew(true)
      return
    }
    const id = Number(value)
    // In-app: setActiveProject changes the store; the routed subtree remounts
    // via its key (see App), so every page refetches with the new project.
    if (id !== activeProjectId) setActiveProject(id)
  }

  return (
    <div className="flex items-center gap-1">
      <Boxes className="w-3.5 h-3.5 text-sidebar-text flex-shrink-0" />
      <select
        value={activeProjectId ?? ''}
        onChange={(e) => onSwitch(e.target.value)}
        className="flex-1 min-w-0 text-xs bg-transparent text-[var(--sidebar-text-muted)] border border-[var(--sidebar-control-border)] rounded px-1.5 py-1 focus:outline-none focus:border-primary [&>option]:bg-bg [&>option]:text-text"
        aria-label="Active project"
      >
        {projects.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
        <option value="__new__">+ New project…</option>
      </select>
      <NewProjectDialog open={showNew} onClose={() => setShowNew(false)} />
    </div>
  )
}

function UserMenu() {
  const currentUser = useAppStore((s) => s.currentUser)
  const organizations = useAppStore((s) => s.organizations)
  const activeOrgId = useAppStore((s) => s.activeOrgId)
  const logout = useAppStore((s) => s.logout)
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onClickOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [])

  const role = organizations.find((o) => o.id === activeOrgId)?.role

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 text-xs text-sidebar-text hover:text-white transition-colors px-2 py-1.5 rounded border border-transparent hover:border-[var(--sidebar-control-border)]"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <UserRound className="w-3.5 h-3.5" />
        <span className="max-w-[10rem] truncate">{currentUser?.username ?? 'Account'}</span>
        <ChevronDown className="w-3 h-3" />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 mt-1 w-56 rounded border border-border bg-surface text-text shadow-lg z-50"
        >
          <div className="px-3 py-2 border-b border-border">
            <div className="text-sm font-medium truncate">{currentUser?.username}</div>
            {currentUser?.email && (
              <div className="text-xs text-muted truncate">{currentUser.email}</div>
            )}
            {role && <div className="text-xs text-muted capitalize">{role}</div>}
          </div>
          <Link
            to="/organization"
            role="menuitem"
            onClick={() => setOpen(false)}
            className="flex items-center gap-2 px-3 py-2 text-sm hover:bg-bg transition-colors"
          >
            <Building2 className="w-3.5 h-3.5" /> Organization
          </Link>
          <button
            role="menuitem"
            onClick={logout}
            className="flex w-full items-center gap-2 px-3 py-2 text-sm hover:bg-bg transition-colors text-left"
          >
            <LogOut className="w-3.5 h-3.5" /> Logout
          </button>
        </div>
      )}
    </div>
  )
}

function TopBar() {
  return (
    <header className="hidden md:flex items-center justify-between px-4 h-12 bg-sidebar-bg text-white flex-shrink-0">
      <Logo />
      <div className="flex items-center gap-3">
        <OrgSwitcher />
        <ProjectSwitcher />
        <UserMenu />
      </div>
    </header>
  )
}

function NavItems({ onNavigate, showLogout = false }: { onNavigate?: () => void; showLogout?: boolean }) {
  const logout = useAppStore((s) => s.logout)
  const role = useAppStore((s) => s.organizations.find((o) => o.id === s.activeOrgId)?.role)
  const pendingApprovals = useAppStore((s) => s.pendingApprovals)
  const isOrgAdmin = role === 'admin' || role === 'owner'
  const badge = pendingBadge(pendingApprovals)
  return (
    <>
      <nav className="flex-1 py-2 px-2 overflow-y-auto scrollbar-thin">
        {navLinks
          .filter((link) => !link.adminOnly || isOrgAdmin)
          .map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              onClick={onNavigate}
              className={({ isActive }) =>
                [
                  'flex items-center gap-2.5 px-3 h-10 my-0.5 text-sm rounded border transition-colors',
                  isActive
                    ? 'text-white border-primary bg-[rgba(124,201,242,0.12)]'
                    : 'text-sidebar-text border-transparent hover:text-white',
                ].join(' ')
              }
            >
              <Icon className="w-3.5 h-3.5 flex-shrink-0" />
              <span className="flex-1">{label}</span>
              {to === '/approvals' && badge && (
                <span className="px-1.5 py-0.5 text-xs font-medium rounded bg-primary text-on-primary">
                  {badge}
                </span>
              )}
            </NavLink>
          ))}
      </nav>
      {showLogout && (
        <div className="px-4 py-3 border-t border-[var(--sidebar-border)]">
          <button
            onClick={logout}
            className="text-xs text-sidebar-text hover:text-white transition-colors w-full text-left"
          >
            Logout
          </button>
        </div>
      )}
    </>
  )
}

function Sidebar() {
  return (
    <aside className="hidden md:flex w-[250px] flex-shrink-0 flex-col h-full bg-sidebar-bg">
      <NavItems />
    </aside>
  )
}

function MobileHeader({ onMenuOpen }: { onMenuOpen: () => void }) {
  return (
    <header className="md:hidden flex items-center justify-between px-4 py-3 bg-sidebar-bg text-white sticky top-0 z-30">
      <Logo />
      <button
        onClick={onMenuOpen}
        className="text-[var(--sidebar-text-strong)] hover:text-white transition-colors p-1"
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
        style={{ width: '250px' }}
        className="fixed top-0 left-0 h-full bg-sidebar-bg z-50 flex flex-col md:hidden"
      >
        <div className="px-4 py-3 space-y-2 text-white border-b border-[var(--sidebar-border)]">
          <div className="flex items-center justify-between">
            <Logo />
            <button
              onClick={onClose}
              className="text-sidebar-text hover:text-white transition-colors p-1"
              aria-label="Close menu"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
          <OrgSwitcher />
          <ProjectSwitcher />
        </div>
        <NavItems onNavigate={onClose} showLogout />
      </div>
    </>
  )
}

export default function App() {
  // Keycloak callback redirects here with the session token in the query string.
  const oidcToken = new URLSearchParams(window.location.search).get('oidc_token')
  if (oidcToken) {
    setAuthToken(oidcToken)
    window.history.replaceState({}, '', window.location.pathname)
  }

  const authState = useAppStore((s) => s.authState)
  const setupMode = useAppStore((s) => s.setupMode)
  const setAuthState = useAppStore((s) => s.setAuthState)
  const completeLogin = useAppStore((s) => s.completeLogin)
  const bootstrap = useAppStore((s) => s.bootstrap)
  const activeOrgId = useAppStore((s) => s.activeOrgId)
  const activeProjectId = useAppStore((s) => s.activeProjectId)
  const [drawerOpen, setDrawerOpen] = useState(false)

  useEffect(() => {
    bootstrap()
  }, [bootstrap])

  // Public shared-chat link, reachable without an existing session.
  const shareMatch = window.location.pathname.match(/^\/share\/chat\/(.+)$/)
  if (shareMatch) {
    return <SharedChat token={decodeURIComponent(shareMatch[1])} />
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
      <div className="flex flex-col h-screen overflow-hidden" style={{ height: '100dvh' }}>
        <TopBar />
        <div className="flex flex-1 min-h-0">
          <Sidebar />
          <div className="flex-1 flex flex-col min-w-0">
            <MobileHeader onMenuOpen={() => setDrawerOpen(true)} />
            <Drawer open={drawerOpen} onClose={() => setDrawerOpen(false)} />
            <main className="flex-1 min-h-0 overflow-auto bg-bg">
              <Routes key={`${activeOrgId}:${activeProjectId}`}>
                <Route path="/" element={<Navigate to="/repos" replace />} />
                <Route path="/search" element={<Search />} />
                <Route path="/chat" element={<Chat />} />
                <Route path="/repos" element={<Repos />} />
                <Route path="/repos/:repoName" element={<RepoDetail />} />
                <Route path="/datasources" element={<DataSources />} />
                <Route path="/datasources/:connectionId" element={<DataSourceDetail />} />
                <Route path="/ssh-sources" element={<SshSources />} />
                <Route path="/approvals" element={<Approvals />} />
                <Route path="/contracts" element={<ApiContracts />} />
                <Route path="/knowledge" element={<Knowledge />} />
                <Route path="/web" element={<WebPages />} />
                <Route path="/memory" element={<Memory />} />
                <Route path="/environments" element={<Environments />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="/organization" element={<Organization />} />
                <Route path="/tools" element={<Tools />} />
                <Route path="/jobs" element={<Jobs />} />
              </Routes>
            </main>
          </div>
        </div>
      </div>
    </BrowserRouter>
  )
}
