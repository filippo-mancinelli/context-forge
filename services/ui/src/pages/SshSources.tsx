import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Plus, Server, RefreshCw, Pencil, Trash2, FolderOpen, Upload } from 'lucide-react'
import { api, type SshSource, type SshFile } from '../lib/api'
import { useAppStore } from '../store'
import SshSourceDialog from '../components/SshSourceDialog'
import {
  Badge, Button, Dialog, DialogFooter, Input, Table, Tbody, Td, Textarea, Th, Thead, Tr,
  useConfirm, useToast,
} from '../components/ui'

function statusBadge(status: SshSource['status']) {
  if (status === 'ok') return <Badge variant="success">Reachable</Badge>
  if (status === 'error') return <Badge variant="danger">Error</Badge>
  return <Badge variant="muted">Unknown</Badge>
}

export default function SshSources() {
  const toast = useToast()
  const confirm = useConfirm()
  const role = useAppStore((s) => s.organizations.find((o) => o.id === s.activeOrgId)?.role)
  const canWrite = role === 'owner'
  const [sources, setSources] = useState<SshSource[]>([])
  const [loading, setLoading] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<SshSource | null>(null)
  const [testingId, setTestingId] = useState<number | null>(null)
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

  const openCreate = () => {
    setEditing(null)
    setDialogOpen(true)
  }

  const openEdit = (s: SshSource) => {
    setEditing(s)
    setDialogOpen(true)
  }

  const test = async (s: SshSource) => {
    setTestingId(s.id)
    try {
      const res = await api.sshSources.test(s.id)
      if (res.status === 'ok') toast.success(`${s.name} reachable`)
      else toast.error(res.error ?? 'Connection failed')
      await load()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setTestingId(null)
    }
  }

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

  const remove = (s: SshSource) => {
    confirm({
      title: 'Delete SSH source',
      message: `Delete «${s.name}»? Tools will no longer be able to read from ${s.host}.`,
      confirmLabel: 'Delete',
      danger: true,
      onConfirm: async () => {
        await api.sshSources.delete(s.id)
        await load()
        toast.success('Source deleted')
      },
    })
  }

  return (
    <div className="p-4 sm:p-8">
      <div className="page-wide space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1>SSH Files</h1>
            <p className="text-sm text-muted">
              Read configuration files on remote Linux hosts, confined to each source root.
            </p>
          </div>
          <div className="page-header-actions">
            <Button variant="primary" onClick={openCreate}>
              <Plus className="w-4 h-4" /> Add source
            </Button>
          </div>
        </div>

        {loading ? (
          <p className="text-sm text-muted">Loading…</p>
        ) : sources.length === 0 ? (
          <div className="rounded border border-dashed border-border p-8 text-center">
            <Server className="w-6 h-6 mx-auto text-muted" />
            <p className="mt-2 text-sm text-muted">No SSH sources yet. Add one to read remote files.</p>
          </div>
        ) : (
          <Table>
            <Thead>
              <Tr>
                <Th>Name</Th>
                <Th>Host</Th>
                <Th>Root path</Th>
                <Th>Status</Th>
                <Th className="w-40" />
              </Tr>
            </Thead>
            <Tbody>
              {sources.map((s) => (
                <Tr key={s.id}>
                  <Td>
                    <span className="font-medium">{s.name}</span>
                    {s.description && <span className="text-xs text-muted ml-2">{s.description}</span>}
                  </Td>
                  <Td className="text-sm">
                    {s.username}@{s.host}:{s.port}
                  </Td>
                  <Td className="text-sm font-mono">{s.root_path}</Td>
                  <Td>{statusBadge(s.status)}</Td>
                  <Td>
                    <div className="flex items-center gap-1 justify-end">
                      <Button size="sm" variant="ghost" onClick={() => openBrowse(s)} aria-label="Browse files" title="Browse files">
                        <FolderOpen className="w-3.5 h-3.5" />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => test(s)}
                        loading={testingId === s.id}
                        aria-label="Test connection"
                        title="Test connection"
                      >
                        <RefreshCw className="w-3.5 h-3.5" />
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => openEdit(s)} aria-label="Edit" title="Edit">
                        <Pencil className="w-3.5 h-3.5" />
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => remove(s)} aria-label="Delete" title="Delete">
                        <Trash2 className="w-3.5 h-3.5" />
                      </Button>
                    </div>
                  </Td>
                </Tr>
              ))}
            </Tbody>
          </Table>
        )}
      </div>

      <SshSourceDialog
        open={dialogOpen}
        source={editing}
        onClose={() => setDialogOpen(false)}
        onSaved={load}
      />

      {browse && (
        <Dialog
          open
          onOpenChange={(o) => !o && closeBrowse()}
          title={`Files — ${browse.source.name}`}
          description={`${browse.source.root_path} (filtered by the source globs)`}
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
