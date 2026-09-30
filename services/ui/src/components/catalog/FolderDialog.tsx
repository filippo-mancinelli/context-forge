import { useEffect, useState, type FormEvent } from 'react'
import { api, type FolderRequest, type Machine, type SshSource } from '../../lib/api'
import { Button, Dialog, DialogFooter, Input, Select, useToast } from '../ui'

const EMPTY_FORM: FolderRequest = {
  name: '',
  machine_id: 0,
  root_path: '',
  include_globs: '*.conf,*.yml,*.yaml,*.ini,*.env,*.properties,*.xml,*.json,*.toml',
  exclude_globs: '',
  description: '',
  restricted: false,
}

interface FolderDialogProps {
  open: boolean
  folder: SshSource | null
  machines: Machine[]
  onClose: () => void
  onSaved: () => void
}

export default function FolderDialog({ open, folder, machines, onClose, onSaved }: FolderDialogProps) {
  const [form, setForm] = useState<FolderRequest>(EMPTY_FORM)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const toast = useToast()

  useEffect(() => {
    if (!open) return
    setError(null)
    setForm(
      folder
        ? {
            name: folder.name,
            machine_id: folder.machine_id,
            root_path: folder.root_path,
            include_globs: folder.include_globs ?? '',
            exclude_globs: folder.exclude_globs ?? '',
            description: folder.description ?? '',
            restricted: folder.restricted,
          }
        : EMPTY_FORM
    )
  }, [open, folder])

  const set = (patch: Partial<FolderRequest>) => setForm((f) => ({ ...f, ...patch }))

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      if (folder) await api.catalog.folders.update(folder.id, form)
      else await api.catalog.folders.create(form)
      toast.success(folder ? 'Folder updated' : 'Folder created')
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
      title={folder ? 'Edit folder' : 'New folder'}
      description="A directory on a catalog machine. Reads are confined to the root and filtered by the globs."
    >
      <form onSubmit={submit} className="space-y-3">
        <Input label="Name" value={form.name} onChange={(e) => set({ name: e.target.value })} required />
        <Select
          label="Machine"
          value={form.machine_id ? String(form.machine_id) : undefined}
          onValueChange={(v) => set({ machine_id: Number(v) })}
          options={machines.map((m) => ({ value: String(m.id), label: m.name }))}
          placeholder={machines.length ? 'Select a machine' : 'Add a machine first'}
        />
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
            placeholder="secrets*,*.key,*.pdf"
          />
        </div>
        <Input
          label="Description"
          value={form.description ?? ''}
          onChange={(e) => set({ description: e.target.value })}
          placeholder="What lives here (shown to agents)"
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
        {error && <p className="text-sm text-danger break-words">{error}</p>}
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" loading={saving} disabled={!form.machine_id}>
            {folder ? 'Save' : 'Create'}
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  )
}
