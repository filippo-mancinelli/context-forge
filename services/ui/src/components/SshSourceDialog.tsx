import { useEffect, useState, type FormEvent } from 'react'
import { api, type SshSource, type SshSourceRequest } from '../lib/api'
import { Button, Dialog, DialogFooter, Input, Select, Textarea, useToast } from './ui'

const EMPTY_FORM: SshSourceRequest = {
  name: '',
  host: '',
  port: 22,
  username: '',
  auth_method: 'password',
  password: '',
  private_key: '',
  root_path: '',
  include_globs: '*.conf,*.yml,*.yaml,*.ini,*.env,*.properties,*.xml,*.json,*.toml',
  exclude_globs: '',
  description: '',
}

const AUTH_OPTIONS = [
  { value: 'password', label: 'Password' },
  { value: 'key', label: 'Private key' },
]

interface SshSourceDialogProps {
  open: boolean
  source: SshSource | null
  onClose: () => void
  onSaved: () => void
}

export default function SshSourceDialog({ open, source, onClose, onSaved }: SshSourceDialogProps) {
  const [form, setForm] = useState<SshSourceRequest>(EMPTY_FORM)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const toast = useToast()

  useEffect(() => {
    if (!open) return
    setError(null)
    if (source) {
      setForm({
        name: source.name,
        host: source.host,
        port: source.port,
        username: source.username,
        auth_method: source.auth_method,
        password: '',
        private_key: '',
        root_path: source.root_path,
        include_globs: source.include_globs ?? '',
        exclude_globs: source.exclude_globs ?? '',
        description: source.description ?? '',
      })
    } else {
      setForm(EMPTY_FORM)
    }
  }, [open, source])

  const set = (patch: Partial<SshSourceRequest>) => setForm((f) => ({ ...f, ...patch }))

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      if (source) await api.sshSources.update(source.id, form)
      else await api.sshSources.create(form)
      toast.success(source ? 'Source updated' : 'Source created')
      onClose()
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
      onOpenChange={(o) => !o && onClose()}
      title={source ? 'Edit SSH source' : 'New SSH source'}
      description="Read-only. Paths are confined to the root; secrets are stored encrypted."
    >
      <form onSubmit={submit} className="space-y-3">
        <Input label="Name" value={form.name} onChange={(e) => set({ name: e.target.value })} required />
        <div className="grid grid-cols-3 gap-2">
          <div className="col-span-2">
            <Input label="Host" value={form.host} onChange={(e) => set({ host: e.target.value })} placeholder="10.0.0.9" required />
          </div>
          <Input
            label="Port"
            type="number"
            value={form.port ?? 22}
            onChange={(e) => set({ port: e.target.value ? Number(e.target.value) : 22 })}
          />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Input label="Username" value={form.username} onChange={(e) => set({ username: e.target.value })} autoComplete="off" required />
          <Select
            label="Authentication"
            value={form.auth_method ?? 'password'}
            onValueChange={(v) => set({ auth_method: v as 'key' | 'password' })}
            options={AUTH_OPTIONS}
          />
        </div>
        {form.auth_method === 'key' ? (
          <Textarea
            label="Private key"
            value={form.private_key ?? ''}
            onChange={(e) => set({ private_key: e.target.value })}
            placeholder={source?.has_secret ? '(unchanged)' : '-----BEGIN OPENSSH PRIVATE KEY-----'}
            rows={4}
            autoComplete="off"
          />
        ) : (
          <Input
            label="Password"
            type="password"
            value={form.password ?? ''}
            onChange={(e) => set({ password: e.target.value })}
            placeholder={source?.has_secret ? '(unchanged)' : ''}
            autoComplete="new-password"
          />
        )}
        <Input
          label="Root path"
          value={form.root_path}
          onChange={(e) => set({ root_path: e.target.value })}
          placeholder="/etc/myapp"
          hint="Reads are confined to this directory."
          required
        />
        <div className="grid grid-cols-2 gap-2">
          <Input
            label="Include globs"
            value={form.include_globs ?? ''}
            onChange={(e) => set({ include_globs: e.target.value })}
            placeholder="*.conf,*.yml"
          />
          <Input
            label="Exclude globs"
            value={form.exclude_globs ?? ''}
            onChange={(e) => set({ exclude_globs: e.target.value })}
            placeholder="secrets*,*.key"
          />
        </div>
        <Input
          label="Description"
          value={form.description ?? ''}
          onChange={(e) => set({ description: e.target.value })}
          placeholder="What lives here (shown to agents)"
        />
        {error && <p className="text-sm text-danger">{error}</p>}
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" loading={saving}>
            {source ? 'Save' : 'Create'}
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  )
}
