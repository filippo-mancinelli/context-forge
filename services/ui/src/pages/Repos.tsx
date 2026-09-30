import { useCallback, useEffect, useMemo, useState } from 'react'
import { GitBranch, Plus, RefreshCw, Square, Unlink } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api, type Repo } from '../lib/api'
import { useAppStore } from '../store'
import AddFromCatalogDialog from '../components/catalog/AddFromCatalogDialog'
import RestrictedIcon from '../components/catalog/RestrictedIcon'
import { RepoSource, RepoStatusBadge, RepoTypeIcon, formatIndexedAt } from '../components/catalog/RepoBadges'
import { canSelectResources, canSelectRestricted } from '../lib/roles'
import { useOrgRole, useProjectRole } from '../lib/useRoles'
import { Banner, Button, Card, Table, Tbody, Td, Th, Thead, Tr, useConfirm, useToast } from '../components/ui'

export default function Repos() {
  const toast = useToast()
  const confirm = useConfirm()
  const orgRole = useOrgRole()
  const projectRole = useProjectRole()
  const projectId = useAppStore((s) => s.activeProjectId)
  const canSelect = canSelectResources(projectRole) && projectId != null
  const [repos, setRepos] = useState<Repo[]>([])
  const [catalog, setCatalog] = useState<Repo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [addOpen, setAddOpen] = useState(false)
  const [busy, setBusy] = useState<number | null>(null)
  const [indexingAll, setIndexingAll] = useState(false)

  const load = useCallback(async () => {
    try {
      setRepos(await api.repos.list())
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
    // Lo stato di indicizzazione cambia in background.
    const interval = setInterval(load, 5000)
    return () => clearInterval(interval)
  }, [load, projectId])

  const openAdd = async () => {
    try {
      setCatalog((await api.catalog.repos.list()).repos)
      setAddOpen(true)
    } catch (e) {
      toast.error(String(e))
    }
  }

  const options = useMemo(
    () =>
      catalog.map((r) => ({
        id: r.id,
        name: r.name,
        detail: r.type === 'local' ? r.path ?? '' : `${r.url ?? ''} · ${r.branch}`,
        restricted: r.restricted,
      })),
    [catalog]
  )
  const selectedIds = useMemo(() => new Set(repos.map((r) => r.id)), [repos])

  const act = async (repo: Repo, action: () => Promise<unknown>, message: string) => {
    setBusy(repo.id)
    try {
      await action()
      toast.success(message)
      await load()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy(null)
    }
  }

  const indexAll = async () => {
    setIndexingAll(true)
    try {
      await api.repos.indexAll()
      toast.success('All repositories of the project queued for indexing')
      await load()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setIndexingAll(false)
    }
  }

  const removeFromProject = async (repo: Repo) => {
    if (projectId == null) return
    const ok = await confirm({
      title: 'Remove from project',
      message: `Remove «${repo.name}» from this project? It stays in the organization catalog with its index.`,
      confirmLabel: 'Remove',
      onConfirm: () => api.projects.deselectResource(projectId, 'repos', repo.id),
    })
    if (ok) {
      toast.success('Repository removed from the project')
      await load()
    }
  }

  const actions = (repo: Repo) => (
    <div className="flex items-center gap-1 justify-end whitespace-nowrap">
      {canSelect &&
        (repo.status === 'indexing' ? (
          <Button size="sm" variant="ghost" loading={busy === repo.id} title="Stop indexing" aria-label="Stop indexing"
            onClick={() => act(repo, () => api.repos.cancelIndex(repo.name), `Indexing stopped for ${repo.name}`)}>
            <Square className="w-3 h-3" />
          </Button>
        ) : (
          <Button size="sm" variant="ghost" loading={busy === repo.id} title="Re-index" aria-label="Re-index"
            onClick={() => act(repo, () => api.repos.index(repo.name), `Indexing queued for ${repo.name}`)}>
            <RefreshCw className="w-3 h-3" />
          </Button>
        ))}
      <Link to={`/repos/${encodeURIComponent(repo.name)}`} className="text-xs text-accent hover:underline mx-1">
        Open
      </Link>
      {canSelect && (
        <Button size="sm" variant="ghost" onClick={() => removeFromProject(repo)} title="Remove from project" aria-label="Remove from project">
          <Unlink className="w-3 h-3" />
        </Button>
      )}
    </div>
  )

  const totalChunks = repos.reduce((sum, repo) => sum + repo.total_chunks, 0)
  const indexedCount = repos.filter((r) => r.status === 'indexed').length

  return (
    <div className="p-4 sm:p-8">
      <div className="page-wide space-y-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h1>Repositories</h1>
            <p className="text-muted text-sm">
              {repos.length} in this project &middot; {indexedCount} indexed &middot; {totalChunks.toLocaleString()} chunks.
              Repositories are registered once in the organization catalog.
            </p>
          </div>
          {canSelect && (
            <div className="page-header-actions">
              <Button variant="secondary" onClick={indexAll} loading={indexingAll} disabled={repos.length === 0}>
                <RefreshCw className="w-3.5 h-3.5" /> Re-index all
              </Button>
              <Button variant="primary" onClick={openAdd}>
                <Plus className="w-4 h-4" /> Add from catalog
              </Button>
            </div>
          )}
        </div>

        {error && <Banner variant="danger">{error}</Banner>}

        {loading ? (
          <p className="text-sm text-muted">Loading…</p>
        ) : repos.length === 0 ? (
          <div className="rounded border border-dashed border-border p-8 text-center">
            <GitBranch className="w-6 h-6 mx-auto text-muted" />
            <p className="mt-2 text-sm text-muted">No repositories in this project yet. Add them from the organization catalog.</p>
          </div>
        ) : (
          <>
            {/* Mobile: schede impilate invece di una tabella compressa */}
            <div className="space-y-3 md:hidden">
              {repos.map((repo) => (
                <Card key={repo.id} className="p-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-start gap-2 min-w-0">
                      <span className="mt-0.5 flex-shrink-0">
                        <RepoTypeIcon type={repo.type} />
                      </span>
                      <div className="min-w-0">
                        <span className="text-sm font-medium break-words">{repo.name}</span>
                        {repo.restricted && <RestrictedIcon />}
                        <div className="text-xs text-muted">
                          <RepoSource repo={repo} />
                        </div>
                      </div>
                    </div>
                    <RepoStatusBadge repo={repo} />
                  </div>
                  <div className="flex flex-wrap gap-x-4 gap-y-1 mt-3 text-xs text-muted">
                    <span>
                      branch <code className="font-mono text-text">{repo.branch}</code>
                    </span>
                    <span>{repo.total_chunks > 0 ? `${repo.total_chunks.toLocaleString()} chunks` : 'no chunks'}</span>
                    <span>{formatIndexedAt(repo.last_indexed_at)}</span>
                  </div>
                  <div className="border-t border-border mt-3 pt-3">{actions(repo)}</div>
                </Card>
              ))}
            </div>

            <Card className="hidden md:block overflow-x-auto">
              <Table>
                <Thead>
                  <Tr>
                    <Th>Repository</Th>
                    <Th>Branch</Th>
                    <Th>Status</Th>
                    <Th className="text-right">Chunks</Th>
                    <Th>Last indexed</Th>
                    <Th className="w-36" />
                  </Tr>
                </Thead>
                <Tbody>
                  {repos.map((repo) => (
                    <Tr key={repo.id}>
                      <Td>
                        <div className="flex items-center gap-2">
                          <RepoTypeIcon type={repo.type} />
                          <div className="min-w-0">
                            <span className="text-sm font-medium">{repo.name}</span>
                            {repo.restricted && <RestrictedIcon />}
                            <div className="text-xs text-muted">
                              <RepoSource repo={repo} />
                            </div>
                          </div>
                        </div>
                      </Td>
                      <Td>
                        <code className="font-mono text-xs text-muted">{repo.branch}</code>
                      </Td>
                      <Td>
                        <RepoStatusBadge repo={repo} />
                      </Td>
                      <Td className="text-right font-mono text-xs text-muted">
                        {repo.total_chunks > 0 ? repo.total_chunks.toLocaleString() : '—'}
                      </Td>
                      <Td className="text-xs text-muted whitespace-nowrap">{formatIndexedAt(repo.last_indexed_at)}</Td>
                      <Td>{actions(repo)}</Td>
                    </Tr>
                  ))}
                </Tbody>
              </Table>
            </Card>
          </>
        )}
      </div>

      {projectId != null && (
        <AddFromCatalogDialog
          open={addOpen}
          title="Add repositories"
          kind="repos"
          projectId={projectId}
          options={options}
          selectedIds={selectedIds}
          allowRestricted={canSelectRestricted(orgRole)}
          onClose={() => setAddOpen(false)}
          onAdded={load}
        />
      )}
    </div>
  )
}
