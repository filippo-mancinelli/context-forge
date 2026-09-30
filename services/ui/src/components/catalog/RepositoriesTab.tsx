import { useCallback, useEffect, useState } from 'react'
import { GitBranch, Pencil, Plus, RefreshCw, Square, Trash2 } from 'lucide-react'
import { api, type CatalogProjectRef, type Repo } from '../../lib/api'
import { Banner, Button, Card, Select, Table, Tbody, Td, Th, Thead, Tr, useConfirm, useToast } from '../ui'
import AddBranchDialog from './AddBranchDialog'
import ImportReposDialog, { type GitProvider } from './ImportReposDialog'
import RepoDialog from './RepoDialog'
import { RepoSource, RepoStatusBadge, RepoTypeIcon } from './RepoBadges'
import RestrictedIcon from './RestrictedIcon'

export default function RepositoriesTab({ canManage }: { canManage: boolean }) {
  const toast = useToast()
  const confirm = useConfirm()
  const [repos, setRepos] = useState<Repo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [provider, setProvider] = useState<GitProvider>('gitlab')
  const [importOpen, setImportOpen] = useState(false)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<Repo | null>(null)
  const [branchOf, setBranchOf] = useState<Repo | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      setRepos((await api.catalog.repos.list()).repos)
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
  }, [load])

  const run = async (repo: Repo, action: () => Promise<unknown>, message: string) => {
    setBusyId(repo.id)
    try {
      await action()
      toast.success(message)
      await load()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusyId(null)
    }
  }

  const remove = async (repo: Repo) => {
    let projects: CatalogProjectRef[] = []
    try {
      projects = (await api.catalog.repos.projects(repo.id)).projects
    } catch (e) {
      toast.error(String(e))
      return
    }
    const ok = await confirm({
      title: 'Delete repository',
      message: projects.length ? (
        <>
          <p>«{repo.name}» and its index are used by these projects and will be removed from them:</p>
          <ul className="list-disc pl-5 mt-2">
            {projects.map((p) => (
              <li key={p.id}>{p.name}</li>
            ))}
          </ul>
        </>
      ) : (
        `Delete «${repo.name}» and its index?`
      ),
      confirmLabel: 'Delete',
      danger: true,
      onConfirm: () => api.catalog.repos.delete(repo.id),
    })
    if (ok) {
      toast.success('Repository deleted')
      await load()
    }
  }

  return (
    <div className="space-y-4">
      {canManage && (
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Select
            value={provider}
            onValueChange={(v) => setProvider(v as GitProvider)}
            options={[
              { value: 'gitlab', label: 'GitLab' },
              { value: 'github', label: 'GitHub' },
            ]}
          />
          <Button variant="secondary" onClick={() => setImportOpen(true)}>
            Import
          </Button>
          <Button
            variant="primary"
            onClick={() => {
              setEditing(null)
              setDialogOpen(true)
            }}
          >
            <Plus className="w-4 h-4" /> Add repository
          </Button>
        </div>
      )}

      {error && <Banner variant="danger">{error}</Banner>}

      {loading ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : repos.length === 0 ? (
        <div className="rounded border border-dashed border-border p-8 text-center">
          <GitBranch className="w-6 h-6 mx-auto text-muted" />
          <p className="mt-2 text-sm text-muted">No repositories registered yet.</p>
        </div>
      ) : (
        <Card className="overflow-x-auto">
          <Table>
            <Thead>
              <Tr>
                <Th>Name</Th>
                <Th>Source</Th>
                <Th>Projects</Th>
                <Th>Status</Th>
                <Th className="text-right">Chunks</Th>
                {canManage && <Th className="w-40" />}
              </Tr>
            </Thead>
            <Tbody>
              {repos.map((r) => (
                <Tr key={r.id}>
                  <Td>
                    <div className="flex items-center gap-2">
                      <RepoTypeIcon type={r.type} />
                      <span className="font-medium">{r.name}</span>
                      {r.restricted && <RestrictedIcon />}
                    </div>
                    {r.description && <p className="text-xs text-muted mt-0.5 max-w-xs truncate">{r.description}</p>}
                  </Td>
                  <Td className="text-xs">
                    <RepoSource repo={r} />
                    {r.type !== 'local' && <code className="font-mono text-muted">{r.branch}</code>}
                  </Td>
                  <Td className="text-xs text-muted">{r.project_count ?? 0}</Td>
                  <Td>
                    <RepoStatusBadge repo={r} />
                  </Td>
                  <Td className="text-right text-xs font-mono text-muted">
                    {r.total_chunks > 0 ? r.total_chunks.toLocaleString() : '—'}
                  </Td>
                  {canManage && (
                    <Td>
                      <div className="flex items-center gap-1 justify-end">
                        {r.status === 'indexing' ? (
                          <Button size="sm" variant="ghost" loading={busyId === r.id} title="Stop indexing" aria-label="Stop indexing"
                            onClick={() => run(r, () => api.catalog.repos.cancelIndex(r.id), `Indexing stopped for ${r.name}`)}>
                            <Square className="w-3.5 h-3.5" />
                          </Button>
                        ) : (
                          <Button size="sm" variant="ghost" loading={busyId === r.id} title="Re-index" aria-label="Re-index"
                            onClick={() => run(r, () => api.catalog.repos.index(r.id), `Indexing queued for ${r.name}`)}>
                            <RefreshCw className="w-3.5 h-3.5" />
                          </Button>
                        )}
                        {r.type !== 'local' && (
                          <Button size="sm" variant="ghost" onClick={() => setBranchOf(r)} title="Index another branch" aria-label="Index another branch">
                            <GitBranch className="w-3.5 h-3.5" />
                          </Button>
                        )}
                        <Button size="sm" variant="ghost" title="Edit" aria-label="Edit"
                          onClick={() => {
                            setEditing(r)
                            setDialogOpen(true)
                          }}>
                          <Pencil className="w-3.5 h-3.5" />
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => remove(r)} title="Delete" aria-label="Delete">
                          <Trash2 className="w-3.5 h-3.5" />
                        </Button>
                      </div>
                    </Td>
                  )}
                </Tr>
              ))}
            </Tbody>
          </Table>
        </Card>
      )}

      <ImportReposDialog
        open={importOpen}
        provider={provider}
        registered={repos}
        onClose={() => setImportOpen(false)}
        onAdded={() => {
          setImportOpen(false)
          void load()
        }}
      />
      <RepoDialog open={dialogOpen} repo={editing} onClose={() => setDialogOpen(false)} onSaved={load} />
      <AddBranchDialog repo={branchOf} registered={repos} onClose={() => setBranchOf(null)} onSaved={load} />
    </div>
  )
}
