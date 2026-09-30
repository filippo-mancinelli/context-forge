import { useEffect, useState, type FormEvent } from 'react'
import { api, type DbConnection, type DbConnectionRequest, type DbEngine, type Machine } from '../../lib/api'
import { Badge, Button, Dialog, DialogFooter, Input, Select, useToast } from '../ui'

const ENGINE_OPTIONS: { value: DbEngine; label: string }[] = [
  { value: 'postgresql', label: 'PostgreSQL' },
  { value: 'mysql', label: 'MySQL' },
  { value: 'mariadb', label: 'MariaDB' },
  { value: 'sqlite', label: 'SQLite' },
]

// Engines that cannot open a connection without a database: PostgreSQL needs it in the
// URL, and for SQLite the database is the file itself.
const DEFAULT_DATABASE_REQUIRED: DbEngine[] = ['postgresql', 'sqlite']

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
  ssh_machine_id: null,
  restricted: false,
}

interface DatabaseDialogProps {
  open: boolean
  editing: DbConnection | null
  machines: Machine[]
  onOpenChange: (open: boolean) => void
  onSaved: () => void
}

export default function DatabaseDialog({ open, editing, machines, onOpenChange, onSaved }: DatabaseDialogProps) {
  const [form, setForm] = useState<DbConnectionRequest>(EMPTY_FORM)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const toast = useToast()

  useEffect(() => {
    if (!open) return
    setError(null)
    setForm(
      editing
        ? {
            name: editing.name,
            engine: editing.engine,
            host: editing.host ?? '',
            port: editing.port,
            database_name: editing.database_name ?? '',
            username: editing.username ?? '',
            password: '',
            description: editing.description ?? '',
            ssh_machine_id: editing.ssh_machine_id ?? null,
            restricted: editing.restricted,
          }
        : EMPTY_FORM
    )
  }, [open, editing])

  const isSqlite = form.engine === 'sqlite'
  const viaMachine = !isSqlite && form.ssh_machine_id != null
  const databaseRequired = DEFAULT_DATABASE_REQUIRED.includes(form.engine)
  const databaseLabel = isSqlite ? 'Database file path' : 'Default database'
  const databaseHint = isSqlite
    ? 'Path of the database file inside the container.'
    : 'Used to test the connection and to list the available scopes. Each project chooses its own database and schema when it adds this connection.'
  const seenScopes = editing?.available_scopes ?? []
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
      ssh_machine_id: isSqlite ? null : form.ssh_machine_id ?? null,
    }
    try {
      if (editing) await api.catalog.databases.update(editing.id, payload)
      else await api.catalog.databases.create(payload)
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
      description="The connection is the database server. Credentials should belong to a read-only user: projects only read the scope they choose, and only the user's grants on the server truly keep them apart."
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
          <Select
            label="Access"
            value={form.ssh_machine_id != null ? String(form.ssh_machine_id) : 'direct'}
            onValueChange={(v) => set({ ssh_machine_id: v === 'direct' ? null : Number(v) })}
            options={[
              { value: 'direct', label: 'Direct connection' },
              ...machines.map((m) => ({ value: String(m.id), label: `SSH tunnel via ${m.name}` })),
            ]}
          />
        )}
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
            {viaMachine ? (
              <p className="text-xs text-muted mt-1">Host and port are the database as seen from the machine.</p>
            ) : (
              LOOPBACK_HOSTS.includes((form.host ?? '').trim().toLowerCase()) && (
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
              )
            )}
          </div>
        )}
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
          id="database-default-database"
          label={databaseLabel}
          value={form.database_name ?? ''}
          onChange={(e) => set({ database_name: e.target.value })}
          placeholder={isSqlite ? '/data/app.db' : databaseRequired ? 'database name' : 'optional'}
          required={databaseRequired}
          hint={databaseHint}
        />
        <Input
          label="Description"
          value={form.description ?? ''}
          onChange={(e) => set({ description: e.target.value })}
          placeholder="What lives on this server (shown to agents)"
        />
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            className="accent-accent"
            checked={!!form.restricted}
            onChange={(e) => set({ restricted: e.target.checked })}
          />
          Restricted — only organization admins can add it to a project
        </label>
        {editing && !isSqlite && (
          <div className="space-y-2">
            <p className="text-sm font-medium text-muted">Scopes seen at last check</p>
            {seenScopes.length > 0 ? (
              <>
                <div className="flex flex-wrap gap-1">
                  {seenScopes.map((s) => (
                    // The label is the only field every scope has, so it is also the key.
                    <Badge key={s.label}>{s.label}</Badge>
                  ))}
                </div>
                {editing.scopes_checked_at && (
                  <p className="text-xs text-muted">
                    Checked on {new Date(editing.scopes_checked_at).toLocaleString()}
                  </p>
                )}
              </>
            ) : editing.scopes_checked_at ? (
              <p className="text-xs text-muted">
                Checked on {new Date(editing.scopes_checked_at).toLocaleString()}: no scopes visible with these
                credentials.
              </p>
            ) : (
              <p className="text-xs text-muted">
                No scopes seen yet. Test the connection to read them from the server.
              </p>
            )}
          </div>
        )}
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
