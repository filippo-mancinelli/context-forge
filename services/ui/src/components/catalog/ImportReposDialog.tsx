import { useCallback, useEffect, useMemo, useState } from 'react'
import { ExternalLink, RefreshCw } from 'lucide-react'
import { api, type GitHubRepo, type GitLabRepo, type RemoteRepo, type Repo } from '../../lib/api'
import { Badge, Banner, Button, Card, Dialog, DialogFooter, Input, useToast } from '../ui'

export type GitProvider = 'github' | 'gitlab'

function stars(repo: RemoteRepo, provider: GitProvider) {
  return provider === 'github' ? (repo as GitHubRepo).stargazers_count : (repo as GitLabRepo).star_count
}

function isFork(repo: RemoteRepo, provider: GitProvider) {
  return provider === 'github' ? (repo as GitHubRepo).fork : (repo as GitLabRepo).forked_from_project
}

// Un repository remoto è già nel catalogo se URL e branch coincidono.
function catalogKey(url: string | undefined, branch: string) {
  return `${(url ?? '').toLowerCase().replace(/\/+$/, '').replace(/\.git$/, '')}#${branch}`
}

interface ImportReposDialogProps {
  open: boolean
  provider: GitProvider
  registered: Repo[]
  onClose: () => void
  onAdded: () => void
}

export default function ImportReposDialog({ open, provider, registered, onClose, onAdded }: ImportReposDialogProps) {
  const [repos, setRepos] = useState<RemoteRepo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<Record<string, boolean>>({})
  const [adding, setAdding] = useState(false)
  const toast = useToast()
  const title = provider === 'github' ? 'GitHub' : 'GitLab'

  const registeredKeys = useMemo(() => new Set(registered.map((r) => catalogKey(r.url, r.branch))), [registered])
  const inCatalog = useCallback(
    (repo: RemoteRepo) => registeredKeys.has(catalogKey(repo.url, repo.default_branch)),
    [registeredKeys]
  )

  const loadRepos = useCallback(async () => {
    try {
      setLoading(true)
      setError(null)
      setRepos(provider === 'github' ? await api.github.listRepos() : await api.gitlab.listRepos())
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [provider])

  useEffect(() => {
    if (!open) return
    setSelected({})
    void loadRepos()
  }, [open, loadRepos])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return repos
    return repos.filter(
      (repo) =>
        repo.name.toLowerCase().includes(q) ||
        repo.full_name.toLowerCase().includes(q) ||
        (repo.description || '').toLowerCase().includes(q)
    )
  }, [repos, search])

  const chosen = filtered.filter((repo) => selected[repo.full_name] && !inCatalog(repo))

  const handleAdd = async () => {
    if (!chosen.length) return
    setAdding(true)
    setError(null)
    try {
      for (const repo of chosen) await api.catalog.repos.import(provider, repo.full_name, repo.default_branch)
      toast.success(
        chosen.length === 1 ? `Repository "${chosen[0].full_name}" registered` : `${chosen.length} repositories registered`
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
      onOpenChange={(o) => !o && onClose()}
      title={`Browse ${title} repositories`}
      description="Selected repositories are registered in the organization catalog and indexed once."
      maxWidth="720px"
    >
      <div className="space-y-4">
        <div className="flex gap-2">
          <Input
            className="flex-1"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={`Filter ${title} repositories...`}
          />
          <Button variant="ghost" onClick={loadRepos} disabled={loading} size="sm" aria-label="Reload">
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </Button>
        </div>

        {error && <Banner variant="danger">{error}</Banner>}

        {loading ? (
          <p className="text-sm text-muted py-4">Loading repositories...</p>
        ) : filtered.length === 0 ? (
          <p className="text-sm text-muted py-4">No repositories found.</p>
        ) : (
          <Card className="overflow-y-auto scrollbar-thin max-h-[360px]">
            {filtered.map((repo) => {
              const already = inCatalog(repo)
              return (
                <label
                  key={repo.id}
                  className={[
                    'border-b border-border flex items-start gap-3 px-3 py-2.5 cursor-pointer last:border-b-0',
                    already ? 'opacity-50 cursor-not-allowed bg-surface' : 'hover:bg-surface',
                    selected[repo.full_name] && !already ? 'bg-primary-light' : '',
                  ].join(' ')}
                >
                  <input
                    type="checkbox"
                    checked={already || !!selected[repo.full_name]}
                    disabled={already}
                    onChange={() => setSelected((prev) => ({ ...prev, [repo.full_name]: !prev[repo.full_name] }))}
                    className="mt-0.5 h-3.5 w-3.5"
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-medium">{repo.full_name}</span>
                      {repo.private && <Badge variant="warning">Private</Badge>}
                      {isFork(repo, provider) && <Badge>Fork</Badge>}
                      {already && <Badge variant="success">In catalog</Badge>}
                    </div>
                    {repo.description && <p className="text-xs text-muted mt-0.5 truncate">{repo.description}</p>}
                    <div className="flex gap-3 mt-1 text-xs text-muted">
                      {repo.language && <span>{repo.language}</span>}
                      <span>{stars(repo, provider).toLocaleString()} stars</span>
                      <span>{repo.default_branch}</span>
                    </div>
                  </div>
                  <a
                    href={repo.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={(e) => e.stopPropagation()}
                    className="text-muted hover:text-accent transition-colors flex-shrink-0 mt-0.5"
                    aria-label="Open on the provider"
                  >
                    <ExternalLink className="w-3.5 h-3.5" />
                  </a>
                </label>
              )
            })}
          </Card>
        )}
      </div>

      <DialogFooter>
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button variant="primary" onClick={handleAdd} disabled={!chosen.length || adding} loading={adding}>
          Register {chosen.length > 0 ? chosen.length : ''} {chosen.length === 1 ? 'repository' : 'repositories'}
        </Button>
      </DialogFooter>
    </Dialog>
  )
}
