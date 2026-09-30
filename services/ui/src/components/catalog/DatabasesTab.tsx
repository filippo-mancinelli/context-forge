import { useCallback, useEffect, useState } from 'react'
import { Database, Pencil, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { api, type CatalogProjectRef, type DbConnection, type Machine } from '../../lib/api'
import { Button, Card, Table, Tbody, Td, Th, Thead, Tr, useConfirm, useToast } from '../ui'
import DatabaseDialog from './DatabaseDialog'
import RestrictedIcon from './RestrictedIcon'
import StatusBadge from './StatusBadge'

function target(c: DbConnection): string {
  if (c.engine === 'sqlite') return c.database_name ?? ''
  return `${c.host ?? '?'}${c.port ? `:${c.port}` : ''}/${c.database_name ?? ''}`
}

export default function DatabasesTab({ canManage }: { canManage: boolean }) {
  const toast = useToast()
  const confirm = useConfirm()
  const [connections, setConnections] = useState<DbConnection[]>([])
  const [machines, setMachines] = useState<Machine[]>([])
  const [loading, setLoading] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<DbConnection | null>(null)
  const [testingId, setTestingId] = useState<number | null>(null)
  const [suggestions, setSuggestions] = useState<Record<number, string>>({})

  const load = useCallback(async () => {
    try {
      const [d, m] = await Promise.all([api.catalog.databases.list(), api.catalog.machines.list()])
      setConnections(d.connections)
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

  const test = async (conn: DbConnection) => {
    setTestingId(conn.id)
    try {
      const result = await api.catalog.databases.test(conn.id)
      setSuggestions((s) => {
        const next = { ...s }
        if (result.suggested_host) next[conn.id] = result.suggested_host
        else delete next[conn.id]
        return next
      })
      if (result.status === 'ok') toast.success(`Connection "${conn.name}" is working`)
      else toast.error(result.error ?? `Connection "${conn.name}" failed`)
      await load()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setTestingId(null)
    }
  }

  const applySuggestedHost = async (conn: DbConnection) => {
    const host = suggestions[conn.id]
    if (!host) return
    setTestingId(conn.id)
    try {
      await api.catalog.databases.update(conn.id, {
        name: conn.name,
        engine: conn.engine,
        host,
        port: conn.port,
        database_name: conn.database_name ?? undefined,
        username: conn.username ?? undefined,
        password: '', // mantiene la password memorizzata
        description: conn.description ?? undefined,
        ssh_machine_id: conn.ssh_machine_id ?? null,
        restricted: conn.restricted,
      })
      setSuggestions((s) => {
        const next = { ...s }
        delete next[conn.id]
        return next
      })
      const result = await api.catalog.databases.test(conn.id)
      if (result.status === 'ok') toast.success(`Connection "${conn.name}" fixed and working`)
      else toast.error(result.error ?? 'Connection still failing')
      await load()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setTestingId(null)
    }
  }

  const remove = async (conn: DbConnection) => {
    let projects: CatalogProjectRef[] = []
    try {
      projects = (await api.catalog.databases.projects(conn.id)).projects
    } catch (e) {
      toast.error(String(e))
      return
    }
    const ok = await confirm({
      title: 'Delete connection',
      message: (
        <>
          <p>Delete «{conn.name}»? Annotations and query log are deleted too.</p>
          {projects.length > 0 && (
            <>
              <p className="mt-2">It is used by these projects and will be removed from them:</p>
              <ul className="list-disc pl-5 mt-2">
                {projects.map((p) => (
                  <li key={p.id}>{p.name}</li>
                ))}
              </ul>
            </>
          )}
        </>
      ),
      confirmLabel: 'Delete',
      danger: true,
      onConfirm: () => api.catalog.databases.delete(conn.id),
    })
    if (ok) {
      toast.success(`Connection "${conn.name}" deleted`)
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
            <Plus className="w-4 h-4" /> Add connection
          </Button>
        </div>
      )}

      {loading ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : connections.length === 0 ? (
        <div className="rounded border border-dashed border-border p-8 text-center">
          <Database className="w-6 h-6 mx-auto text-muted" />
          <p className="mt-2 text-sm text-muted">No database connections registered yet.</p>
        </div>
      ) : (
        <Card className="overflow-x-auto">
          <Table>
            <Thead>
              <Tr>
                <Th>Name</Th>
                <Th>Engine</Th>
                <Th>Target</Th>
                <Th>Access</Th>
                <Th>Projects</Th>
                <Th>Status</Th>
                {canManage && <Th className="w-32" />}
              </Tr>
            </Thead>
            <Tbody>
              {connections.map((c) => (
                <Tr key={c.id}>
                  <Td>
                    <span className="font-medium">{c.name}</span>
                    {c.restricted && <RestrictedIcon />}
                    {c.description && <p className="text-xs text-muted mt-0.5 max-w-xs truncate">{c.description}</p>}
                  </Td>
                  <Td className="text-xs font-mono">{c.engine}</Td>
                  <Td className="text-xs text-muted font-mono">{target(c)}</Td>
                  <Td className="text-xs">{c.ssh_machine_name ? `via ${c.ssh_machine_name}` : 'direct'}</Td>
                  <Td className="text-xs text-muted">{c.project_count ?? 0}</Td>
                  <Td>
                    <StatusBadge status={c.status} error={c.error_message} />
                    {c.status === 'error' && suggestions[c.id] && canManage && (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="mt-1"
                        loading={testingId === c.id}
                        onClick={() => applySuggestedHost(c)}
                      >
                        Use {suggestions[c.id]}
                      </Button>
                    )}
                  </Td>
                  {canManage && (
                    <Td>
                      <div className="flex items-center gap-1 justify-end">
                        <Button size="sm" variant="ghost" onClick={() => test(c)} loading={testingId === c.id} title="Test connection" aria-label="Test connection">
                          <RefreshCw className="w-3.5 h-3.5" />
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            setEditing(c)
                            setDialogOpen(true)
                          }}
                          title="Edit"
                          aria-label="Edit"
                        >
                          <Pencil className="w-3.5 h-3.5" />
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => remove(c)} title="Delete" aria-label="Delete">
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

      <DatabaseDialog
        open={dialogOpen}
        editing={editing}
        machines={machines}
        onOpenChange={setDialogOpen}
        onSaved={load}
      />
    </div>
  )
}
