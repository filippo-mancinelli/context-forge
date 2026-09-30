import { useCallback, useEffect, useState } from 'react'
import { FolderOpen, Pencil, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { api, type CatalogProjectRef, type Machine, type SshSource } from '../../lib/api'
import { Button, Card, Table, Tbody, Td, Th, Thead, Tr, useConfirm, useToast } from '../ui'
import FolderDialog from './FolderDialog'
import RestrictedIcon from './RestrictedIcon'
import StatusBadge from './StatusBadge'

export default function FoldersTab({ canManage }: { canManage: boolean }) {
  const toast = useToast()
  const confirm = useConfirm()
  const [folders, setFolders] = useState<SshSource[]>([])
  const [machines, setMachines] = useState<Machine[]>([])
  const [loading, setLoading] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<SshSource | null>(null)
  const [testingId, setTestingId] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      const [f, m] = await Promise.all([api.catalog.folders.list(), api.catalog.machines.list()])
      setFolders(f.sources)
      setMachines(m.machines)
    } catch (e) {
      toast.error(String(e))
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => {
    void load()
  }, [load])

  const test = async (f: SshSource) => {
    setTestingId(f.id)
    try {
      const res = await api.catalog.folders.test(f.id)
      if (res.status === 'ok') toast.success(`${f.name} reachable`)
      else toast.error(res.error ?? 'Connection failed')
      await load()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setTestingId(null)
    }
  }

  const remove = async (f: SshSource) => {
    let projects: CatalogProjectRef[] = []
    try {
      projects = (await api.catalog.folders.projects(f.id)).projects
    } catch (e) {
      toast.error(String(e))
      return
    }
    const ok = await confirm({
      title: 'Delete folder',
      message: projects.length ? (
        <>
          <p>«{f.name}» is used by these projects and will be removed from them:</p>
          <ul className="list-disc pl-5 mt-2">
            {projects.map((p) => (
              <li key={p.id}>{p.name}</li>
            ))}
          </ul>
        </>
      ) : (
        `Delete «${f.name}»?`
      ),
      confirmLabel: 'Delete',
      danger: true,
      onConfirm: () => api.catalog.folders.delete(f.id),
    })
    if (ok) {
      toast.success('Folder deleted')
      await load()
    }
  }

  return (
    <div className="space-y-4">
      {canManage && (
        <div className="flex justify-end">
          <Button
            variant="primary"
            onClick={() => {
              setEditing(null)
              setDialogOpen(true)
            }}
          >
            <Plus className="w-4 h-4" /> Add folder
          </Button>
        </div>
      )}

      {loading ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : folders.length === 0 ? (
        <div className="rounded border border-dashed border-border p-8 text-center">
          <FolderOpen className="w-6 h-6 mx-auto text-muted" />
          <p className="mt-2 text-sm text-muted">No folders registered yet.</p>
        </div>
      ) : (
        <Card className="overflow-x-auto">
          <Table>
            <Thead>
              <Tr>
                <Th>Name</Th>
                <Th>Machine</Th>
                <Th>Root path</Th>
                <Th>Projects</Th>
                <Th>Status</Th>
                {canManage && <Th className="w-32" />}
              </Tr>
            </Thead>
            <Tbody>
              {folders.map((f) => (
                <Tr key={f.id}>
                  <Td>
                    <span className="font-medium">{f.name}</span>
                    {f.restricted && <RestrictedIcon />}
                    {f.description && <p className="text-xs text-muted mt-0.5 max-w-xs truncate">{f.description}</p>}
                  </Td>
                  <Td className="text-sm">{f.machine_name}</Td>
                  <Td className="text-xs font-mono">{f.root_path}</Td>
                  <Td className="text-xs text-muted">{f.project_count ?? 0}</Td>
                  <Td>
                    <StatusBadge status={f.status} error={f.error_message} />
                  </Td>
                  {canManage && (
                    <Td>
                      <div className="flex items-center gap-1 justify-end">
                        <Button size="sm" variant="ghost" onClick={() => test(f)} loading={testingId === f.id} title="Test connection" aria-label="Test connection">
                          <RefreshCw className="w-3.5 h-3.5" />
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            setEditing(f)
                            setDialogOpen(true)
                          }}
                          title="Edit"
                          aria-label="Edit"
                        >
                          <Pencil className="w-3.5 h-3.5" />
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => remove(f)} title="Delete" aria-label="Delete">
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

      <FolderDialog
        open={dialogOpen}
        folder={editing}
        machines={machines}
        onClose={() => setDialogOpen(false)}
        onSaved={load}
      />
    </div>
  )
}
