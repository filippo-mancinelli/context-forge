import { useEffect, useState, type FormEvent } from 'react'
import { api, type Machine, type MachineRequest } from '../../lib/api'
import { Button, Dialog, DialogFooter, Input, Select, Textarea, useToast } from '../ui'

const EMPTY_FORM: MachineRequest = {
  name: '',
  host: '',
  port: 22,
  username: '',
  auth_method: 'password',
  password: '',
  private_key: '',
  description: '',
}

const AUTH_OPTIONS = [
  { value: 'password', label: 'Password' },
  { value: 'key', label: 'Private key' },
]

interface MachineDialogProps {
  open: boolean
  machine: Machine | null
  onClose: () => void
  onSaved: () => void
}

export default function MachineDialog({ open, machine, onClose, onSaved }: MachineDialogProps) {
  const [form, setForm] = useState<MachineRequest>(EMPTY_FORM)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const toast = useToast()

  useEffect(() => {
    if (!open) return
    setError(null)
    setForm(
      machine
        ? {
            name: machine.name,
            host: machine.host,
            port: machine.port,
            username: machine.username,
            auth_method: machine.auth_method,
            password: '',
            private_key: '',
            description: machine.description ?? '',
          }
        : EMPTY_FORM
    )
  }, [open, machine])

  const set = (patch: Partial<MachineRequest>) => setForm((f) => ({ ...f, ...patch }))

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      if (machine) await api.catalog.machines.update(machine.id, form)
      else await api.catalog.machines.create(form)
      toast.success(machine ? 'Machine updated' : 'Machine created')
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
      title={machine ? 'Edit machine' : 'New machine'}
      description="SSH access shared by folders and database tunnels. Secrets are stored encrypted."
    >
      <form onSubmit={submit} className="space-y-3">
        <Input
          label="Name"
          value={form.name}
          onChange={(e) => set({ name: e.target.value })}
          placeholder="deploy@10.0.0.5"
          required
        />
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
          <Input
            label="Username"
            value={form.username}
            onChange={(e) => set({ username: e.target.value })}
            autoComplete="off"
            required
          />
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
            placeholder={machine?.has_secret ? '(unchanged)' : '-----BEGIN OPENSSH PRIVATE KEY-----'}
            rows={4}
            autoComplete="off"
          />
        ) : (
          <Input
            label="Password"
            type="password"
            value={form.password ?? ''}
            onChange={(e) => set({ password: e.target.value })}
            placeholder={machine?.has_secret ? '(unchanged)' : ''}
            autoComplete="new-password"
          />
        )}
        <Input
          label="Description"
          value={form.description ?? ''}
          onChange={(e) => set({ description: e.target.value })}
          placeholder="What this machine is (shown to agents)"
        />
        {error && <p className="text-sm text-danger break-words">{error}</p>}
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" loading={saving}>
            {machine ? 'Save' : 'Create'}
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  )
}
