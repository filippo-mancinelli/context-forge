import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'
import { Database, Brain, Wrench, Activity, GitBranch, Home, Sparkles } from 'lucide-react'
import Repos from './pages/Repos'
import Memory from './pages/Memory'
import Tools from './pages/Tools'
import Jobs from './pages/Jobs'
import Dashboard from './pages/Dashboard'
import Search from './pages/Search'
import RepoDetail from './pages/RepoDetail'

function Sidebar() {
  const links = [
    { to: '/', icon: Home, label: 'Dashboard', end: true },
    { to: '/search', icon: Sparkles, label: 'Cross Search' },
    { to: '/repos', icon: GitBranch, label: 'Repositories' },
    { to: '/memory', icon: Brain, label: 'Memory' },
    { to: '/tools', icon: Wrench, label: 'MCP Tools' },
    { to: '/jobs', icon: Activity, label: 'Async Jobs' },
  ]

  return (
    <aside className="w-56 bg-gray-900 border-r border-gray-800 flex flex-col min-h-screen">
      <div className="px-5 py-5 border-b border-gray-800">
        <div className="flex items-center gap-2.5">
          <Database className="w-5 h-5 text-indigo-400" />
          <span className="font-semibold text-white text-sm tracking-tight">context-forge</span>
        </div>
        <p className="text-xs text-gray-500 mt-0.5 ml-7">agent platform</p>
      </div>
      <nav className="flex-1 px-3 py-4 space-y-0.5">
        {links.map(({ to, icon: Icon, label, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
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
      <div className="px-5 py-4 border-t border-gray-800">
        <p className="text-xs text-gray-600">MCP: :4000/mcp</p>
        <p className="text-xs text-gray-600">API: :8000/api</p>
      </div>
    </aside>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex min-h-screen">
        <Sidebar />
        <main className="flex-1 overflow-auto">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/search" element={<Search />} />
            <Route path="/repos" element={<Repos />} />
            <Route path="/repos/:repoName" element={<RepoDetail />} />
            <Route path="/memory" element={<Memory />} />
            <Route path="/tools" element={<Tools />} />
            <Route path="/jobs" element={<Jobs />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
