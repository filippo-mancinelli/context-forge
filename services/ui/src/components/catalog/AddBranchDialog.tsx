import { useEffect, useMemo, useState } from 'react'
import { api, type Repo } from '../../lib/api'
import { Button, Dialog, DialogFooter, Input, Select, useToast } from '../ui'

// Repository con migliaia di branch: si mostrano le prime voci e si chiede di filtrare.
const MAX_BRANCH_OPTIONS = 200

// Percorso del progetto GitLab (gruppo[/sottogruppo]/repo) da un URL http(s) o ssh.
function gitlabProjectPath(url: string) {
  try {
    return new URL(url).pathname.replace(/^\/+/, '').replace(/\.git$/, '')
  } catch {
    const m = url.match(/:(?:\d+\/)?(.+?)(?:\.git)?$/)
    return m ? m[1].replace(/^\/+/, '') : ''
  }
}

interface AddBranchDialogProps {
  repo: Repo | null
  registered: Repo[]
  onClose: () => void
  onSaved: () => void
}

export default function AddBranchDialog({ repo, registered, onClose, onSaved }: AddBranchDialogProps) {
  const [branch, setBranch] = useState('')
  const [branches, setBranches] = useState<{ name: string; is_default: boolean }[]>([])
  const [loadingBranches, setLoadingBranches] = useState(false)
  const [branchSearch, setBranchSearch] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const toast = useToast()

  useEffect(() => {
    if (!repo) return
    setBranch('')
    setBranchSearch('')
    setError(null)
    setBranches([])
    setLoadingBranches(true)
    const fetchBranches = async () => {
      try {
        let data: { name: string; is_default: boolean }[] = []
        if (repo.type === 'github') {
          const match = repo.url?.match(/github\.com\/([^/]+)\/([^/]+?)(?:\.git)?$/)
          if (match) data = await api.github.listBranches(match[1], match[2])
        } else if (repo.type === 'gitlab' && repo.url) {
          const projectPath = gitlabProjectPath(repo.url)
          if (projectPath) data = await api.gitlab.listBranches(projectPath)
        }
        setBranches(data.map((b) => ({ ...b, is_default: b.name === repo.branch })))
      } catch (e) {
        setError('Failed to load branches: ' + String(e))
      } finally {
        setLoadingBranches(false)
      }
    }
    void fetchBranches()
  }, [repo])

  const filtered = useMemo(() => {
    const q = branchSearch.toLowerCase().trim()
    return q ? branches.filter((b) => b.name.toLowerCase().includes(q)) : branches
  }, [branches, branchSearch])
  const shown = filtered.slice(0, MAX_BRANCH_OPTIONS)
  const hidden = filtered.length - shown.length

  const inCatalog = useMemo(
    () =>
      new Set(
        registered.filter((r) => repo && r.url && r.url.toLowerCase() === repo.url?.toLowerCase()).map((r) => r.branch)
      ),
    [registered, repo]
  )

  const submit = async () => {
    if (!repo || !branch) return
    setSaving(true)
    setError(null)
    try {
      await api.catalog.repos.create({ type: repo.type, url: repo.url || undefined, branch, language: repo.language || 'auto' })
      toast.success(`Branch "${branch}" registered and queued for indexing`)
      onClose()
      onSaved()
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog
      open={!!repo}
      onOpenChange={(o) => !o && onClose()}
      title={`Index another branch of ${repo ? repo.name.split('@')[0] : ''}`}
      description="Registers the branch as its own catalog repository. Embeddings are reused for unchanged files."
    >
      <div className="space-y-3">
        {error && <p className="text-xs text-danger break-words">{error}</p>}
        {loadingBranches ? (
          <p className="text-sm text-muted py-2">Loading branches...</p>
        ) : branches.length === 0 && !error ? (
          <>
            <p className="text-xs text-muted">Could not fetch branches. Enter manually:</p>
            <Input label="Branch" value={branch} onChange={(e) => setBranch(e.target.value)} placeholder="develop" />
          </>
        ) : (
          <>
            {branches.length > 15 && (
              <Input value={branchSearch} onChange={(e) => setBranchSearch(e.target.value)} placeholder="Filter branches..." />
            )}
            <Select
              label="Branch"
              value={branch}
              onValueChange={setBranch}
              options={shown.map((b) => ({
                value: b.name,
                label: inCatalog.has(b.name) ? `${b.name} (in catalog)` : b.is_default ? `${b.name} (default)` : b.name,
              }))}
            />
            {hidden > 0 && (
              <p className="text-xs text-muted">
                Showing first {MAX_BRANCH_OPTIONS} of {filtered.length} branches — refine the filter to narrow the list.
              </p>
            )}
          </>
        )}
        <p className="text-xs text-muted">The name is proposed from the URL, with @branch when it is already taken.</p>
      </div>
      <DialogFooter>
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button variant="primary" onClick={submit} loading={saving} disabled={!branch || inCatalog.has(branch)}>
          Register & index
        </Button>
      </DialogFooter>
    </Dialog>
  )
}
