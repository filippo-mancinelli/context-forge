import { useEffect, useMemo, useState } from 'react'
import { api, type CatalogKind } from '../../lib/api'
import { Badge, Button, Dialog, DialogFooter, Input, useToast } from '../ui'
import RestrictedIcon from './RestrictedIcon'

export interface CatalogOption {
  id: number
  name: string
  detail: string
  restricted: boolean
}

interface AddFromCatalogDialogProps {
  open: boolean
  title: string
  kind: CatalogKind
  projectId: number
  options: CatalogOption[]
  selectedIds: Set<number>
  allowRestricted: boolean
  onClose: () => void
  onAdded: () => void
}

export default function AddFromCatalogDialog({
  open, title, kind, projectId, options, selectedIds, allowRestricted, onClose, onAdded,
}: AddFromCatalogDialogProps) {
  const toast = useToast()
  const [query, setQuery] = useState('')
  const [checked, setChecked] = useState<Set<number>>(new Set())
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (open) {
      setQuery('')
      setChecked(new Set())
    }
  }, [open])

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    return q ? options.filter((o) => `${o.name} ${o.detail}`.toLowerCase().includes(q)) : options
  }, [options, query])

  const toggle = (id: number) =>
    setChecked((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const submit = async () => {
    setSaving(true)
    try {
      for (const id of checked) await api.projects.selectResource(projectId, kind, id)
      toast.success(`${checked.size} added to the project`)
      onClose()
      onAdded()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title={title}
      description="Resources registered in the organization catalog."
      maxWidth="560px"
    >
      <Input label="Search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Name, machine or path" />
      <div className="mt-3 max-h-80 overflow-y-auto border border-border rounded">
        {visible.length === 0 ? (
          <p className="p-4 text-sm text-muted">Nothing in the catalog matches.</p>
        ) : (
          visible.map((o) => {
            const already = selectedIds.has(o.id)
            const locked = o.restricted && !allowRestricted
            return (
              <label
                key={o.id}
                className={[
                  'flex items-start gap-3 px-3 py-2 border-b border-border last:border-b-0 text-sm',
                  already || locked ? 'text-muted' : 'cursor-pointer hover:bg-surface',
                ].join(' ')}
              >
                <input
                  type="checkbox"
                  className="accent-accent mt-0.5"
                  checked={already || checked.has(o.id)}
                  disabled={already || locked}
                  onChange={() => toggle(o.id)}
                />
                <span className="min-w-0 flex-1">
                  <span className="font-medium">{o.name}</span>
                  {o.restricted && <RestrictedIcon />}
                  <span className="block text-xs text-muted font-mono truncate">{o.detail}</span>
                </span>
                {already && <Badge variant="muted">Selected</Badge>}
                {locked && !already && <Badge variant="muted">Admin only</Badge>}
              </label>
            )
          })
        )}
      </div>
      <DialogFooter>
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button variant="primary" onClick={submit} loading={saving} disabled={checked.size === 0}>
          {checked.size > 0 ? `Add (${checked.size})` : 'Add'}
        </Button>
      </DialogFooter>
    </Dialog>
  )
}
