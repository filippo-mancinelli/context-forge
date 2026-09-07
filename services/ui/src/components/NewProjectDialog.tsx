import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { useAppStore } from '../store'
import { Button, Dialog, DialogFooter, Input, useToast } from './ui'

export default function NewProjectDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const loadProjects = useAppStore((s) => s.loadProjects)
  const setActiveProject = useAppStore((s) => s.setActiveProject)
  const toast = useToast()
  const [name, setName] = useState('')
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    if (open) setName('')
  }, [open])

  const create = async () => {
    setCreating(true)
    try {
      const res = await api.projects.create(name.trim())
      await loadProjects()
      setActiveProject(res.project.id)
      onClose()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setCreating(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose() }} title="New project">
      <Input
        label="Project name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="e.g. Billing service"
      />
      <DialogFooter>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" onClick={create} loading={creating} disabled={!name.trim()}>
          Create
        </Button>
      </DialogFooter>
    </Dialog>
  )
}
