import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  RefreshCw,
  Github,
  GitlabIcon,
  HardDrive,
  ExternalLink,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { api, type GitHubRepo, type GitLabRepo, type RemoteRepo, type Repo } from '../lib/api'
import { Button, Input, Badge, Dialog, DialogFooter, Select } from '../components/ui'

type Provider = 'github' | 'gitlab'

function repoBadgeVariant(status: Repo['status']) {
  const map: Record<Repo['status'], 'success' | 'accent' | 'warning' | 'danger'> = {
    indexed: 'success',
    indexing: 'accent',
    pending: 'warning',
    error: 'danger',
  }
  return map[status] ?? 'warning'
}

function TypeIcon({ type }: { type: Repo['type'] }) {
  if (type === 'github') return <Github className="w-3.5 h-3.5 text-muted" />
  if (type === 'gitlab') return <GitlabIcon className="w-3.5 h-3.5 text-warning" />
  return <HardDrive className="w-3.5 h-3.5 text-muted" />
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

function formatDate(iso?: string) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

function ImportDialog({
  open,
  provider,
  existingRepos,
  onClose,
  onAdded,
}: {
  open: boolean
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
    () => new Set(existingRepos.map(repo => repo.name)),
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
    if (open) loadRepos()
  }, [open, loadRepos])

  const filteredRepos = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return repos
    return repos.filter(repo =>
      repo.name.toLowerCase().includes(q) ||
      repo.full_name.toLowerCase().includes(q) ||
      (repo.description || '').toLowerCase().includes(q)
    )
  }, [repos, search])

  const selectableRepos = filteredRepos.filter(repo => !configuredNames.has(repo.full_name.replace('/', '-')))
  const selectedRepos = selectableRepos.filter(repo => selected[repo.full_name])

  const toggleSelected = (fullName: string) => {
    setSelected(prev => ({ ...prev, [fullName]: !prev[fullName] }))
  }

  const handleAdd = async () => {
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
    <Dialog
      open={open}
      onOpenChange={open => { if (!open) onClose() }}
      title={`Browse ${title} repositories`}
      description="Select repositories to add to context-forge."
      maxWidth="720px"
    >
      <div className="space-y-4">
        <div className="flex gap-2">
          <Input
            className="flex-1"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder={`Filter ${title} repositories...`}
          />
          <Button variant="ghost" onClick={loadRepos} disabled={loading} size="sm">
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </Button>
        </div>

        {error && (
          <div style={{ border: '1px solid var(--danger)', color: 'var(--danger)' }} className="text-sm p-3 bg-[#fef2f2]">
            {error}
          </div>
        )}

        {loading ? (
          <p className="text-sm text-muted py-4">Loading repositories...</p>
        ) : filteredRepos.length === 0 ? (
          <p className="text-sm text-muted py-4">No repositories found.</p>
        ) : (
          <div
            style={{ border: '1px solid var(--border)', maxHeight: '360px' }}
            className="overflow-y-auto scrollbar-thin"
          >
            {filteredRepos.map(repo => {
              const alreadyAdded = configuredNames.has(repo.full_name.replace('/', '-'))
              return (
                <label
                  key={repo.id}
                  style={{ borderBottom: '1px solid var(--border)' }}
                  className={[
                    'flex items-start gap-3 px-3 py-2.5 cursor-pointer last:border-b-0',
                    alreadyAdded ? 'opacity-50 cursor-not-allowed bg-surface' : 'hover:bg-surface',
                    selected[repo.full_name] && !alreadyAdded ? 'bg-[#eaf4fb]' : '',
                  ].join(' ')}
                >
                  <input
                    type="checkbox"
                    checked={alreadyAdded ? true : !!selected[repo.full_name]}
                    disabled={alreadyAdded}
                    onChange={() => toggleSelected(repo.full_name)}
                    className="mt-0.5 h-3.5 w-3.5"
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-medium">{repo.full_name}</span>
                      {repo.private && <Badge variant="warning">Private</Badge>}
                      {getFork(repo, provider) && <Badge>Fork</Badge>}
                      {alreadyAdded && <Badge variant="success">Added</Badge>}
                    </div>
                    {repo.description && (
                      <p className="text-xs text-muted mt-0.5 truncate">{repo.description}</p>
                    )}
                    <div className="flex gap-3 mt-1 text-xs text-muted">
                      {repo.language && <span>{repo.language}</span>}
                      <span>{getStars(repo, provider).toLocaleString()} stars</span>
                      <span>{repo.default_branch}</span>
                    </div>
                  </div>
                  <a
                    href={repo.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={e => e.stopPropagation()}
                    className="text-muted hover:text-accent transition-colors flex-shrink-0 mt-0.5"
                  >
                    <ExternalLink className="w-3.5 h-3.5" />
                  </a>
                </label>
              )
            })}
          </div>
        )}
      </div>

      <DialogFooter>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button
          variant="primary"
          onClick={handleAdd}
          disabled={!selectedRepos.length || adding}
          loading={adding}
        >
          Add {selectedRepos.length > 0 ? selectedRepos.length : ''} {selectedRepos.length === 1 ? 'repository' : 'repositories'}
        </Button>
      </DialogFooter>
    </Dialog>
  )
}

export default function Repos() {
  const [repos, setRepos] = useState<Repo[]>([])
  const [loading, setLoading] = useState(true)
  const [indexingRepo, setIndexingRepo] = useState<string | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showImport, setShowImport] = useState(false)
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
  const indexedCount = repos.filter(r => r.status === 'indexed').length

  return (
    <div className="p-8">
      <div className="page-content">
        <div className="flex items-start justify-between mb-6">
          <div>
            <h1>Repositories</h1>
            <p className="text-muted text-sm">
              {repos.length} configured &middot; {indexedCount} indexed &middot; {totalChunks.toLocaleString()} chunks
            </p>
          </div>
          <div className="flex items-center gap-2 mt-1">
            <Select
              options={[
                { value: 'github', label: 'GitHub' },
                { value: 'gitlab', label: 'GitLab' },
              ]}
              value={provider}
              onValueChange={v => setProvider(v as Provider)}
            />
            <Button variant="secondary" onClick={() => setShowImport(true)}>
              Import
            </Button>
            <Button
              variant="secondary"
              onClick={handleIndexAll}
              disabled={syncing}
              loading={syncing}
            >
              <RefreshCw className="w-3.5 h-3.5" />
              Re-index all
            </Button>
          </div>
        </div>

        {error && (
          <div
            style={{ border: '1px solid var(--danger)', color: 'var(--danger)' }}
            className="text-sm p-3 mb-4 bg-[#fef2f2]"
          >
            {error}
          </div>
        )}

        {loading ? (
          <p className="text-muted text-sm">Loading...</p>
        ) : repos.length === 0 ? (
          <div style={{ border: '1px dashed var(--border)' }} className="py-12 text-center">
            <p className="text-muted text-sm">No repositories configured.</p>
            <p className="text-muted text-xs mt-1">
              Import from a provider above or{' '}
              <Link to="/settings" className="text-accent">add local repos in Settings</Link>.
            </p>
          </div>
        ) : (
          <div style={{ border: '1px solid var(--border)' }}>
            <table className="w-full">
              <thead>
                <tr style={{ borderBottom: '2px solid var(--border)' }}>
                  <th className="text-left text-xs font-semibold uppercase tracking-wide text-muted px-4 py-2">Repository</th>
                  <th className="text-left text-xs font-semibold uppercase tracking-wide text-muted px-3 py-2">Branch</th>
                  <th className="text-left text-xs font-semibold uppercase tracking-wide text-muted px-3 py-2">Status</th>
                  <th className="text-right text-xs font-semibold uppercase tracking-wide text-muted px-3 py-2">Chunks</th>
                  <th className="text-left text-xs font-semibold uppercase tracking-wide text-muted px-3 py-2">Last indexed</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {repos.map(repo => (
                  <tr
                    key={repo.name}
                    style={{ borderBottom: '1px solid var(--border)' }}
                    className="last:border-b-0 hover:bg-surface transition-colors"
                  >
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <TypeIcon type={repo.type} />
                        <div className="min-w-0">
                          <Link
                            to={`/repos/${encodeURIComponent(repo.name)}`}
                            className="text-sm font-medium text-text hover:text-accent"
                          >
                            {repo.name}
                          </Link>
                          <div className="text-xs text-muted font-mono truncate">
                            {repo.url || repo.path || '—'}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="px-3 py-3">
                      <code className="font-mono text-xs text-muted">{repo.branch}</code>
                    </td>
                    <td className="px-3 py-3">
                      <Badge variant={repoBadgeVariant(repo.status)}>{repo.status}</Badge>
                      {repo.error_message && (
                        <p className="text-xs text-danger mt-1 max-w-xs truncate" title={repo.error_message}>
                          {repo.error_message}
                        </p>
                      )}
                    </td>
                    <td className="px-3 py-3 text-right">
                      <span className="font-mono text-xs text-muted">
                        {repo.total_chunks > 0 ? repo.total_chunks.toLocaleString() : '—'}
                      </span>
                    </td>
                    <td className="px-3 py-3 text-xs text-muted">{formatDate(repo.last_indexed_at)}</td>
                    <td className="px-3 py-3">
                      <div className="flex items-center gap-2 justify-end">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => handleIndex(repo.name)}
                          disabled={indexingRepo === repo.name || repo.status === 'indexing'}
                          loading={indexingRepo === repo.name}
                        >
                          <RefreshCw className="w-3 h-3" />
                          Index
                        </Button>
                        <Link
                          to={`/repos/${encodeURIComponent(repo.name)}`}
                          className="text-xs text-accent hover:underline"
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

      <ImportDialog
        open={showImport}
        provider={provider}
        existingRepos={repos}
        onClose={() => setShowImport(false)}
        onAdded={() => { setShowImport(false); load() }}
      />
    </div>
  )
}
