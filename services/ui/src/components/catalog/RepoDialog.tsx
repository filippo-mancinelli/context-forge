import { useEffect, useState, type FormEvent } from 'react'
import { api, type Repo, type RepoRequest } from '../../lib/api'
import { Button, Dialog, DialogFooter, Input, Select, useToast } from '../ui'

const EMPTY_FORM: RepoRequest = {
  name: '',
  type: 'gitlab',
  url: '',
  path: '',
  branch: 'main',
  language: 'auto',
  token: '',
  description: '',
  restricted: false,
}

interface RepoDialogProps {
  open: boolean
  repo: Repo | null
  onClose: () => void
  onSaved: () => void
}

export default function RepoDialog({ open, repo, onClose, onSaved }: RepoDialogProps) {
  const [form, setForm] = useState<RepoRequest>(EMPTY_FORM)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const toast = useToast()

  useEffect(() => {
    if (!open) return
    setError(null)
    setForm(
      repo
        ? {
            name: repo.name,
            type: repo.type,
            url: repo.url ?? '',
            path: repo.path ?? '',
            branch: repo.branch || 'main',
            language: repo.language || 'auto',
            token: '',
            description: repo.description ?? '',
            restricted: repo.restricted,
          }
        : EMPTY_FORM
    )
  }, [open, repo])

  const set = (patch: Partial<RepoRequest>) => setForm((f) => ({ ...f, ...patch }))
  const isLocal = form.type === 'local'

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    const payload: RepoRequest = {
      ...form,
      name: form.name?.trim() || undefined,
      url: isLocal ? undefined : form.url || undefined,
      path: isLocal ? form.path || undefined : undefined,
      token: form.token || undefined,
    }
    try {
      if (repo) await api.catalog.repos.update(repo.id, payload)
      else await api.catalog.repos.create(payload)
      toast.success(repo ? 'Repository updated' : 'Repository registered and queued for indexing')
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
      title={repo ? `Edit ${repo.name}` : 'New repository'}
      description="Indexed once for the organization. Each project adds it from the catalog."
    >
      <form onSubmit={submit} className="space-y-3">
        <div className="grid gap-3 md:grid-cols-2">
          <Input
            label="Name"
            value={form.name ?? ''}
            onChange={(e) => set({ name: e.target.value })}
            placeholder="Proposed from the URL"
          />
          <Select
            label="Type"
            value={form.type}
            onValueChange={(v) => set({ type: v as Repo['type'] })}
            options={[
              { value: 'gitlab', label: 'GitLab' },
              { value: 'github', label: 'GitHub' },
              { value: 'local', label: 'Local' },
            ]}
          />
        </div>
        {isLocal ? (
          <Input
            label="Local path"
            value={form.path ?? ''}
            onChange={(e) => set({ path: e.target.value })}
            placeholder="/repos/project (path inside the container)"
            hint="Local repositories index the mounted working tree as it is."
            required
          />
        ) : (
          <Input
            label="Repository URL"
            value={form.url ?? ''}
            onChange={(e) => set({ url: e.target.value })}
            placeholder={`https://${form.type}.com/owner/repo`}
            required
          />
        )}
        <div className="grid gap-3 md:grid-cols-2">
          {!isLocal && (
            <Input label="Branch" value={form.branch} onChange={(e) => set({ branch: e.target.value })} placeholder="main" />
          )}
          <Input
            label="Language"
            value={form.language ?? ''}
            onChange={(e) => set({ language: e.target.value })}
            placeholder="auto"
          />
        </div>
        {!isLocal && (
          <Input
            label="Access token"
            type="password"
            autoComplete="new-password"
            value={form.token ?? ''}
            onChange={(e) => set({ token: e.target.value })}
            placeholder={repo?.has_token ? 'Leave empty to keep the stored token' : 'Optional'}
            hint="Overrides the organization GitHub/GitLab token for this repository."
          />
        )}
        <Input
          label="Description"
          value={form.description ?? ''}
          onChange={(e) => set({ description: e.target.value })}
          placeholder="What this code is (shown to agents)"
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
          <Button type="submit" variant="primary" loading={saving}>
            {repo ? 'Save' : 'Register'}
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  )
}
