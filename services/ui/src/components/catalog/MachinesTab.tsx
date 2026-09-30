import { useCallback, useEffect, useState } from 'react'
import { Pencil, Plus, RefreshCw, Server, Trash2 } from 'lucide-react'
import { api, type Machine } from '../../lib/api'
import { Button, Card, Table, Tbody, Td, Th, Thead, Tr, useConfirm, useToast } from '../ui'
import MachineDialog from './MachineDialog'
import StatusBadge from './StatusBadge'

export default function MachinesTab({ canManage }: { canManage: boolean }) {
  const toast = useToast()
  const confirm = useConfirm()
  const [machines, setMachines] = useState<Machine[]>([])
  const [loading, setLoading] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<Machine | null>(null)
  const [testingId, setTestingId] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      setMachines((await api.catalog.machines.list()).machines)
    } catch (e) {
      toast.error(String(e))
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => {
    void load()
  }, [load])

  const test = async (m: Machine) => {
    setTestingId(m.id)
    try {
      const res = await api.catalog.machines.test(m.id)
      if (res.status === 'ok') toast.success(`${m.name} reachable`)
      else toast.error(res.error ?? 'Connection failed')
      await load()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setTestingId(null)
    }
  }

  const remove = async (m: Machine) => {
    const ok = await confirm({
      title: 'Delete machine',
      message: `Delete «${m.name}»? Its credential is deleted too.`,
      confirmLabel: 'Delete',
      danger: true,
      onConfirm: () => api.catalog.machines.delete(m.id),
    })
    if (ok) {
      toast.success('Machine deleted')
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
            <Plus className="w-4 h-4" /> Add machine
          </Button>
        </div>
      )}

      {loading ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : machines.length === 0 ? (
        <div className="rounded border border-dashed border-border p-8 text-center">
          <Server className="w-6 h-6 mx-auto text-muted" />
          <p className="mt-2 text-sm text-muted">No machines registered yet.</p>
        </div>
      ) : (
        <Card className="overflow-x-auto">
          <Table>
            <Thead>
              <Tr>
                <Th>Name</Th>
                <Th>Address</Th>
                <Th>Used by</Th>
                <Th>Status</Th>
                {canManage && <Th className="w-32" />}
              </Tr>
            </Thead>
            <Tbody>
              {machines.map((m) => {
                const inUse = m.folder_count + m.database_count > 0
                return (
                  <Tr key={m.id}>
                    <Td>
                      <span className="font-medium">{m.name}</span>
                      {m.description && <p className="text-xs text-muted mt-0.5 max-w-xs truncate">{m.description}</p>}
                    </Td>
                    <Td className="text-xs font-mono">{m.username}@{m.host}:{m.port}</Td>
                    <Td className="text-xs text-muted">
                      {m.folder_count} folders · {m.database_count} databases
                    </Td>
                    <Td>
                      <StatusBadge status={m.status} error={m.error_message} />
                    </Td>
                    {canManage && (
                      <Td>
                        <div className="flex items-center gap-1 justify-end">
                          <Button size="sm" variant="ghost" onClick={() => test(m)} loading={testingId === m.id} title="Test connection" aria-label="Test connection">
                            <RefreshCw className="w-3.5 h-3.5" />
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => {
                              setEditing(m)
                              setDialogOpen(true)
                            }}
                            title="Edit"
                            aria-label="Edit"
                          >
                            <Pencil className="w-3.5 h-3.5" />
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => remove(m)}
                            disabled={inUse}
                            title={inUse ? 'In use by folders or databases' : 'Delete'}
                            aria-label="Delete"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </Button>
                        </div>
                      </Td>
                    )}
                  </Tr>
                )
              })}
            </Tbody>
          </Table>
        </Card>
      )}

      <MachineDialog open={dialogOpen} machine={editing} onClose={() => setDialogOpen(false)} onSaved={load} />
    </div>
  )
}
