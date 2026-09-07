import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { Plus, Database, RefreshCw, Pencil, Trash2 } from 'lucide-react'
import { api, type DbConnection, type DbConnectionRequest, type DbEngine } from '../lib/api'
import { Badge, Banner, Button, Card, Dialog, DialogFooter, Input, Select, Table, Tbody, Td, Textarea, Th, Thead, Tr, useConfirm, useToast } from '../components/ui'

const ENGINE_OPTIONS: { value: DbEngine; label: string }[] = [
  { value: 'postgresql', label: 'PostgreSQL' },
  { value: 'mysql', label: 'MySQL' },
  { value: 'mariadb', label: 'MariaDB' },
  { value: 'sqlite', label: 'SQLite' },
]

const DOCKER_HOST_ALIAS = 'host.docker.internal'
const LOOPBACK_HOSTS = ['localhost', '127.0.0.1', '::1']

const EMPTY_FORM: DbConnectionRequest = {
  name: '',
  engine: 'postgresql',
  host: '',
  port: undefined,
  database_name: '',
  username: '',
  password: '',
  description: '',
  ssh_enabled: false,
  ssh_host: '',
  ssh_port: 22,
  ssh_username: '',
  ssh_auth_method: 'password',
  ssh_password: '',
  ssh_private_key: '',
}

const SSH_AUTH_OPTIONS = [
  { value: 'password', label: 'Password' },
  { value: 'key', label: 'Private key' },
]

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
  const toast = useToast()

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
        ssh_enabled: editing.ssh_enabled ?? false,
        ssh_host: editing.ssh_host ?? '',
        ssh_port: editing.ssh_port ?? 22,
        ssh_username: editing.ssh_username ?? '',
        ssh_auth_method: editing.ssh_auth_method ?? 'password',
        ssh_password: '',
        ssh_private_key: '',
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
    const sshOn = !isSqlite && !!form.ssh_enabled
    const payload: DbConnectionRequest = {
      ...form,
      host: isSqlite ? undefined : form.host || undefined,
      port: isSqlite ? undefined : form.port || undefined,
      username: isSqlite ? undefined : form.username || undefined,
      password: isSqlite ? undefined : form.password || undefined,
      database_name: form.database_name || undefined,
      description: form.description || undefined,
      ssh_enabled: sshOn,
      ssh_host: sshOn ? form.ssh_host || undefined : undefined,
      ssh_port: sshOn ? form.ssh_port || 22 : undefined,
      ssh_username: sshOn ? form.ssh_username || undefined : undefined,
      ssh_auth_method: sshOn ? form.ssh_auth_method : undefined,
      ssh_password:
        sshOn && form.ssh_auth_method === 'password' ? form.ssh_password || undefined : undefined,
      ssh_private_key:
        sshOn && form.ssh_auth_method === 'key' ? form.ssh_private_key || undefined : undefined,
    }
    try {
      if (editing) await api.datasources.update(editing.id, payload)
      else await api.datasources.create(payload)
      toast.success(editing ? `Connection "${form.name}" updated` : `Connection "${form.name}" created`)
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
          placeholder="e.g. billing-prod"
          required
        />
        <Select
          label="Engine"
          value={form.engine}
          onValueChange={(v) => set({ engine: v as DbEngine })}
          options={ENGINE_OPTIONS}
        />
        {!isSqlite && (
          <div>
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
            {LOOPBACK_HOSTS.includes((form.host ?? '').trim().toLowerCase()) && (
              <p className="text-xs text-muted mt-1">
                The server runs in a container, so <code>{form.host}</code> points to the
                container itself. For a database on this machine (or published by another
                container) use{' '}
                <button
                  type="button"
                  className="text-accent hover:underline"
                  onClick={() => set({ host: DOCKER_HOST_ALIAS })}
                >
                  {DOCKER_HOST_ALIAS}
                </button>
                .
              </p>
            )}
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
        {!isSqlite && (
          <div className="rounded-md border border-border p-3 space-y-3">
            <label className="flex items-center gap-2 text-sm font-medium">
              <input
                type="checkbox"
                className="accent-accent"
                checked={!!form.ssh_enabled}
                onChange={(e) => set({ ssh_enabled: e.target.checked })}
              />
              Connect through an SSH bastion
            </label>
            {form.ssh_enabled && (
              <div className="space-y-3">
                <p className="text-xs text-muted">
                  Host and port above are the database as seen from the bastion.
                </p>
                <div className="grid grid-cols-3 gap-2">
                  <div className="col-span-2">
                    <Input
                      label="SSH host"
                      value={form.ssh_host ?? ''}
                      onChange={(e) => set({ ssh_host: e.target.value })}
                      placeholder="bastion.internal"
                      required
                    />
                  </div>
                  <Input
                    label="SSH port"
                    type="number"
                    value={form.ssh_port ?? 22}
                    onChange={(e) => set({ ssh_port: e.target.value ? Number(e.target.value) : 22 })}
                    placeholder="22"
                  />
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <Input
                    label="SSH username"
                    value={form.ssh_username ?? ''}
                    onChange={(e) => set({ ssh_username: e.target.value })}
                    autoComplete="off"
                    required
                  />
                  <Select
                    label="Authentication"
                    value={form.ssh_auth_method ?? 'password'}
                    onValueChange={(v) => set({ ssh_auth_method: v as 'key' | 'password' })}
                    options={SSH_AUTH_OPTIONS}
                  />
                </div>
                {form.ssh_auth_method === 'key' ? (
                  <Textarea
                    label="Private key"
                    value={form.ssh_private_key ?? ''}
                    onChange={(e) => set({ ssh_private_key: e.target.value })}
                    placeholder={
                      editing?.has_ssh_secret ? '(unchanged)' : '-----BEGIN OPENSSH PRIVATE KEY-----'
                    }
                    rows={4}
                    autoComplete="off"
                  />
                ) : (
                  <Input
                    label="SSH password"
                    type="password"
                    value={form.ssh_password ?? ''}
                    onChange={(e) => set({ ssh_password: e.target.value })}
                    placeholder={editing?.has_ssh_secret ? '(unchanged)' : ''}
                    autoComplete="new-password"
                  />
                )}
              </div>
            )}
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
  const [suggestions, setSuggestions] = useState<Record<number, string>>({})
  const confirm = useConfirm()
  const toast = useToast()

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
      const result = await api.datasources.test(conn.id)
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
      await api.datasources.update(conn.id, {
        name: conn.name,
        engine: conn.engine,
        host,
        port: conn.port,
        database_name: conn.database_name ?? undefined,
        username: conn.username ?? undefined,
        password: '', // keep the stored password
        description: conn.description ?? undefined,
      })
      setSuggestions((s) => {
        const next = { ...s }
        delete next[conn.id]
        return next
      })
      const result = await api.datasources.test(conn.id)
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
    const ok = await confirm({
      title: 'Delete connection',
      message: `Delete connection '${conn.name}'? Annotations and query log are removed too.`,
      confirmLabel: 'Delete',
      danger: true,
      onConfirm: () => api.datasources.delete(conn.id),
    })
    if (!ok) return
    toast.success(`Connection "${conn.name}" deleted`)
    await load()
  }

  return (
    <div className="p-4 sm:p-8">
      <div className="page-wide">
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

        {error && <Banner variant="danger" className="mb-4 break-words">{error}</Banner>}

        {loading ? (
          <p className="text-muted text-sm">Loading...</p>
        ) : connections.length === 0 ? (
          <div className="border border-dashed border-border p-8 text-center text-sm text-muted">
            <Database className="w-6 h-6 mx-auto mb-2 opacity-50" />
            No database connections yet. Add one to give agents schema context and
            read-only query access.
          </div>
        ) : (
          <Card className="overflow-x-auto max-w-5xl">
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
                      {c.status === 'error' && suggestions[c.id] && (
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
          </Card>
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
