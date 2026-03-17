import { useEffect, useState, useCallback } from 'react'
import { RefreshCw, GitBranch, Github, GitlabIcon, HardDrive, AlertCircle, CheckCircle, Clock, Loader2, RotateCcw, Plus } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api, type Repo, type GitHubRepo } from '../lib/api'

function StatusBadge({ status }: { status: Repo['status'] }) {
  const map: Record<string, { label: string; className: string; icon: React.ReactNode }> = {
    indexed: {
      label: 'Indexed',
      className: 'bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/30',
      icon: <CheckCircle className="w-3 h-3" />,
    },
    indexing: {
      label: 'Indexing…',
      className: 'bg-indigo-500/15 text-indigo-400 ring-1 ring-indigo-500/30',
      icon: <Loader2 className="w-3 h-3 animate-spin" />,
    },
    pending: {
      label: 'Pending',
      className: 'bg-yellow-500/15 text-yellow-400 ring-1 ring-yellow-500/30',
      icon: <Clock className="w-3 h-3" />,
    },
    error: {
      label: 'Error',
      className: 'bg-red-500/15 text-red-400 ring-1 ring-red-500/30',
      icon: <AlertCircle className="w-3 h-3" />,
    },
  }
  const { label, className, icon } = map[status] ?? map.pending
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${className}`}>
      {icon}
      {label}
    </span>
  )
}

function TypeIcon({ type }: { type: Repo['type'] }) {
  if (type === 'github') return <Github className="w-4 h-4 text-gray-400" />
  if (type === 'gitlab') return <GitlabIcon className="w-4 h-4 text-orange-400" />
  return <HardDrive className="w-4 h-4 text-gray-500" />
}

function formatDate(iso?: string) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

// GitHub Import Modal Component
function GitHubImportModal({
  onClose,
  onAdded,
}: {
  onClose: () => void
  onAdded: () => void
}) {
  const [repos, setRepos] = useState<GitHubRepo[]>([])
  const [filtered, setFiltered] = useState<GitHubRepo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [adding, setAdding] = useState<string | null>(null)

  useEffect(() => {
    loadRepos()
  }, [])

  useEffect(() => {
    const q = search.toLowerCase()
    setFiltered(
      repos.filter(
        (r) =>
          r.name.toLowerCase().includes(q) ||
          r.full_name.toLowerCase().includes(q) ||
          (r.description && r.description.toLowerCase().includes(q))
      )
    )
  }, [search, repos])

  const loadRepos = async () => {
    try {
      setLoading(true)
      const data = await api.github.listRepos()
      setRepos(data)
      setFiltered(data)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  const handleAdd = async (repo: GitHubRepo) => {
    setAdding(repo.full_name)
    try {
      await api.github.addRepo(repo.full_name, repo.default_branch)
      onAdded()
    } catch (e) {
      setError(String(e))
    } finally {
      setAdding(null)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="w-full max-w-2xl bg-gray-900 border border-gray-800 rounded-xl shadow-2xl flex flex-col max-h-[85vh]">
        <div className="flex items-center justify-between p-5 border-b border-gray-800">
          <div>
            <h3 className="text-lg font-semibold text-white flex items-center gap-2">
              <Github className="w-5 h-5" />
              Import from GitHub
            </h3>
            <p className="text-xs text-gray-500 mt-0.5">
              Select repositories to add to context-forge
            </p>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300 transition-colors">
            <span className="sr-only">Close</span>
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="p-4 border-b border-gray-800">
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search your GitHub repositories..."
            className="w-full px-4 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 
                     placeholder-gray-500 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
          />
        </div>

        <div className="flex-1 overflow-auto p-2">
          {loading ? (
            <div className="flex items-center justify-center py-16 text-gray-500">
              <Loader2 className="w-5 h-5 animate-spin mr-2" />
              Loading repositories...
            </div>
          ) : error ? (
            <div className="p-4 m-2 text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg">
              <div className="flex items-center gap-2 mb-1">
                <AlertCircle className="w-4 h-4" />
                <span className="font-medium">Error</span>
              </div>
              {error}
            </div>
          ) : filtered.length === 0 ? (
            <div className="text-center py-16 text-gray-500">
              <Github className="w-12 h-12 mx-auto mb-3 opacity-20" />
              <p className="text-sm">
                {search ? 'No repositories match your search' : 'No repositories found'}
              </p>
            </div>
          ) : (
            <div className="space-y-1">
              {filtered.map((repo) => (
                <div
                  key={repo.id}
                  className="flex items-center gap-4 p-3 hover:bg-gray-800/50 rounded-lg group transition-colors"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-white truncate">{repo.full_name}</span>
                      {repo.private && (
                        <span className="px-1.5 py-0.5 text-[10px] font-medium bg-yellow-500/15 text-yellow-400 rounded">
                          Private
                        </span>
                      )}
                      {repo.fork && (
                        <span className="px-1.5 py-0.5 text-[10px] font-medium bg-gray-700 text-gray-400 rounded">
                          Fork
                        </span>
                      )}
                    </div>
                    {repo.description && (
                      <p className="text-xs text-gray-500 truncate mt-0.5">{repo.description}</p>
                    )}
                    <div className="flex items-center gap-3 mt-1.5 text-xs text-gray-500">
                      {repo.language && <span className="text-gray-400">{repo.language}</span>}
                      <span>★ {repo.stargazers_count.toLocaleString()}</span>
                      <span className="text-gray-600">default: {repo.default_branch}</span>
                    </div>
                  </div>
                  <a
                    href={repo.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="p-2 text-gray-500 hover:text-gray-300 opacity-0 group-hover:opacity-100 transition-opacity"
                    title="View on GitHub"
                  >
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                    </svg>
                  </a>
                  <button
                    onClick={() => handleAdd(repo)}
                    disabled={adding === repo.full_name}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-white 
                             bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors disabled:opacity-50"
                  >
                    {adding === repo.full_name ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <Plus className="w-3.5 h-3.5" />
                    )}
                    Add
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default function Repos() {
  const [repos, setRepos] = useState<Repo[]>([])
  const [loading, setLoading] = useState(true)
  const [indexingRepo, setIndexingRepo] = useState<string | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showGitHubModal, setShowGitHubModal] = useState(false)

  const load = useCallback(async () => {
    try {
      const data = await api.repos.list()
      setRepos(data)
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    const interval = setInterval(load, 5000)
    return () => clearInterval(interval)
  }, [load])

  const handleIndex = async (name: string) => {
    setIndexingRepo(name)
    try {
      await api.repos.index(name)
      await load()
    } finally {
      setIndexingRepo(null)
    }
  }

  const handleIndexAll = async () => {
    setSyncing(true)
    try {
      await api.repos.indexAll()
      await load()
    } finally {
      setSyncing(false)
    }
  }

  const handleSyncConfig = async () => {
    setSyncing(true)
    try {
      await api.repos.syncConfig()
      await load()
    } finally {
      setSyncing(false)
    }
  }

  const totalChunks = repos.reduce((sum, r) => sum + r.total_chunks, 0)
  const indexedCount = repos.filter(r => r.status === 'indexed').length

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-xl font-semibold text-white flex items-center gap-2">
            <GitBranch className="w-5 h-5 text-indigo-400" />
            Repositories
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            {indexedCount}/{repos.length} indexed · {totalChunks.toLocaleString()} total chunks
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowGitHubModal(true)}
            className="flex items-center gap-2 px-3 py-1.5 text-sm text-gray-300 bg-gray-800 hover:bg-gray-700 rounded-lg transition-colors"
          >
            <Github className="w-3.5 h-3.5" />
            Import from GitHub
          </button>
          <button
            onClick={handleSyncConfig}
            disabled={syncing}
            className="flex items-center gap-2 px-3 py-1.5 text-sm text-gray-300 bg-gray-800 hover:bg-gray-700 rounded-lg transition-colors disabled:opacity-50"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            Sync Config
          </button>
          <button
            onClick={handleIndexAll}
            disabled={syncing}
            className="flex items-center gap-2 px-3 py-1.5 text-sm text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${syncing ? 'animate-spin' : ''}`} />
            Re-index All
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-6 p-4 bg-red-500/10 border border-red-500/30 rounded-xl text-sm text-red-400">
          <span className="font-medium">API error:</span> {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center h-48 text-gray-600">
          <Loader2 className="w-5 h-5 animate-spin mr-2" />
          Loading…
        </div>
      ) : repos.length === 0 ? (
        <div className="text-center py-20 text-gray-600">
          <GitBranch className="w-10 h-10 mx-auto mb-3 opacity-40" />
          <p className="text-sm">No repositories configured.</p>
          <p className="text-xs mt-1">Add repos to <code className="font-mono text-gray-500">context-forge.yml</code> and click Sync Config.</p>
        </div>
      ) : (
        <div className="bg-gray-900 rounded-xl border border-gray-800 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800 text-gray-500 text-xs uppercase tracking-wider">
                <th className="text-left px-5 py-3 font-medium">Repository</th>
                <th className="text-left px-4 py-3 font-medium">Branch</th>
                <th className="text-left px-4 py-3 font-medium">Status</th>
                <th className="text-right px-4 py-3 font-medium">Chunks</th>
                <th className="text-left px-4 py-3 font-medium">Last Indexed</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {repos.map(repo => (
                <tr key={repo.name} className="hover:bg-gray-800/50 transition-colors">
                  <td className="px-5 py-3.5">
                    <div className="flex items-center gap-2.5">
                      <TypeIcon type={repo.type} />
                      <div>
                        <Link to={`/repos/${encodeURIComponent(repo.name)}`} className="font-medium text-white hover:text-indigo-300">
                          {repo.name}
                        </Link>
                        <div className="text-xs text-gray-500 font-mono mt-0.5">
                          {repo.url || repo.path || '—'}
                        </div>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3.5">
                    <span className="text-xs font-mono text-gray-400">{repo.branch}</span>
                  </td>
                  <td className="px-4 py-3.5">
                    <div>
                      <StatusBadge status={repo.status} />
                      {repo.error_message && (
                        <p className="text-xs text-red-400 mt-1 max-w-xs truncate" title={repo.error_message}>
                          {repo.error_message}
                        </p>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3.5 text-right">
                    <span className="text-gray-300 font-mono text-xs">
                      {repo.total_chunks > 0 ? repo.total_chunks.toLocaleString() : '—'}
                    </span>
                  </td>
                  <td className="px-4 py-3.5 text-gray-500 text-xs">
                    {formatDate(repo.last_indexed_at)}
                  </td>
                  <td className="px-4 py-3.5 text-right">
                    <div className="inline-flex items-center gap-2">
                      <button
                        onClick={() => handleIndex(repo.name)}
                        disabled={indexingRepo === repo.name || repo.status === 'indexing'}
                        className="flex items-center gap-1.5 px-3 py-1 text-xs text-gray-300 bg-gray-800 hover:bg-gray-700 rounded-lg transition-colors disabled:opacity-40"
                      >
                        <RefreshCw className={`w-3 h-3 ${indexingRepo === repo.name ? 'animate-spin' : ''}`} />
                        Index
                      </button>
                      <Link
                        to={`/repos/${encodeURIComponent(repo.name)}`}
                        className="inline-flex items-center gap-1.5 px-3 py-1 text-xs text-indigo-300 bg-indigo-500/10 hover:bg-indigo-500/20 rounded-lg transition-colors"
                      >
                        Open
                      </Link>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showGitHubModal && (
        <GitHubImportModal
          onClose={() => setShowGitHubModal(false)}
          onAdded={() => {
            setShowGitHubModal(false)
            load()
          }}
        />
      )}
    </div>
  )
}
