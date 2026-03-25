import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  RefreshCw,
  GitBranch,
  Github,
  GitlabIcon,
  HardDrive,
  AlertCircle,
  CheckCircle,
  Clock,
  Loader2,
  Plus,
  ExternalLink,
  Search,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { api, type GitHubRepo, type GitLabRepo, type RemoteRepo, type Repo } from '../lib/api'

type Provider = 'github' | 'gitlab'

function StatusBadge({ status }: { status: Repo['status'] }) {
  const map: Record<string, { label: string; className: string; icon: React.ReactNode }> = {
    indexed: {
      label: 'Indexed',
      className: 'bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/30',
      icon: <CheckCircle className="w-3 h-3" />,
    },
    indexing: {
      label: 'Indexing...',
      className: 'bg-sky-500/15 text-sky-400 ring-1 ring-sky-500/30',
      icon: <Loader2 className="w-3 h-3 animate-spin" />,
    },
    pending: {
      label: 'Pending',
      className: 'bg-amber-500/15 text-amber-400 ring-1 ring-amber-500/30',
      icon: <Clock className="w-3 h-3" />,
    },
    error: {
      label: 'Error',
      className: 'bg-rose-500/15 text-rose-400 ring-1 ring-rose-500/30',
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

function ProviderIcon({ provider }: { provider: Provider }) {
  return provider === 'github' ? <Github className="w-4 h-4" /> : <GitlabIcon className="w-4 h-4" />
}

function formatDate(iso?: string) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

function getStars(repo: RemoteRepo, provider: Provider) {
  return provider === 'github'
    ? (repo as GitHubRepo).stargazers_count
    : (repo as GitLabRepo).star_count
}

function getFork(repo: RemoteRepo, provider: Provider) {
  return provider === 'github'
    ? (repo as GitHubRepo).fork
    : (repo as GitLabRepo).forked_from_project
}

function ImportModal({
  provider,
  existingRepos,
  onClose,
  onAdded,
}: {
  provider: Provider
  existingRepos: Repo[]
  onClose: () => void
  onAdded: () => void
}) {
  const [repos, setRepos] = useState<RemoteRepo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<Record<string, boolean>>({})
  const [adding, setAdding] = useState(false)

  const title = provider === 'github' ? 'GitHub' : 'GitLab'

  const configuredNames = useMemo(
    () => new Set(existingRepos.map((repo) => repo.name)),
    [existingRepos]
  )

  const loadRepos = useCallback(async () => {
    try {
      setLoading(true)
      setError(null)
      const data = provider === 'github'
        ? await api.github.listRepos()
        : await api.gitlab.listRepos()
      setRepos(data)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [provider])

  useEffect(() => {
    loadRepos()
  }, [loadRepos])

  const filteredRepos = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return repos
    return repos.filter((repo) =>
      repo.name.toLowerCase().includes(q) ||
      repo.full_name.toLowerCase().includes(q) ||
      (repo.description || '').toLowerCase().includes(q)
    )
  }, [repos, search])

  const selectableRepos = filteredRepos.filter((repo) => !configuredNames.has(repo.full_name.replace('/', '-')))
  const selectedRepos = selectableRepos.filter((repo) => selected[repo.full_name])

  const toggleSelected = (fullName: string) => {
    setSelected((prev) => ({ ...prev, [fullName]: !prev[fullName] }))
  }

  const handleAddSelected = async () => {
    if (!selectedRepos.length) return
    setAdding(true)
    setError(null)
    try {
      for (const repo of selectedRepos) {
        if (provider === 'github') {
          await api.github.addRepo(repo.full_name, repo.default_branch)
        } else {
          await api.gitlab.addRepo(repo.full_name, repo.default_branch)
        }
      }
      onAdded()
    } catch (e) {
      setError(String(e))
    } finally {
      setAdding(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
      <div className="flex max-h-[88vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-gray-800 bg-gray-950 shadow-2xl">
        <div className="border-b border-gray-800 bg-gradient-to-r from-gray-900 via-gray-900 to-gray-950 p-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-xs font-medium uppercase tracking-[0.18em] text-cyan-400">Repository Import</p>
              <h3 className="mt-1 flex items-center gap-2 text-lg font-semibold text-white">
                <ProviderIcon provider={provider} />
                Browse {title} repositories
              </h3>
              <p className="mt-1 text-sm text-gray-400">
                Select one or more repositories and add them directly to the indexing queue.
              </p>
            </div>
            <button onClick={onClose} className="rounded-lg p-2 text-gray-500 transition-colors hover:bg-gray-900 hover:text-gray-300">
              <span className="sr-only">Close</span>
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        <div className="border-b border-gray-800 p-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
            <div className="relative flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-600" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={`Search ${title} repositories...`}
                className="w-full rounded-xl border border-gray-700 bg-gray-900 py-2.5 pl-10 pr-3 text-sm text-gray-200 outline-none transition-colors focus:border-cyan-500"
              />
            </div>
            <button
              onClick={loadRepos}
              disabled={loading}
              className="inline-flex items-center justify-center gap-2 rounded-xl border border-gray-700 bg-gray-900 px-4 py-2.5 text-sm text-gray-300 transition-colors hover:bg-gray-800 disabled:opacity-50"
            >
              <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
              Refresh
            </button>
            <button
              onClick={handleAddSelected}
              disabled={!selectedRepos.length || adding}
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-cyan-600 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-cyan-500 disabled:opacity-50"
            >
              {adding ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
              Add {selectedRepos.length || ''} {selectedRepos.length === 1 ? 'repository' : 'repositories'}
            </button>
          </div>
          <div className="mt-3 grid gap-3 text-xs text-gray-400 lg:grid-cols-3">
            <div className="rounded-xl border border-gray-800 bg-gray-900/60 p-3">
              <p className="font-medium text-gray-200">Provider token</p>
              <p className="mt-1">If this list is empty or errors, check the token in Settings first.</p>
            </div>
            <div className="rounded-xl border border-gray-800 bg-gray-900/60 p-3">
              <p className="font-medium text-gray-200">Selection aware</p>
              <p className="mt-1">Already configured repos stay visible but are marked and cannot be re-added.</p>
            </div>
            <div className="rounded-xl border border-gray-800 bg-gray-900/60 p-3">
              <p className="font-medium text-gray-200">Persistence</p>
              <p className="mt-1">Imported repos are stored in runtime config, so remote setup does not depend on editing files.</p>
            </div>
          </div>
        </div>

        <div className="flex-1 overflow-auto p-3">
          {loading ? (
            <div className="flex items-center justify-center py-20 text-gray-500">
              <Loader2 className="mr-2 h-5 w-5 animate-spin" />
              Loading repositories...
            </div>
          ) : error ? (
            <div className="rounded-xl border border-rose-500/20 bg-rose-500/10 p-4 text-sm text-rose-300">
              {error}
            </div>
          ) : filteredRepos.length === 0 ? (
            <div className="py-20 text-center text-gray-500">
              <ProviderIcon provider={provider} />
              <p className="mt-3 text-sm">No repositories found for this provider.</p>
            </div>
          ) : (
            <div className="space-y-2">
              {filteredRepos.map((repo) => {
                const alreadyConfigured = configuredNames.has(repo.full_name.replace('/', '-'))
                return (
                  <label
                    key={repo.id}
                    className={`flex cursor-pointer items-start gap-4 rounded-2xl border p-4 transition-colors ${
                      alreadyConfigured
                        ? 'border-gray-800 bg-gray-900/40 opacity-60'
                        : selected[repo.full_name]
                        ? 'border-cyan-500/40 bg-cyan-500/10'
                        : 'border-gray-800 bg-gray-900/60 hover:border-gray-700'
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={alreadyConfigured ? true : !!selected[repo.full_name]}
                      disabled={alreadyConfigured}
                      onChange={() => toggleSelected(repo.full_name)}
                      className="mt-1 h-4 w-4 rounded border-gray-600 bg-gray-900 text-cyan-500"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="truncate font-medium text-white">{repo.full_name}</span>
                        {repo.private && (
                          <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-300">
                            Private
                          </span>
                        )}
                        {getFork(repo, provider) && (
                          <span className="rounded bg-gray-700 px-1.5 py-0.5 text-[10px] font-medium text-gray-300">
                            Fork
                          </span>
                        )}
                        {alreadyConfigured && (
                          <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-medium text-emerald-300">
                            Already added
                          </span>
                        )}
                      </div>
                      {repo.description && (
                        <p className="mt-1 truncate text-xs text-gray-500">{repo.description}</p>
                      )}
                      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-gray-500">
                        {repo.language && <span>{repo.language}</span>}
                        <span>Stars {getStars(repo, provider).toLocaleString()}</span>
                        <span>Default {repo.default_branch}</span>
                      </div>
                    </div>
                    <a
                      href={repo.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="rounded-lg p-2 text-gray-500 transition-colors hover:bg-gray-800 hover:text-gray-300"
                      title="Open repository"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <ExternalLink className="h-4 w-4" />
                    </a>
                  </label>
                )
              })}
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
  const [showImportModal, setShowImportModal] = useState(false)
  const [provider, setProvider] = useState<Provider>('github')

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

  const totalChunks = repos.reduce((sum, repo) => sum + repo.total_chunks, 0)
  const indexedCount = repos.filter((repo) => repo.status === 'indexed').length
  const remoteCount = repos.filter((repo) => repo.type !== 'local').length

  return (
    <div className="min-h-screen bg-gray-950">
      <div className="mx-auto max-w-7xl p-8">
        <div className="mb-8 flex flex-col gap-6 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.18em] text-cyan-400">Repositories</p>
            <h1 className="mt-1 flex items-center gap-2 text-2xl font-semibold text-white">
              <GitBranch className="h-6 w-6 text-cyan-400" />
              Manage indexed code sources
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-gray-400">
              Browse configured repositories, import remote GitHub and GitLab sources, and manage runtime-backed indexing without editing config files.
            </p>
          </div>

          <div className="grid gap-3 sm:grid-cols-3 xl:w-[34rem]">
            <div className="rounded-2xl border border-gray-800 bg-gray-900/70 p-4">
              <p className="text-xs uppercase tracking-wider text-gray-500">Configured</p>
              <p className="mt-2 text-2xl font-semibold text-white">{repos.length}</p>
              <p className="mt-1 text-xs text-gray-500">{remoteCount} remote sources</p>
            </div>
            <div className="rounded-2xl border border-gray-800 bg-gray-900/70 p-4">
              <p className="text-xs uppercase tracking-wider text-gray-500">Indexed</p>
              <p className="mt-2 text-2xl font-semibold text-white">{indexedCount}</p>
              <p className="mt-1 text-xs text-gray-500">ready for semantic search</p>
            </div>
            <div className="rounded-2xl border border-gray-800 bg-gray-900/70 p-4">
              <p className="text-xs uppercase tracking-wider text-gray-500">Chunks</p>
              <p className="mt-2 text-2xl font-semibold text-white">{totalChunks.toLocaleString()}</p>
              <p className="mt-1 text-xs text-gray-500">total indexed fragments</p>
            </div>
          </div>
        </div>

        <div className="mb-6 grid gap-4 xl:grid-cols-[1.3fr_0.9fr]">
          <div className="rounded-2xl border border-gray-800 bg-gradient-to-br from-gray-900 via-gray-900 to-gray-950 p-5">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
              <div>
                <p className="text-xs font-medium uppercase tracking-[0.18em] text-cyan-400">Remote Import</p>
                <h2 className="mt-1 text-lg font-semibold text-white">Pick repositories from your provider</h2>
                <p className="mt-1 text-sm text-gray-400">
                  Use provider tokens from Settings, then add repositories in bulk from GitHub or GitLab.
                </p>
              </div>
              <div className="flex flex-col gap-3 sm:flex-row">
                <select
                  value={provider}
                  onChange={(e) => setProvider(e.target.value as Provider)}
                  className="rounded-xl border border-gray-700 bg-gray-950 px-3 py-2.5 text-sm text-gray-200 outline-none transition-colors focus:border-cyan-500"
                >
                  <option value="github">GitHub</option>
                  <option value="gitlab">GitLab</option>
                </select>
                <button
                  onClick={() => setShowImportModal(true)}
                  className="inline-flex items-center justify-center gap-2 rounded-xl bg-cyan-600 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-cyan-500"
                >
                  <ProviderIcon provider={provider} />
                  Browse {provider === 'github' ? 'GitHub' : 'GitLab'}
                </button>
              </div>
            </div>
          </div>

          <div className="rounded-2xl border border-gray-800 bg-gray-900/70 p-5">
            <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-500">Actions</p>
            <div className="mt-4 flex flex-col gap-3">
              <button
                onClick={handleIndexAll}
                disabled={syncing}
                className="inline-flex items-center justify-center gap-2 rounded-xl bg-gray-100 px-4 py-2.5 text-sm font-medium text-gray-950 transition-colors hover:bg-white disabled:opacity-50"
              >
                <RefreshCw className={`h-4 w-4 ${syncing ? 'animate-spin' : ''}`} />
                Re-index all
              </button>
              <Link
                to="/settings"
                className="inline-flex items-center justify-center gap-2 rounded-xl border border-gray-700 bg-gray-900 px-4 py-2.5 text-sm text-gray-300 transition-colors hover:bg-gray-800"
              >
                Add local repositories
              </Link>
              <Link
                to="/settings"
                className="inline-flex items-center justify-center gap-2 rounded-xl border border-gray-700 bg-gray-900 px-4 py-2.5 text-sm text-gray-300 transition-colors hover:bg-gray-800"
              >
                Configure tokens and providers
              </Link>
            </div>
          </div>
        </div>

        {error && (
          <div className="mb-6 rounded-2xl border border-rose-500/20 bg-rose-500/10 p-4 text-sm text-rose-300">
            <span className="font-medium">API error:</span> {error}
          </div>
        )}

        {loading ? (
          <div className="flex h-48 items-center justify-center text-gray-600">
            <Loader2 className="mr-2 h-5 w-5 animate-spin" />
            Loading...
          </div>
        ) : repos.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-gray-800 bg-gray-900/40 py-24 text-center text-gray-500">
            <GitBranch className="mx-auto mb-4 h-12 w-12 opacity-30" />
            <p className="text-sm">No repositories configured yet.</p>
            <p className="mt-1 text-xs text-gray-600">Browse a provider above or add local repositories from Settings.</p>
          </div>
        ) : (
          <div className="overflow-hidden rounded-2xl border border-gray-800 bg-gray-900/80">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-800 text-left text-xs uppercase tracking-wider text-gray-500">
                  <th className="px-5 py-3 font-medium">Repository</th>
                  <th className="px-4 py-3 font-medium">Branch</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-4 py-3 font-medium text-right">Chunks</th>
                  <th className="px-4 py-3 font-medium">Last Indexed</th>
                  <th className="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800">
                {repos.map((repo) => (
                  <tr key={repo.name} className="transition-colors hover:bg-gray-800/40">
                    <td className="px-5 py-4">
                      <div className="flex items-center gap-3">
                        <TypeIcon type={repo.type} />
                        <div className="min-w-0">
                          <Link to={`/repos/${encodeURIComponent(repo.name)}`} className="font-medium text-white hover:text-cyan-300">
                            {repo.name}
                          </Link>
                          <div className="mt-0.5 truncate font-mono text-xs text-gray-500">
                            {repo.url || repo.path || '-'}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-4">
                      <span className="font-mono text-xs text-gray-400">{repo.branch}</span>
                    </td>
                    <td className="px-4 py-4">
                      <div>
                        <StatusBadge status={repo.status} />
                        {repo.error_message && (
                          <p className="mt-1 max-w-xs truncate text-xs text-rose-400" title={repo.error_message}>
                            {repo.error_message}
                          </p>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-4 text-right">
                      <span className="font-mono text-xs text-gray-300">
                        {repo.total_chunks > 0 ? repo.total_chunks.toLocaleString() : '-'}
                      </span>
                    </td>
                    <td className="px-4 py-4 text-xs text-gray-500">
                      {formatDate(repo.last_indexed_at)}
                    </td>
                    <td className="px-4 py-4 text-right">
                      <div className="inline-flex items-center gap-2">
                        <button
                          onClick={() => handleIndex(repo.name)}
                          disabled={indexingRepo === repo.name || repo.status === 'indexing'}
                          className="inline-flex items-center gap-1.5 rounded-lg bg-gray-800 px-3 py-1.5 text-xs text-gray-300 transition-colors hover:bg-gray-700 disabled:opacity-40"
                        >
                          <RefreshCw className={`h-3 w-3 ${indexingRepo === repo.name ? 'animate-spin' : ''}`} />
                          Index
                        </button>
                        <Link
                          to={`/repos/${encodeURIComponent(repo.name)}`}
                          className="inline-flex items-center gap-1.5 rounded-lg bg-cyan-500/10 px-3 py-1.5 text-xs text-cyan-300 transition-colors hover:bg-cyan-500/20"
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
      </div>

      {showImportModal && (
        <ImportModal
          provider={provider}
          existingRepos={repos}
          onClose={() => setShowImportModal(false)}
          onAdded={() => {
            setShowImportModal(false)
            load()
          }}
        />
      )}
    </div>
  )
}
