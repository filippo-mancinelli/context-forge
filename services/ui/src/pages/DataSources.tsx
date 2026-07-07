import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { Plus, Database, RefreshCw, Pencil, Trash2 } from 'lucide-react'
import { api, type DbConnection, type DbConnectionRequest, type DbEngine } from '../lib/api'
import { Badge, Button, Dialog, DialogFooter, Input, Select, Table, Tbody, Td, Th, Thead, Tr } from '../components/ui'

const ENGINE_OPTIONS: { value: DbEngine; label: string }[] = [
  { value: 'postgresql', label: 'PostgreSQL' },
  { value: 'mysql', label: 'MySQL' },
  { value: 'mariadb', label: 'MariaDB' },
  { value: 'sqlite', label: 'SQLite' },
]

const EMPTY_FORM: DbConnectionRequest = {
  name: '',
  engine: 'postgresql',
  host: '',
  port: undefined,
  database_name: '',
  username: '',
  password: '',
  description: '',
}

function statusVariant(status: DbConnection['status']) {
  if (status === 'ok') return 'success' as const
  if (status === 'error') return 'danger' as const
  return 'default' as const
}

function ConnectionDialog({
  open,
  onOpenChange,
  editing,
  onSaved,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  editing: DbConnection | null
  onSaved: () => void
}) {
  const [form, setForm] = useState<DbConnectionRequest>(EMPTY_FORM)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setError(null)
    if (editing) {
      setForm({
        name: editing.name,
        engine: editing.engine,
        host: editing.host ?? '',
        port: editing.port,
        database_name: editing.database_name ?? '',
        username: editing.username ?? '',
        password: '',
        description: editing.description ?? '',
      })
    } else {
      setForm(EMPTY_FORM)
    }
  }, [open, editing])

  const isSqlite = form.engine === 'sqlite'
  const set = (patch: Partial<DbConnectionRequest>) => setForm((f) => ({ ...f, ...patch }))

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    const payload: DbConnectionRequest = {
      ...form,
      host: isSqlite ? undefined : form.host || undefined,
      port: isSqlite ? undefined : form.port || undefined,
      username: isSqlite ? undefined : form.username || undefined,
      password: isSqlite ? undefined : form.password || undefined,
      database_name: form.database_name || undefined,
      description: form.description || undefined,
    }
    try {
      if (editing) await api.datasources.update(editing.id, payload)
      else await api.datasources.create(payload)
      onOpenChange(false)
      onSaved()
    } catch (err) {
      setError(String(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={editing ? `Edit ${editing.name}` : 'New database connection'}
      description="Credentials should belong to a read-only database user."
    >
      <form onSubmit={submit} className="space-y-3">
        {error && <p className="text-xs text-danger break-words">{error}</p>}
        <Input
          label="Name"
          value={form.name}
          onChange={(e) => set({ name: e.target.value })}
          placeholder="e.g. askmechat-prod"
          required
        />
        <Select
          label="Engine"
          value={form.engine}
          onValueChange={(v) => set({ engine: v as DbEngine })}
          options={ENGINE_OPTIONS}
        />
        {!isSqlite && (
          <div className="grid grid-cols-3 gap-2">
            <div className="col-span-2">
              <Input
                label="Host"
                value={form.host ?? ''}
                onChange={(e) => set({ host: e.target.value })}
                placeholder="db.internal"
                required
              />
            </div>
            <Input
              label="Port"
              type="number"
              value={form.port ?? ''}
              onChange={(e) => set({ port: e.target.value ? Number(e.target.value) : undefined })}
              placeholder={form.engine === 'postgresql' ? '5432' : '3306'}
            />
          </div>
        )}
        <Input
          label={isSqlite ? 'Database file path' : 'Database'}
          value={form.database_name ?? ''}
          onChange={(e) => set({ database_name: e.target.value })}
          placeholder={isSqlite ? '/data/app.db (path inside the container)' : 'database name'}
          required
        />
        {!isSqlite && (
          <div className="grid grid-cols-2 gap-2">
            <Input
              label="Username"
              value={form.username ?? ''}
              onChange={(e) => set({ username: e.target.value })}
              autoComplete="off"
            />
            <Input
              label="Password"
              type="password"
              value={form.password ?? ''}
              onChange={(e) => set({ password: e.target.value })}
              placeholder={editing?.has_password ? '(unchanged)' : ''}
              autoComplete="new-password"
            />
          </div>
        )}
        <Input
          label="Description"
          value={form.description ?? ''}
          onChange={(e) => set({ description: e.target.value })}
          placeholder="What lives in this database (shown to agents)"
        />
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" loading={saving}>
            {editing ? 'Save' : 'Create'}
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  )
}

export default function DataSources() {
  const [connections, setConnections] = useState<DbConnection[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<DbConnection | null>(null)
  const [testingId, setTestingId] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      const data = await api.datasources.list()
      setConnections(data.connections)
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const test = async (conn: DbConnection) => {
    setTestingId(conn.id)
    try {
      await api.datasources.test(conn.id)
      await load()
    } catch (e) {
      setError(String(e))
    } finally {
      setTestingId(null)
    }
  }

  const remove = async (conn: DbConnection) => {
    if (!window.confirm(`Delete connection '${conn.name}'? Annotations and query log are removed too.`)) return
    try {
      await api.datasources.delete(conn.id)
      await load()
    } catch (e) {
      setError(String(e))
    }
  }

  return (
    <div className="p-4 sm:p-8">
      <div className="page-content">
        <div className="flex items-start justify-between gap-4 mb-6">
          <div>
            <h1>Data Sources</h1>
            <p className="text-muted text-sm">
              External databases exposed to agents via the db_* MCP tools (read-only).
            </p>
          </div>
          <Button
            variant="primary"
            onClick={() => {
              setEditing(null)
              setDialogOpen(true)
            }}
          >
            <Plus className="w-3.5 h-3.5" /> Add connection
          </Button>
        </div>

        {error && (
          <div
            style={{ border: '1px solid var(--danger)', color: 'var(--danger)' }}
            className="text-sm p-3 mb-4 bg-[#fef2f2] break-words"
          >
            {error}
          </div>
        )}

        {loading ? (
          <p className="text-muted text-sm">Loading...</p>
        ) : connections.length === 0 ? (
          <div
            style={{ border: '1px dashed var(--border)' }}
            className="p-8 text-center text-sm text-muted"
          >
            <Database className="w-6 h-6 mx-auto mb-2 opacity-50" />
            No database connections yet. Add one to give agents schema context and
            read-only query access.
          </div>
        ) : (
          <div style={{ border: '1px solid var(--border)' }} className="overflow-x-auto">
            <Table>
              <Thead>
                <Tr>
                  <Th>Name</Th>
                  <Th>Engine</Th>
                  <Th>Target</Th>
                  <Th>Status</Th>
                  <Th>Annotations</Th>
                  <Th>Actions</Th>
                </Tr>
              </Thead>
              <Tbody>
                {connections.map((c) => (
                  <Tr key={c.id}>
                    <Td>
                      <Link to={`/datasources/${c.id}`} className="text-accent hover:underline font-medium">
                        {c.name}
                      </Link>
                      {c.description && (
                        <p className="text-xs text-muted mt-0.5 max-w-xs truncate">{c.description}</p>
                      )}
                    </Td>
                    <Td className="text-xs font-mono">{c.engine}</Td>
                    <Td className="text-xs text-muted font-mono">
                      {c.engine === 'sqlite'
                        ? c.database_name
                        : `${c.host ?? '?'}${c.port ? `:${c.port}` : ''}/${c.database_name ?? ''}`}
                    </Td>
                    <Td>
                      <Badge variant={statusVariant(c.status)}>{c.status}</Badge>
                      {c.status === 'error' && c.error_message && (
                        <p className="text-xs text-danger mt-1 max-w-xs truncate" title={c.error_message}>
                          {c.error_message}
                        </p>
                      )}
                    </Td>
                    <Td className="text-xs text-muted">{c.annotation_count ?? 0}</Td>
                    <Td>
                      <div className="flex items-center gap-1">
                        <Button
                          size="sm"
                          variant="ghost"
                          loading={testingId === c.id}
                          onClick={() => test(c)}
                          title="Test connection"
                        >
                          <RefreshCw className="w-3.5 h-3.5" /> Test
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            setEditing(c)
                            setDialogOpen(true)
                          }}
                          title="Edit"
                        >
                          <Pencil className="w-3.5 h-3.5" />
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => remove(c)} title="Delete">
                          <Trash2 className="w-3.5 h-3.5" />
                        </Button>
                      </div>
                    </Td>
                  </Tr>
                ))}
              </Tbody>
            </Table>
          </div>
        )}

        <ConnectionDialog
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          editing={editing}
          onSaved={load}
        />
      </div>
    </div>
  )
}
