import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  RefreshCw,
  Github,
  GitlabIcon,
  GitBranch,
  HardDrive,
  ExternalLink,
  Pencil,
  Plus,
  Square,
  Trash2,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { api, type GitHubRepo, type GitLabRepo, type RemoteRepo, type Repo, type RepoCreateRequest } from '../lib/api'
import { Button, Input, Badge, Dialog, DialogFooter, Select, useConfirm, useToast } from '../components/ui'

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
  const toast = useToast()

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
      toast.success(
        selectedRepos.length === 1
          ? `Repository "${selectedRepos[0].full_name}" added`
          : `${selectedRepos.length} repositories added`
      )
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
      description="Select repositories to add to ContextForge."
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

const EMPTY_REPO_FORM: RepoCreateRequest = {
  name: '',
  type: 'local',
  url: '',
  path: '',
  branch: 'main',
  language: 'auto',
}

function RepoDialog({
  open,
  repo,
  onClose,
  onSaved,
}: {
  open: boolean
  repo: Repo | null
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<RepoCreateRequest>(EMPTY_REPO_FORM)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const toast = useToast()

  // Re-seed the form each time the dialog opens: state initializers only run
  // on first mount, so without this an edit shows stale/empty fields.
  useEffect(() => {
    if (!open) return
    setError(null)
    setForm(
      repo
        ? {
            name: repo.name,
            type: repo.type,
            url: repo.url || '',
            path: repo.path || '',
            branch: repo.branch || 'main',
            language: repo.language || 'auto',
          }
        : EMPTY_REPO_FORM
    )
  }, [open, repo])

  const set = (patch: Partial<RepoCreateRequest>) => setForm(f => ({ ...f, ...patch }))
  const isLocal = form.type === 'local'

  const submit = async () => {
    setSaving(true)
    setError(null)
    const payload: RepoCreateRequest = {
      ...form,
      url: isLocal ? undefined : form.url || undefined,
      path: isLocal ? form.path || undefined : undefined,
    }
    try {
      if (repo) await api.repos.update(repo.name, payload)
      else await api.repos.create(payload)
      toast.success(repo ? `Repository "${form.name}" updated` : `Repository "${form.name}" added`)
      onClose()
      onSaved()
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={o => { if (!o) onClose() }}
      title={repo ? `Edit ${repo.name}` : 'Add repository'}
    >
      <div className="space-y-3">
        {error && <p className="text-xs text-danger break-words">{error}</p>}
        <div className="grid gap-3 md:grid-cols-2">
          <Input
            label="Name"
            value={form.name}
            onChange={e => set({ name: e.target.value })}
            placeholder="my-repo"
          />
          <Select
            label="Type"
            value={form.type}
            onValueChange={v => set({ type: v as Repo['type'] })}
            options={[
              { value: 'local', label: 'Local' },
              { value: 'github', label: 'GitHub' },
              { value: 'gitlab', label: 'GitLab' },
            ]}
          />
        </div>
        {isLocal ? (
          <Input
            label="Local path"
            value={form.path ?? ''}
            onChange={e => set({ path: e.target.value })}
            placeholder="/repos/project (path inside the container)"
          />
        ) : (
          <Input
            label="Repository URL"
            value={form.url ?? ''}
            onChange={e => set({ url: e.target.value })}
            placeholder={`https://${form.type}.com/owner/repo`}
          />
        )}
        <div className="grid gap-3 md:grid-cols-2">
          {!isLocal && (
            <Input
              label="Branch"
              value={form.branch}
              onChange={e => set({ branch: e.target.value })}
              placeholder="main"
            />
          )}
          <Input
            label="Language"
            value={form.language ?? ''}
            onChange={e => set({ language: e.target.value })}
            placeholder="auto"
          />
        </div>
        {isLocal && (
          <p className="text-xs text-muted">
            Local repos index the mounted working tree as-is (whatever branch is checked out).
          </p>
        )}
      </div>
      <DialogFooter>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button
          variant="primary"
          onClick={submit}
          loading={saving}
          disabled={!form.name || (isLocal ? !form.path : !form.url)}
        >
          {repo ? 'Save changes' : 'Add repository'}
        </Button>
      </DialogFooter>
    </Dialog>
  )
}

function AddBranchDialog({
  repo,
  onClose,
  onSaved,
}: {
  repo: Repo | null
  onClose: () => void
  onSaved: () => void
}) {
  const [branch, setBranch] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const toast = useToast()

  useEffect(() => {
    if (repo) {
      setBranch('')
      setError(null)
    }
  }, [repo])

  const baseName = repo ? repo.name.split('@')[0] : ''
  const newName = branch ? `${baseName}@${branch}` : ''

  const submit = async () => {
    if (!repo || !branch) return
    setSaving(true)
    setError(null)
    try {
      await api.repos.create({
        name: newName,
        type: repo.type,
        url: repo.url || undefined,
        branch,
        language: repo.language || 'auto',
      })
      await api.repos.index(newName)
      toast.success(`Branch "${branch}" added and queued for indexing`)
      onClose()
      onSaved()
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog
      open={!!repo}
      onOpenChange={o => { if (!o) onClose() }}
      title={`Index another branch of ${baseName}`}
      description="Adds a separate index for this branch — searchable on its own, embeddings are reused for unchanged files."
    >
      <div className="space-y-3">
        {error && <p className="text-xs text-danger break-words">{error}</p>}
        <Input
          label="Branch"
          value={branch}
          onChange={e => setBranch(e.target.value)}
          placeholder="develop"
        />
        {newName && (
          <p className="text-xs text-muted">
            Will be indexed as <code className="font-mono text-text">{newName}</code>.
          </p>
        )}
      </div>
      <DialogFooter>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" onClick={submit} loading={saving} disabled={!branch}>
          Add & index
        </Button>
      </DialogFooter>
    </Dialog>
  )
}

export default function Repos() {
  const [repos, setRepos] = useState<Repo[]>([])
  const [loading, setLoading] = useState(true)
  const [indexingRepo, setIndexingRepo] = useState<string | null>(null)
  const [stoppingRepo, setStoppingRepo] = useState<string | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showImport, setShowImport] = useState(false)
  const [provider, setProvider] = useState<Provider>('github')
  const [showRepoDialog, setShowRepoDialog] = useState(false)
  const [editingRepo, setEditingRepo] = useState<Repo | null>(null)
  const [branchRepo, setBranchRepo] = useState<Repo | null>(null)
  const confirm = useConfirm()
  const toast = useToast()

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
      toast.success(`Indexing queued for ${name}`)
      await load()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setIndexingRepo(null)
    }
  }

  const handleStop = async (name: string) => {
    setStoppingRepo(name)
    try {
      await api.repos.cancelIndex(name)
      toast.success(`Indexing stopped for ${name}`)
      await load()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setStoppingRepo(null)
    }
  }

  const handleIndexAll = async () => {
    setSyncing(true)
    try {
      await api.repos.indexAll()
      toast.success('All repositories queued for indexing')
      await load()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setSyncing(false)
    }
  }

  const handleDelete = async (repo: Repo) => {
    const ok = await confirm({
      title: 'Remove repository',
      message: `Remove repository "${repo.name}"? Its indexed chunks are deleted too.`,
      confirmLabel: 'Remove',
      danger: true,
      onConfirm: () => api.repos.delete(repo.name),
    })
    if (!ok) return
    toast.success(`Repository "${repo.name}" removed`)
    await load()
  }

  const openEdit = (repo: Repo) => {
    setEditingRepo(repo)
    setShowRepoDialog(true)
  }

  const openAdd = () => {
    setEditingRepo(null)
    setShowRepoDialog(true)
  }

  const totalChunks = repos.reduce((sum, repo) => sum + repo.total_chunks, 0)
  const indexedCount = repos.filter(r => r.status === 'indexed').length

  return (
    <div className="p-4 sm:p-8">
      <div className="page-wide">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between mb-6">
          <div>
            <h1>Repositories</h1>
            <p className="text-muted text-sm">
              {repos.length} configured &middot; {indexedCount} indexed &middot; {totalChunks.toLocaleString()} chunks
            </p>
          </div>
          <div className="flex items-center gap-2 flex-wrap sm:flex-nowrap sm:mt-1">
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
            <Button variant="primary" onClick={openAdd}>
              <Plus className="w-3.5 h-3.5" />
              Add
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
              Import from a provider or add a local/remote repo with the buttons above.
            </p>
          </div>
        ) : (
          <>
          {/* Mobile: stacked cards use the full width instead of a squished table */}
          <div className="space-y-3 md:hidden">
            {repos.map(repo => (
              <div
                key={repo.name}
                style={{ border: '1px solid var(--border)' }}
                className="p-3"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-start gap-2 min-w-0">
                    <span className="mt-0.5 flex-shrink-0"><TypeIcon type={repo.type} /></span>
                    <div className="min-w-0">
                      <Link
                        to={`/repos/${encodeURIComponent(repo.name)}`}
                        className="text-sm font-medium text-text hover:text-accent break-words"
                      >
                        {repo.name}
                      </Link>
                      <div className="text-xs text-muted font-mono break-all">
                        {repo.url || repo.path || '—'}
                      </div>
                    </div>
                  </div>
                  <Badge variant={repoBadgeVariant(repo.status)}>{repo.status}</Badge>
                </div>
                {repo.error_message && (
                  <p className="text-xs text-danger mt-2 break-words" title={repo.error_message}>
                    {repo.error_message}
                  </p>
                )}
                <div className="flex flex-wrap gap-x-4 gap-y-1 mt-3 text-xs text-muted">
                  <span>branch <code className="font-mono text-text">{repo.branch}</code></span>
                  <span>{repo.total_chunks > 0 ? `${repo.total_chunks.toLocaleString()} chunks` : 'no chunks'}</span>
                  <span>{formatDate(repo.last_indexed_at)}</span>
                </div>
                <div
                  style={{ borderTop: '1px solid var(--border)' }}
                  className="flex items-center gap-2 mt-3 pt-3 flex-wrap"
                >
                  {repo.status === 'indexing' ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => handleStop(repo.name)}
                      disabled={stoppingRepo === repo.name}
                      loading={stoppingRepo === repo.name}
                    >
                      <Square className="w-3 h-3" />
                      Stop
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => handleIndex(repo.name)}
                      disabled={indexingRepo === repo.name}
                      loading={indexingRepo === repo.name}
                    >
                      <RefreshCw className="w-3 h-3" />
                      Index
                    </Button>
                  )}
                  {repo.type !== 'local' && (
                    <Button size="sm" variant="ghost" onClick={() => setBranchRepo(repo)} title="Index another branch">
                      <GitBranch className="w-3 h-3" />
                    </Button>
                  )}
                  <Button size="sm" variant="ghost" onClick={() => openEdit(repo)} title="Edit">
                    <Pencil className="w-3 h-3" />
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => handleDelete(repo)} title="Delete">
                    <Trash2 className="w-3 h-3" />
                  </Button>
                  <Link
                    to={`/repos/${encodeURIComponent(repo.name)}`}
                    className="text-xs text-accent hover:underline"
                  >
                    Open
                  </Link>
                </div>
              </div>
            ))}
          </div>

          {/* Desktop: full table */}
          <div style={{ border: '1px solid var(--border)' }} className="hidden md:block overflow-x-auto">
            <table className="w-full table-fixed min-w-[600px]">
              <colgroup>
                <col className="w-[40%]" />
                <col className="w-[10%]" />
                <col className="w-[10%]" />
                <col className="w-[10%]" />
                <col className="w-[15%]" />
                <col className="w-[15%]" />
              </colgroup>
              <thead>
                <tr style={{ borderBottom: '2px solid var(--border)' }}>
                  <th className="text-left text-xs font-semibold uppercase tracking-wide text-muted px-4 py-2">Repository</th>
                  <th className="text-left text-xs font-semibold uppercase tracking-wide text-muted px-3 py-2 whitespace-nowrap">Branch</th>
                  <th className="text-left text-xs font-semibold uppercase tracking-wide text-muted px-3 py-2 whitespace-nowrap">Status</th>
                  <th className="text-right text-xs font-semibold uppercase tracking-wide text-muted px-3 py-2 whitespace-nowrap">Chunks</th>
                  <th className="text-left text-xs font-semibold uppercase tracking-wide text-muted px-3 py-2 whitespace-nowrap">Last indexed</th>
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
                        <p className="text-xs text-danger mt-1 max-w-xs break-words" title={repo.error_message}>
                          {repo.error_message}
                        </p>
                      )}
                    </td>
                    <td className="px-3 py-3 text-right">
                      <span className="font-mono text-xs text-muted">
                        {repo.total_chunks > 0 ? repo.total_chunks.toLocaleString() : '—'}
                      </span>
                    </td>
                    <td className="px-3 py-3 text-xs text-muted whitespace-nowrap">{formatDate(repo.last_indexed_at)}</td>
                    <td className="px-3 py-3">
                      <div className="flex items-center gap-1 justify-end whitespace-nowrap">
                        {repo.status === 'indexing' ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => handleStop(repo.name)}
                            disabled={stoppingRepo === repo.name}
                            loading={stoppingRepo === repo.name}
                            title="Stop indexing"
                          >
                            <Square className="w-3 h-3" />
                          </Button>
                        ) : (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => handleIndex(repo.name)}
                            disabled={indexingRepo === repo.name}
                            loading={indexingRepo === repo.name}
                            title="Re-index"
                          >
                            <RefreshCw className="w-3 h-3" />
                          </Button>
                        )}
                        {repo.type !== 'local' && (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => setBranchRepo(repo)}
                            title="Index another branch"
                          >
                            <GitBranch className="w-3 h-3" />
                          </Button>
                        )}
                        <Button size="sm" variant="ghost" onClick={() => openEdit(repo)} title="Edit">
                          <Pencil className="w-3 h-3" />
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => handleDelete(repo)} title="Delete">
                          <Trash2 className="w-3 h-3" />
                        </Button>
                        <Link
                          to={`/repos/${encodeURIComponent(repo.name)}`}
                          className="text-xs text-accent hover:underline ml-1"
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
          </>
        )}
      </div>

      <ImportDialog
        open={showImport}
        provider={provider}
        existingRepos={repos}
        onClose={() => setShowImport(false)}
        onAdded={() => { setShowImport(false); load() }}
      />
      <RepoDialog
        open={showRepoDialog}
        repo={editingRepo}
        onClose={() => setShowRepoDialog(false)}
        onSaved={load}
      />
      <AddBranchDialog
        repo={branchRepo}
        onClose={() => setBranchRepo(null)}
        onSaved={load}
      />
    </div>
  )
}
