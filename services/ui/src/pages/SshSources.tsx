import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { FolderOpen, Plus, Server, Unlink, Upload } from 'lucide-react'
import { api, type SshFile, type SshSource } from '../lib/api'
import { useAppStore } from '../store'
import AddFromCatalogDialog from '../components/catalog/AddFromCatalogDialog'
import RestrictedIcon from '../components/catalog/RestrictedIcon'
import StatusBadge from '../components/catalog/StatusBadge'
import { canSelectResources, canSelectRestricted } from '../lib/roles'
import { useOrgRole, useProjectRole } from '../lib/useRoles'
import {
  Button, Dialog, DialogFooter, Input, Table, Tbody, Td, Textarea, Th, Thead, Tr,
  useConfirm, useToast,
} from '../components/ui'

export default function SshSources() {
  const toast = useToast()
  const confirm = useConfirm()
  const orgRole = useOrgRole()
  const projectRole = useProjectRole()
  const projectId = useAppStore((s) => s.activeProjectId)
  const canWrite = orgRole === 'owner'
  const canSelect = canSelectResources(projectRole) && projectId != null
  const [sources, setSources] = useState<SshSource[]>([])
  const [catalog, setCatalog] = useState<SshSource[]>([])
  const [loading, setLoading] = useState(true)
  const [addOpen, setAddOpen] = useState(false)
  const [browse, setBrowse] = useState<{ source: SshSource; files: SshFile[] } | null>(null)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [uploadPath, setUploadPath] = useState('')
  const [uploadContent, setUploadContent] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.sshSources.list()
      setSources(res.sources)
    } catch (e) {
      toast.error(String(e))
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => {
    void load()
  }, [load])

  const openAdd = async () => {
    try {
      setCatalog((await api.catalog.folders.list()).sources)
      setAddOpen(true)
    } catch (e) {
      toast.error(String(e))
    }
  }

  const options = useMemo(
    () => catalog.map((s) => ({ id: s.id, name: s.name, detail: `${s.machine_name}:${s.root_path}`, restricted: s.restricted })),
    [catalog]
  )
  const selectedIds = useMemo(() => new Set(sources.map((s) => s.id)), [sources])

  const refreshBrowse = async (s: SshSource) => {
    const res = await api.sshSources.files(s.id, { recursive: true })
    setBrowse({ source: s, files: res.files })
  }

  const openBrowse = async (s: SshSource) => {
    try {
      await refreshBrowse(s)
    } catch (e) {
      toast.error(String(e))
    }
  }

  const closeBrowse = () => {
    setBrowse(null)
    setUploadOpen(false)
    setUploadPath('')
    setUploadContent('')
    setUploadError(null)
  }

  const submitUpload = async (e: FormEvent) => {
    e.preventDefault()
    if (!browse) return
    setUploading(true)
    setUploadError(null)
    try {
      await api.sshSources.writeFile(browse.source.id, uploadPath, uploadContent)
      toast.success('File written')
      setUploadOpen(false)
      setUploadPath('')
      setUploadContent('')
      await refreshBrowse(browse.source)
    } catch (err) {
      setUploadError(String(err))
    } finally {
      setUploading(false)
    }
  }

  const removeFromProject = async (s: SshSource) => {
    if (projectId == null) return
    const ok = await confirm({
      title: 'Remove from project',
      message: `Remove «${s.name}» from this project? It stays in the organization catalog.`,
      confirmLabel: 'Remove',
      onConfirm: () => api.projects.deselectResource(projectId, 'folders', s.id),
    })
    if (ok) {
      toast.success('Folder removed from the project')
      await load()
    }
  }

  return (
    <div className="p-4 sm:p-8">
      <div className="page-wide space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1>SSH Files</h1>
            <p className="text-sm text-muted">
              Remote folders this project can read, chosen from the organization catalog.
            </p>
          </div>
          {canSelect && (
            <div className="page-header-actions">
              <Button variant="primary" onClick={openAdd}>
                <Plus className="w-4 h-4" /> Add from catalog
              </Button>
            </div>
          )}
        </div>

        {loading ? (
          <p className="text-sm text-muted">Loading…</p>
        ) : sources.length === 0 ? (
          <div className="rounded border border-dashed border-border p-8 text-center">
            <Server className="w-6 h-6 mx-auto text-muted" />
            <p className="mt-2 text-sm text-muted">
              No SSH folders in this project yet. Add them from the organization catalog.
            </p>
          </div>
        ) : (
          <Table>
            <Thead>
              <Tr>
                <Th>Name</Th>
                <Th>Machine</Th>
                <Th>Root path</Th>
                <Th>Status</Th>
                <Th className="w-28" />
              </Tr>
            </Thead>
            <Tbody>
              {sources.map((s) => (
                <Tr key={s.id}>
                  <Td>
                    <span className="font-medium">{s.name}</span>
                    {s.restricted && <RestrictedIcon />}
                    {s.description && <span className="text-xs text-muted ml-2">{s.description}</span>}
                  </Td>
                  <Td className="text-sm">
                    {s.machine_name}
                    <span className="block text-xs text-muted font-mono">
                      {s.username}@{s.host}:{s.port}
                    </span>
                  </Td>
                  <Td className="text-sm font-mono">{s.root_path}</Td>
                  <Td>
                    <StatusBadge status={s.status} error={s.error_message} />
                  </Td>
                  <Td>
                    <div className="flex items-center gap-1 justify-end">
                      <Button size="sm" variant="ghost" onClick={() => openBrowse(s)} aria-label="Browse files" title="Browse files">
                        <FolderOpen className="w-3.5 h-3.5" />
                      </Button>
                      {canSelect && (
                        <Button size="sm" variant="ghost" onClick={() => removeFromProject(s)} aria-label="Remove from project" title="Remove from project">
                          <Unlink className="w-3.5 h-3.5" />
                        </Button>
                      )}
                    </div>
                  </Td>
                </Tr>
              ))}
            </Tbody>
          </Table>
        )}
      </div>

      {projectId != null && (
        <AddFromCatalogDialog
          open={addOpen}
          title="Add SSH folders"
          kind="folders"
          projectId={projectId}
          options={options}
          selectedIds={selectedIds}
          allowRestricted={canSelectRestricted(orgRole)}
          onClose={() => setAddOpen(false)}
          onAdded={load}
        />
      )}

      {browse && (
        <Dialog
          open
          onOpenChange={(o) => !o && closeBrowse()}
          title={`Files — ${browse.source.name}`}
          description={`${browse.source.root_path} (filtered by the folder globs)`}
        >
          {browse.files.length === 0 ? (
            <p className="text-sm text-muted">No matching files.</p>
          ) : (
            <div className="max-h-96 overflow-y-auto">
              <Table>
                <Thead>
                  <Tr>
                    <Th>Path</Th>
                    <Th className="w-24">Size</Th>
                  </Tr>
                </Thead>
                <Tbody>
                  {browse.files.map((f) => (
                    <Tr key={f.path}>
                      <Td className="font-mono text-xs">{f.path}</Td>
                      <Td className="text-xs text-muted">{f.size ?? '—'}</Td>
                    </Tr>
                  ))}
                </Tbody>
              </Table>
            </div>
          )}

          {canWrite && (
            <div className="mt-4 border-t border-border pt-4">
              {uploadOpen ? (
                <form onSubmit={submitUpload} className="space-y-3">
                  <Input
                    label="Path"
                    value={uploadPath}
                    onChange={(e) => setUploadPath(e.target.value)}
                    placeholder="relative/path/to/file.conf"
                    required
                  />
                  <Textarea
                    label="Content"
                    value={uploadContent}
                    onChange={(e) => setUploadContent(e.target.value)}
                    rows={6}
                    required
                  />
                  {uploadError && <p className="text-sm text-danger">{uploadError}</p>}
                  <div className="flex items-center gap-2">
                    <Button type="button" variant="ghost" onClick={() => setUploadOpen(false)}>
                      Cancel
                    </Button>
                    <Button type="submit" variant="primary" loading={uploading}>
                      Write file
                    </Button>
                  </div>
                </form>
              ) : (
                <Button size="sm" variant="ghost" onClick={() => setUploadOpen(true)}>
                  <Upload className="w-3.5 h-3.5" /> Upload file
                </Button>
              )}
            </div>
          )}

          <DialogFooter>
            <Button type="button" variant="ghost" onClick={closeBrowse}>
              Close
            </Button>
          </DialogFooter>
        </Dialog>
      )}
    </div>
  )
}
