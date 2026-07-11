import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import {
  Plus,
  Pencil,
  Trash2,
  ExternalLink,
  Database,
  GitBranch,
  Globe,
  Server,
} from 'lucide-react'
import { api, type DbConnection, type Environment, type EnvironmentKind, type EnvironmentRequest } from '../lib/api'
import { Badge, Button, Dialog, DialogFooter, Input, Select, Textarea, useConfirm, useToast } from '../components/ui'

const KIND_ORDER: EnvironmentKind[] = ['production', 'staging', 'development', 'other']

const KIND_LABEL: Record<EnvironmentKind, string> = {
  production: 'Production',
  staging: 'Staging',
  development: 'Development',
  other: 'Other',
}

const KIND_VARIANT: Record<EnvironmentKind, 'danger' | 'warning' | 'accent' | 'muted'> = {
  production: 'danger',
  staging: 'warning',
  development: 'accent',
  other: 'muted',
}

const EMPTY_FORM: EnvironmentRequest = {
  name: '',
  kind: 'staging',
  url: '',
  domains: [],
  db_connection_id: null,
  database_notes: '',
  repo: '',
  branch: '',
  config_notes: '',
  notes: '',
}

function EnvironmentDialog({
  open,
  onOpenChange,
  editing,
  connections,
  onSaved,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  editing: Environment | null
  connections: DbConnection[]
  onSaved: () => void
}) {
  const [form, setForm] = useState<EnvironmentRequest>(EMPTY_FORM)
  const [domainsInput, setDomainsInput] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    if (editing) {
      setForm({
        name: editing.name,
        kind: editing.kind,
        url: editing.url || '',
        domains: editing.domains,
        db_connection_id: editing.db_connection_id ?? null,
        database_notes: editing.database_notes || '',
        repo: editing.repo || '',
        branch: editing.branch || '',
        config_notes: editing.config_notes || '',
        notes: editing.notes || '',
      })
      setDomainsInput(editing.domains.join(', '))
    } else {
      setForm(EMPTY_FORM)
      setDomainsInput('')
    }
    setError(null)
  }, [open, editing])

  const set = <K extends keyof EnvironmentRequest>(key: K, value: EnvironmentRequest[K]) =>
    setForm(prev => ({ ...prev, [key]: value }))

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!form.name.trim()) {
      setError('Name is required.')
      return
    }
    setSaving(true)
    setError(null)
    const payload: EnvironmentRequest = {
      ...form,
      name: form.name.trim(),
      url: form.url?.trim() || undefined,
      domains: domainsInput.split(',').map(d => d.trim()).filter(Boolean),
      database_notes: form.database_notes?.trim() || undefined,
      repo: form.repo?.trim() || undefined,
      branch: form.branch?.trim() || undefined,
      config_notes: form.config_notes?.trim() || undefined,
      notes: form.notes?.trim() || undefined,
    }
    try {
      if (editing) {
        await api.environments.update(editing.id, payload)
      } else {
        await api.environments.create(payload)
      }
      onOpenChange(false)
      onSaved()
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={editing ? `Edit ${editing.name}` : 'New environment'}
      description="Where a project actually runs: URL, database, and which repo/branch deploys there."
      maxWidth="640px"
    >
      <form onSubmit={handleSubmit} className="space-y-3 max-h-[70vh] overflow-y-auto pr-1">
        <div className="grid grid-cols-2 gap-3">
          <Input
            label="Name"
            value={form.name}
            onChange={e => set('name', e.target.value)}
            placeholder="acme-prod"
          />
          <Select
            label="Kind"
            value={form.kind}
            onValueChange={v => set('kind', v as EnvironmentKind)}
            options={KIND_ORDER.map(k => ({ value: k, label: KIND_LABEL[k] }))}
          />
        </div>

        <Input
          label="URL"
          value={form.url}
          onChange={e => set('url', e.target.value)}
          placeholder="https://app.example.com"
        />
        <Input
          label="Domains"
          value={domainsInput}
          onChange={e => setDomainsInput(e.target.value)}
          placeholder="example.com, api.example.com"
          hint="Comma-separated. Public domains this environment answers on."
        />

        <div className="grid grid-cols-2 gap-3">
          <Select
            label="Database"
            value={form.db_connection_id != null ? String(form.db_connection_id) : 'none'}
            onValueChange={v => set('db_connection_id', v === 'none' ? null : Number(v))}
            options={[
              { value: 'none', label: 'None / not tracked' },
              ...connections.map(c => ({ value: String(c.id), label: c.name })),
            ]}
          />
          <Input
            label="Database notes"
            value={form.database_notes}
            onChange={e => set('database_notes', e.target.value)}
            placeholder="RDS instance, no managed connection"
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <Input
            label="Repo"
            value={form.repo}
            onChange={e => set('repo', e.target.value)}
            placeholder="org/repo"
          />
          <Input
            label="Branch"
            value={form.branch}
            onChange={e => set('branch', e.target.value)}
            placeholder="main"
          />
        </div>

        <Textarea
          label="Config notes"
          value={form.config_notes}
          onChange={e => set('config_notes', e.target.value)}
          rows={3}
          placeholder="Env vars, feature flags, anything deploy-specific..."
        />
        <Textarea
          label="Notes"
          value={form.notes}
          onChange={e => set('notes', e.target.value)}
          rows={3}
          placeholder="Anything else worth remembering about this environment..."
        />

        {error && <p style={{ color: 'var(--danger)' }} className="text-sm">{error}</p>}

        <DialogFooter>
          <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" loading={saving} disabled={saving || !form.name.trim()}>
            {editing ? 'Save changes' : 'Create environment'}
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  )
}

function EnvironmentCard({
  env,
  onEdit,
  onDelete,
}: {
  env: Environment
  onEdit: (env: Environment) => void
  onDelete: (env: Environment) => void
}) {
  return (
    <div style={{ border: '1px solid var(--border)' }} className="p-4">
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="flex items-center gap-2 min-w-0">
          <Server className="w-4 h-4 text-muted flex-shrink-0" />
          <h3 className="text-sm font-semibold text-text truncate">{env.name}</h3>
          <Badge variant={KIND_VARIANT[env.kind]}>{KIND_LABEL[env.kind]}</Badge>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <button
            onClick={() => onEdit(env)}
            className="text-muted hover:text-accent transition-colors"
            title="Edit"
          >
            <Pencil className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => onDelete(env)}
            className="text-muted hover:text-danger transition-colors"
            title="Delete"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted mb-2">
        {env.url && (
          <a
            href={env.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-accent hover:underline"
          >
            <ExternalLink className="w-3 h-3" /> {env.url.replace(/^https?:\/\//, '')}
          </a>
        )}
        {(env.repo || env.branch) && (
          <span className="inline-flex items-center gap-1 font-mono">
            <GitBranch className="w-3 h-3" />
            {env.repo || '—'}
            {env.branch && <span className="text-text">@{env.branch}</span>}
          </span>
        )}
        {env.db_connection_id ? (
          <Link
            to={`/datasources/${env.db_connection_id}`}
            className="inline-flex items-center gap-1 text-accent hover:underline"
          >
            <Database className="w-3 h-3" /> {env.db_connection_name || `Connection #${env.db_connection_id}`}
          </Link>
        ) : env.database_notes ? (
          <span className="inline-flex items-center gap-1">
            <Database className="w-3 h-3" /> {env.database_notes}
          </span>
        ) : null}
      </div>

      {env.domains.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-2">
          {env.domains.map(d => (
            <Badge key={d} variant="default" className="inline-flex items-center gap-1">
              <Globe className="w-2.5 h-2.5" /> {d}
            </Badge>
          ))}
        </div>
      )}

      {env.config_notes && (
        <p className="text-xs text-muted whitespace-pre-wrap mb-1">
          <span className="font-medium text-text">Config: </span>{env.config_notes}
        </p>
      )}
      {env.notes && (
        <p className="text-xs text-muted whitespace-pre-wrap">{env.notes}</p>
      )}
    </div>
  )
}

export default function Environments() {
  const confirm = useConfirm()
  const toast = useToast()
  const [environments, setEnvironments] = useState<Environment[]>([])
  const [connections, setConnections] = useState<DbConnection[]>([])
  const [loading, setLoading] = useState(true)
  const [pageError, setPageError] = useState<string | null>(null)
  const [showDialog, setShowDialog] = useState(false)
  const [editing, setEditing] = useState<Environment | null>(null)

  const load = useCallback(async () => {
    try {
      const [envs, ds] = await Promise.all([api.environments.list(), api.datasources.list()])
      setEnvironments(envs)
      setConnections(ds.connections)
      setPageError(null)
    } catch (e) {
      setPageError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const grouped = useMemo(() => {
    const byKind = new Map<EnvironmentKind, Environment[]>()
    for (const env of environments) {
      const list = byKind.get(env.kind) || []
      list.push(env)
      byKind.set(env.kind, list)
    }
    for (const list of byKind.values()) list.sort((a, b) => a.name.localeCompare(b.name))
    return KIND_ORDER.map(kind => ({ kind, items: byKind.get(kind) || [] })).filter(g => g.items.length > 0)
  }, [environments])

  const handleAdd = () => {
    setEditing(null)
    setShowDialog(true)
  }

  const handleEdit = (env: Environment) => {
    setEditing(env)
    setShowDialog(true)
  }

  const handleDelete = async (env: Environment) => {
    const ok = await confirm({
      title: 'Delete environment',
      message: `Delete "${env.name}"? This cannot be undone.`,
      confirmLabel: 'Delete',
      danger: true,
      onConfirm: () => api.environments.delete(env.id),
    })
    if (!ok) return
    toast.success('Environment deleted')
    setEnvironments(prev => prev.filter(e => e.id !== env.id))
  }

  return (
    <div className="p-4 sm:p-8">
      <div className="page-wide">
        <div className="mb-6 flex items-start justify-between gap-4">
          <div>
            <h1>Environments</h1>
            <p className="text-muted text-sm">
              {environments.length === 0
                ? 'Where your projects actually run — URLs, databases, and deploy sources'
                : `${environments.length} environment${environments.length === 1 ? '' : 's'}`}
            </p>
          </div>
          <Button variant="primary" onClick={handleAdd}>
            <Plus className="w-3.5 h-3.5" /> Add environment
          </Button>
        </div>

        {pageError && (
          <div
            style={{ border: '1px solid var(--danger)', color: 'var(--danger)' }}
            className="text-sm p-3 mb-4 bg-[#fef2f2]"
          >
            {pageError}
          </div>
        )}

        {loading ? (
          <p className="text-muted text-sm">Loading...</p>
        ) : environments.length === 0 ? (
          <p className="text-muted text-sm">No environments yet. Add your first one above.</p>
        ) : (
          <div className="space-y-6 max-w-4xl">
            {grouped.map(({ kind, items }) => (
              <div key={kind}>
                <h2 className="text-xs font-semibold text-muted uppercase tracking-wide mb-2">
                  {KIND_LABEL[kind]} <span className="font-normal normal-case">({items.length})</span>
                </h2>
                <div className="space-y-3">
                  {items.map(env => (
                    <EnvironmentCard key={env.id} env={env} onEdit={handleEdit} onDelete={handleDelete} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}

        <EnvironmentDialog
          open={showDialog}
          onOpenChange={setShowDialog}
          editing={editing}
          connections={connections}
          onSaved={() => { toast.success(editing ? 'Environment updated' : 'Environment created'); void load() }}
        />
      </div>
    </div>
  )
}
