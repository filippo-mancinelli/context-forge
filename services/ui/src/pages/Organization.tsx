import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Trash2, Plus, Pencil, Users } from 'lucide-react'
import {
  api,
  MCP_PERMISSION_VALUES,
  type McpPermissionsMatrix,
  type OrgMember,
  type OrgRole,
  type ProjectMember,
  type ToolCall,
  type ToolCallStats,
} from '../lib/api'
import { useAppStore } from '../store'
import { Banner, Button, Card, Input, Select, Badge, Dialog, DialogFooter, Table, Thead, Tbody, Tr, Th, Td, Tabs, TabsList, TabsTrigger, TabsContent, useConfirm, useToast } from '../components/ui'
import NewProjectDialog from '../components/NewProjectDialog'

const PROJECT_ROLE_OPTIONS = [
  { value: 'viewer', label: 'Viewer' },
  { value: 'member', label: 'Member' },
  { value: 'admin', label: 'Admin' },
]

function ProjectMembersDialog({
  projectId,
  projectName,
  isOwner,
  onClose,
}: {
  projectId: number
  projectName: string
  isOwner: boolean
  onClose: () => void
}) {
  const toast = useToast()
  const [members, setMembers] = useState<ProjectMember[]>([])
  const [loading, setLoading] = useState(true)
  const [email, setEmail] = useState('')
  const [role, setRole] = useState('member')
  const [adding, setAdding] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.projects.members(projectId)
      setMembers(res.members)
    } catch (e) {
      toast.error(String(e))
    } finally {
      setLoading(false)
    }
  }, [projectId, toast])

  useEffect(() => {
    void load()
  }, [load])

  const add = async (e: FormEvent) => {
    e.preventDefault()
    if (!email.trim()) return
    setAdding(true)
    try {
      // Non-owners can only add members at the 'member' role — the server
      // forces this anyway, but keep the request explicit.
      await api.projects.addMember(projectId, { email: email.trim(), role: isOwner ? role : 'member' })
      setEmail('')
      await load()
      toast.success('Member enabled')
    } catch (err) {
      toast.error(String(err))
    } finally {
      setAdding(false)
    }
  }

  const remove = async (userId: number) => {
    try {
      await api.projects.removeMember(projectId, userId)
      await load()
    } catch (err) {
      toast.error(String(err))
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Members — ${projectName}`}
      description="Org owners and admins already access every project. Enable other users here."
    >
      <div className="space-y-4">
        <form onSubmit={add} className="flex items-end gap-2">
          <div className="flex-1">
            <Input
              label="User email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="user@company.com"
            />
          </div>
          {isOwner ? (
            <Select
              label="Role"
              value={role}
              onValueChange={setRole}
              options={PROJECT_ROLE_OPTIONS}
            />
          ) : (
            <p className="text-xs text-muted pb-2.5">Joins as member</p>
          )}
          <Button type="submit" variant="primary" loading={adding}>
            Add
          </Button>
        </form>
        {loading ? (
          <p className="text-sm text-muted">Loading…</p>
        ) : members.length === 0 ? (
          <p className="text-sm text-muted">No explicit members yet.</p>
        ) : (
          <Table>
            <Thead>
              <Tr>
                <Th>User</Th>
                {isOwner && <Th>Role</Th>}
                <Th className="w-10" />
              </Tr>
            </Thead>
            <Tbody>
              {members.map((m) => (
                <Tr key={m.user_id}>
                  <Td>
                    <span className="font-medium">{m.username ?? m.email ?? `#${m.user_id}`}</span>
                    {m.email && m.username && (
                      <span className="text-xs text-muted ml-2">{m.email}</span>
                    )}
                  </Td>
                  {isOwner && (
                    <Td>
                      {m.role && <Badge>{m.role}</Badge>}
                    </Td>
                  )}
                  <Td>
                    {isOwner && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => remove(m.user_id)}
                        title="Remove member"
                        aria-label="Remove member"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </Button>
                    )}
                  </Td>
                </Tr>
              ))}
            </Tbody>
          </Table>
        )}
      </div>
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onClose}>
          Close
        </Button>
      </DialogFooter>
    </Dialog>
  )
}

const ROLE_OPTIONS: { value: OrgRole; label: string }[] = [
  { value: 'viewer', label: 'Viewer' },
  { value: 'member', label: 'Member' },
  { value: 'admin', label: 'Admin' },
  { value: 'owner', label: 'Owner' },
]

const ROLE_RANK: Record<OrgRole, number> = { viewer: 0, member: 1, admin: 2, owner: 3 }

function roleBadge(role: OrgRole) {
  const variant = role === 'owner' ? 'accent' : role === 'admin' ? 'success' : 'muted'
  return <Badge variant={variant}>{role}</Badge>
}

function McpPermissionsSection({ orgId, myRole }: { orgId: number; myRole: OrgRole }) {
  const toast = useToast()
  const confirm = useConfirm()
  const [matrix, setMatrix] = useState<Record<OrgRole, string[]> | null>(null)
  const [customized, setCustomized] = useState<Record<OrgRole, boolean>>({
    viewer: false, member: false, admin: false, owner: false,
  })
  const [initial, setInitial] = useState<Record<OrgRole, string[]> | null>(null)
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    try {
      const res: McpPermissionsMatrix = await api.organizations.mcpPermissions(orgId)
      const roles = {} as Record<OrgRole, string[]>
      const custom = {} as Record<OrgRole, boolean>
      for (const role of Object.keys(res.roles) as OrgRole[]) {
        roles[role] = res.roles[role].permissions
        custom[role] = res.roles[role].customized
      }
      setMatrix(roles)
      setInitial(roles)
      setCustomized(custom)
    } catch (e) {
      toast.error(String(e))
    }
  }, [orgId, toast])

  useEffect(() => { load() }, [load])

  if (!matrix) return null

  const toggle = (role: OrgRole, perm: string) => {
    setMatrix((m) => {
      if (!m) return m
      const current = m[role]
      let next: string[]
      if (perm === '*') {
        next = current.includes('*') ? [] : ['*']
      } else {
        const without = current.filter((p) => p !== '*' && p !== perm)
        next = current.includes(perm) ? without : [...without, perm]
      }
      return { ...m, [role]: next }
    })
  }

  const reducingOwnRole =
    initial !== null &&
    initial[myRole].some((p) => !matrix[myRole].includes(p) && !matrix[myRole].includes('*'))

  const handleSave = async () => {
    setSaving(true)
    try {
      await api.organizations.updateMcpPermissions(orgId, matrix)
      toast.success('MCP permissions saved')
      await load()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setSaving(false)
    }
  }

  const handleReset = async () => {
    const ok = await confirm({
      title: 'Restore defaults',
      message: 'Discard all customizations and restore the default MCP permissions?',
      confirmLabel: 'Restore',
      onConfirm: () => api.organizations.resetMcpPermissions(orgId),
    })
    if (!ok) return
    toast.success('Defaults restored')
    await load()
  }

  return (
    <section className="mt-8">
      <h2 className="text-base font-semibold mb-1">MCP permissions</h2>
      <p className="text-sm text-muted mb-3">
        Which MCP tools each role can use. Agents authenticating via Keycloak or OAuth get
        the permissions of their role in this organization.
      </p>
      <Table>
        <Thead>
          <Tr>
            <Th>Role</Th>
            <Th>All (*)</Th>
            {MCP_PERMISSION_VALUES.map((p) => (
              <Th key={p}><code className="text-xs font-mono">{p}</code></Th>
            ))}
          </Tr>
        </Thead>
        <Tbody>
          {ROLE_OPTIONS.map(({ value: role, label }) => (
            <Tr key={role}>
              <Td>
                {label}
                {customized[role] && <Badge variant="accent" className="ml-2">custom</Badge>}
              </Td>
              <Td>
                <input
                  type="checkbox"
                  checked={matrix[role].includes('*')}
                  onChange={() => toggle(role, '*')}
                />
              </Td>
              {MCP_PERMISSION_VALUES.map((p) => (
                <Td key={p}>
                  <input
                    type="checkbox"
                    checked={matrix[role].includes('*') || matrix[role].includes(p)}
                    disabled={matrix[role].includes('*')}
                    onChange={() => toggle(role, p)}
                  />
                </Td>
              ))}
            </Tr>
          ))}
        </Tbody>
      </Table>
      {reducingOwnRole && (
        <p className="text-sm mt-2 text-warning">
          Warning: you are reducing the permissions of your own role ({myRole}).
        </p>
      )}
      <div className="flex gap-2 mt-3">
        <Button variant="primary" size="sm" onClick={handleSave} loading={saving}>
          Save permissions
        </Button>
        <Button variant="ghost" size="sm" onClick={handleReset}>
          Restore defaults
        </Button>
      </div>
    </section>
  )
}

function ProjectsSection({ canAddMembers, isOwner }: { canAddMembers: boolean; isOwner: boolean }) {
  const projects = useAppStore((s) => s.projects)
  const activeProjectId = useAppStore((s) => s.activeProjectId)
  const loadProjects = useAppStore((s) => s.loadProjects)
  const setActiveProject = useAppStore((s) => s.setActiveProject)
  const confirm = useConfirm()
  const toast = useToast()
  const [showNew, setShowNew] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editName, setEditName] = useState('')
  const [membersProject, setMembersProject] = useState<{ id: number; name: string } | null>(null)

  const startRename = (id: number, name: string) => {
    setEditingId(id)
    setEditName(name)
  }

  const saveRename = async (id: number) => {
    if (!editName.trim()) return
    try {
      await api.projects.update(id, editName.trim())
      setEditingId(null)
      await loadProjects()
      toast.success('Project renamed')
    } catch (e) {
      toast.error(String(e))
    }
  }

  const handleDelete = async (id: number, name: string) => {
    const ok = await confirm({
      title: 'Delete project',
      message: `Delete project «${name}»? All its resources (repos, knowledge, web, data sources, contracts, chats, memory) will be permanently removed.`,
      confirmLabel: 'Delete',
      danger: true,
      onConfirm: () => api.projects.delete(id),
    })
    if (!ok) return
    // loadProjects re-resolves the active project (falls back to the default
    // when the active one was just deleted) and updates the store.
    await loadProjects()
    const active = useAppStore.getState().activeProjectId
    if (active !== null) setActiveProject(active)
    toast.success('Project deleted')
  }

  return (
    <section className="border border-border mb-6 p-4">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-base font-semibold">Projects ({projects.length})</h2>
        {canAddMembers && (
          <Button variant="primary" size="sm" onClick={() => setShowNew(true)}>
            <Plus className="w-3.5 h-3.5" />
            New project
          </Button>
        )}
      </div>
      <Table>
        <Thead>
          <Tr>
            <Th>Name</Th>
            <Th>Slug</Th>
            <Th className="w-24" />
          </Tr>
        </Thead>
        <Tbody>
          {projects.map((p) => (
            <Tr key={p.id}>
              <Td>
                {editingId === p.id ? (
                  <Input value={editName} onChange={(e) => setEditName(e.target.value)} className="max-w-xs" />
                ) : (
                  <span className="font-medium">{p.name}</span>
                )}
                {p.id === activeProjectId && <Badge variant="accent" className="ml-2">active</Badge>}
              </Td>
              <Td>
                <code className="text-xs font-mono text-muted">{p.slug}</code>
              </Td>
              <Td>
                {canAddMembers &&
                  (editingId === p.id ? (
                    <div className="flex gap-1">
                      <Button size="sm" variant="primary" onClick={() => saveRename(p.id)}>Save</Button>
                      <Button size="sm" variant="ghost" onClick={() => setEditingId(null)}>Cancel</Button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setMembersProject({ id: p.id, name: p.name })}
                        title="Manage members"
                        aria-label="Manage members"
                      >
                        <Users className="w-3.5 h-3.5" />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => startRename(p.id, p.name)}
                        title="Rename project"
                        aria-label="Rename project"
                      >
                        <Pencil className="w-3.5 h-3.5" />
                      </Button>
                      {projects.length > 1 && (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => handleDelete(p.id, p.name)}
                          title="Delete project"
                          aria-label="Delete project"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </Button>
                      )}
                    </div>
                  ))}
              </Td>
            </Tr>
          ))}
        </Tbody>
      </Table>
      <NewProjectDialog open={showNew} onClose={() => setShowNew(false)} />
      {membersProject && (
        <ProjectMembersDialog
          projectId={membersProject.id}
          projectName={membersProject.name}
          isOwner={isOwner}
          onClose={() => setMembersProject(null)}
        />
      )}
    </section>
  )
}

function AddUserSection({
  orgId,
  isOwner,
  onCreated,
}: {
  orgId: number
  isOwner: boolean
  onCreated: () => Promise<void>
}) {
  const toast = useToast()
  const projects = useAppStore((s) => s.projects)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<OrgRole>('member')
  const [grants, setGrants] = useState<Record<number, string>>({})
  const [saving, setSaving] = useState(false)

  const toggleProject = (projectId: number) => {
    setGrants((g) => {
      const next = { ...g }
      if (projectId in next) delete next[projectId]
      else next[projectId] = 'member'
      return next
    })
  }

  const setGrantRole = (projectId: number, grantRole: string) => {
    setGrants((g) => ({ ...g, [projectId]: grantRole }))
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setSaving(true)
    try {
      // Non-owners can only grant the 'member' role — the server forces
      // this anyway, but keep the request explicit.
      await api.organizations.createUser(orgId, {
        username: username.trim(),
        password,
        email: email.trim(),
        role: isOwner ? role : 'member',
        projects: Object.entries(grants).map(([id, grantRole]) => ({
          project_id: Number(id),
          role: isOwner ? grantRole : 'member',
        })),
      })
      toast.success('User created')
      setUsername('')
      setPassword('')
      setEmail('')
      setRole('member')
      setGrants({})
      await onCreated()
    } catch (err) {
      toast.error(String(err))
    } finally {
      setSaving(false)
    }
  }

  const orgRoleGrantsAll = role === 'admin' || role === 'owner'

  return (
    <section className="border border-border mb-6 p-4">
      <h2 className="text-base font-semibold mb-1">Add user</h2>
      <p className="text-sm text-muted mb-4">
        Creates the account directly: share the credentials with the new user.
      </p>
      <form onSubmit={submit} className="space-y-4">
        <div className="flex flex-col sm:flex-row gap-2 sm:items-end">
          <Input
            label="Username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            minLength={3}
            className="sm:max-w-xs"
          />
          <Input
            label="Email"
            type="email"
            placeholder="teammate@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            className="sm:max-w-xs"
          />
          <Input
            label="Password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
            className="sm:max-w-xs"
          />
          {isOwner ? (
            <Select
              label="Role"
              value={role}
              onValueChange={(v) => setRole(v as OrgRole)}
              options={ROLE_OPTIONS.filter((r) => r.value !== 'owner')}
              className="max-w-[140px]"
            />
          ) : (
            <p className="text-xs text-muted pb-2.5">Joins as member</p>
          )}
        </div>
        <div>
          <p className="text-sm font-medium mb-2">Project visibility</p>
          {orgRoleGrantsAll ? (
            <p className="text-xs text-muted">
              Org admins and owners already access every project.
            </p>
          ) : projects.length === 0 ? (
            <p className="text-xs text-muted">No projects in this organization.</p>
          ) : (
            <div className="space-y-1.5">
              {projects.map((p) => (
                <label key={p.id} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={p.id in grants}
                    onChange={() => toggleProject(p.id)}
                  />
                  <span className="flex-1">{p.name}</span>
                  {isOwner && p.id in grants && (
                    <Select
                      value={grants[p.id]}
                      onValueChange={(v) => setGrantRole(p.id, v)}
                      options={PROJECT_ROLE_OPTIONS}
                      className="max-w-[130px]"
                    />
                  )}
                </label>
              ))}
            </div>
          )}
        </div>
        <Button type="submit" variant="primary" loading={saving}>
          Create user
        </Button>
      </form>
    </section>
  )
}

const OUTCOME_OPTIONS = [
  { value: '', label: 'Any outcome' },
  { value: 'ok', label: 'ok' },
  { value: 'denied', label: 'denied' },
  { value: 'error', label: 'error' },
  { value: 'rate_limited', label: 'rate limited' },
]

const PAGE_SIZE = 50

function outcomeVariant(outcome: string): 'success' | 'warning' | 'danger' | 'muted' {
  if (outcome === 'ok') return 'success'
  if (outcome === 'denied' || outcome === 'rate_limited') return 'warning'
  if (outcome === 'error') return 'danger'
  return 'muted'
}

function ToolActivitySection({ orgId }: { orgId: number }) {
  const toast = useToast()
  const projects = useAppStore((s) => s.projects)
  const [calls, setCalls] = useState<ToolCall[]>([])
  const [total, setTotal] = useState(0)
  const [stats, setStats] = useState<ToolCallStats | null>(null)
  const [statsWindow, setStatsWindow] = useState<'24h' | '7d'>('24h')
  const [tool, setTool] = useState('')
  const [outcome, setOutcome] = useState('')
  const [principal, setPrincipal] = useState('')
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [list, agg] = await Promise.all([
        api.toolCalls.list(orgId, {
          limit: PAGE_SIZE,
          offset,
          tool: tool || undefined,
          outcome: outcome || undefined,
          principal: principal || undefined,
        }),
        api.toolCalls.stats(orgId, statsWindow),
      ])
      setCalls(list.calls)
      setTotal(list.total)
      setStats(agg)
    } catch (e) {
      toast.error(String(e))
    } finally {
      setLoading(false)
    }
  }, [orgId, offset, tool, outcome, principal, statsWindow, toast])

  useEffect(() => { void load() }, [load])

  const projectName = (id: number | null) =>
    id === null ? '—' : projects.find((p) => p.id === id)?.name ?? `#${id}`

  const fmtTime = (iso: string) =>
    new Date(iso).toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
    })

  const toolOptions = [
    { value: '', label: 'Any tool' },
    ...(stats?.by_tool ?? []).map((row) => ({ value: row.tool, label: row.tool })),
  ]

  const tiles = [
    { label: 'Calls', value: stats?.totals.calls ?? 0 },
    { label: 'Errors', value: stats?.totals.errors ?? 0 },
    { label: 'Denied', value: stats?.totals.denied ?? 0 },
    { label: 'Rate limited', value: stats?.totals.rate_limited ?? 0 },
  ]

  return (
    <section className="border border-border mb-6 p-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between mb-4">
        <div>
          <h2 className="text-base font-semibold">Tool activity</h2>
          <p className="text-sm text-muted">
            Every MCP tool call made against this organization, with who called it and how it ended.
          </p>
        </div>
        <div className="flex gap-1 self-start">
          {(['24h', '7d'] as const).map((w) => (
            <Button
              key={w}
              size="sm"
              variant={statsWindow === w ? 'primary' : 'ghost'}
              onClick={() => setStatsWindow(w)}
            >
              {w}
            </Button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
        {tiles.map((tile) => (
          <Card key={tile.label} className="p-3">
            <p className="text-xs uppercase tracking-wide text-muted">{tile.label}</p>
            <p className="text-xl font-semibold">{tile.value}</p>
          </Card>
        ))}
      </div>

      <div className="flex flex-col sm:flex-row gap-2 sm:items-end mb-4">
        <Select
          label="Tool"
          value={tool}
          onValueChange={(v) => { setOffset(0); setTool(v) }}
          options={toolOptions}
          className="sm:max-w-[220px]"
        />
        <Select
          label="Outcome"
          value={outcome}
          onValueChange={(v) => { setOffset(0); setOutcome(v) }}
          options={OUTCOME_OPTIONS}
          className="sm:max-w-[180px]"
        />
        <Input
          label="Principal"
          value={principal}
          onChange={(e) => { setOffset(0); setPrincipal(e.target.value) }}
          placeholder="ci-bot"
          className="sm:max-w-[200px]"
        />
      </div>

      {loading ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : calls.length === 0 ? (
        <p className="text-sm text-muted">No tool calls recorded yet.</p>
      ) : (
        <>
          {/* Mobile: stacked cards */}
          <div className="space-y-3 md:hidden">
            {calls.map((call) => (
              <Card key={call.id} className="p-3">
                <div className="flex items-start justify-between gap-2">
                  <code className="font-mono text-xs break-all">{call.tool}</code>
                  <Badge variant={outcomeVariant(call.outcome)}>{call.outcome}</Badge>
                </div>
                {call.error && <p className="text-xs text-danger mt-1 break-words">{call.error}</p>}
                <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-xs text-muted">
                  <span>{call.principal}</span>
                  <span>{projectName(call.project_id)}</span>
                  <span className="font-mono">{call.duration_ms} ms</span>
                  <span>{fmtTime(call.created_at)}</span>
                </div>
              </Card>
            ))}
          </div>

          {/* Desktop: table */}
          <Card className="hidden md:block">
            <Table>
              <Thead>
                <Tr>
                  <Th>Time</Th>
                  <Th>Principal</Th>
                  <Th>Tool</Th>
                  <Th>Project</Th>
                  <Th>Outcome</Th>
                  <Th>Duration</Th>
                </Tr>
              </Thead>
              <Tbody>
                {calls.map((call) => (
                  <Tr key={call.id}>
                    <Td className="text-xs text-muted whitespace-nowrap">{fmtTime(call.created_at)}</Td>
                    <Td className="text-xs">
                      {call.principal}
                      <span className="text-muted ml-1">({call.principal_kind})</span>
                    </Td>
                    <Td><code className="font-mono text-xs">{call.tool}</code></Td>
                    <Td className="text-xs text-muted">{projectName(call.project_id)}</Td>
                    <Td>
                      <Badge variant={outcomeVariant(call.outcome)}>{call.outcome}</Badge>
                      {call.error && (
                        <p className="text-xs text-danger mt-1 max-w-xs truncate">{call.error}</p>
                      )}
                    </Td>
                    <Td className="text-xs text-muted font-mono">{call.duration_ms} ms</Td>
                  </Tr>
                ))}
              </Tbody>
            </Table>
          </Card>

          <div className="flex items-center gap-2 mt-3">
            <Button
              size="sm"
              variant="ghost"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              Previous
            </Button>
            <span className="text-xs text-muted">
              {offset + 1}–{Math.min(offset + calls.length, total)} of {total}
            </span>
            <Button
              size="sm"
              variant="ghost"
              disabled={offset + PAGE_SIZE >= total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next
            </Button>
          </div>
        </>
      )}
    </section>
  )
}

export default function Organization() {
  const confirm = useConfirm()
  const toast = useToast()
  const currentUser = useAppStore((s) => s.currentUser)
  const organizations = useAppStore((s) => s.organizations)
  const activeOrgId = useAppStore((s) => s.activeOrgId)
  const loadIdentity = useAppStore((s) => s.loadIdentity)

  const activeOrg = organizations.find((o) => o.id === activeOrgId) || null
  const myRole: OrgRole = activeOrg?.role ?? 'viewer'
  const isOwner = myRole === 'owner'
  const canAddMembers = ROLE_RANK[myRole] >= ROLE_RANK.admin

  const [members, setMembers] = useState<OrgMember[]>([])
  const [error, setError] = useState<string | null>(null)
  const [orgName, setOrgName] = useState('')
  const [tab, setTab] = useState('overview')

  const refresh = useCallback(async () => {
    if (!activeOrgId) return
    setError(null)
    try {
      const m = await api.organizations.members(activeOrgId)
      setMembers(m.members)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load organization')
    }
  }, [activeOrgId])

  useEffect(() => {
    refresh()
    setOrgName(activeOrg?.name ?? '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeOrgId])

  const handleRoleChange = async (userId: number, role: OrgRole) => {
    if (!activeOrgId) return
    try {
      await api.organizations.updateMember(activeOrgId, userId, role)
      toast.success('Role updated')
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update role')
    }
  }

  const handleRemove = async (userId: number) => {
    if (!activeOrgId) return
    const isSelf = userId === currentUser?.id
    const ok = await confirm({
      title: isSelf ? 'Leave organization' : 'Remove member',
      message: isSelf
        ? 'Leave this organization? You will lose access to its resources.'
        : 'Remove this member from the organization?',
      confirmLabel: isSelf ? 'Leave' : 'Remove',
      danger: true,
      onConfirm: () => api.organizations.removeMember(activeOrgId, userId),
    })
    if (!ok) return
    toast.success(isSelf ? 'You left the organization' : 'Member removed')
    await refresh()
  }

  const handleRename = async (e: FormEvent) => {
    e.preventDefault()
    if (!activeOrgId || !orgName.trim()) return
    try {
      await api.organizations.update(activeOrgId, orgName.trim())
      toast.success('Organization renamed')
      await loadIdentity()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to rename organization')
    }
  }

  if (!activeOrg) {
    return (
      <div className="p-4 sm:p-8">
        <div className="page-wide">
          <h1>Organization</h1>
          <p className="text-muted text-sm">No active organization.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="p-4 sm:p-8">
      <div className="page-wide">
        <div className="mb-6">
          <h1>Organization</h1>
          <p className="text-muted text-sm">
            {activeOrg.name} · namespace <code className="font-mono">{activeOrg.memory_namespace}</code> · your role{' '}
            {roleBadge(myRole)}
          </p>
        </div>

        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value="overview">Overview</TabsTrigger>
            {canAddMembers && <TabsTrigger value="activity">Tool activity</TabsTrigger>}
          </TabsList>

          <TabsContent value="overview">
            <ProjectsSection canAddMembers={canAddMembers} isOwner={isOwner} />

            {error && <Banner variant="danger" className="mb-4">{error}</Banner>}

            {isOwner && (
              <section className="border border-border mb-6 p-4">
                <h2 className="text-base font-semibold mb-4">Settings</h2>
                <form onSubmit={handleRename} className="flex flex-col sm:flex-row gap-2 sm:items-end">
                  <Input
                    label="Organization name"
                    value={orgName}
                    onChange={(e) => setOrgName(e.target.value)}
                    className="sm:max-w-xs"
                  />
                  <Button type="submit" variant="secondary">
                    Save
                  </Button>
                </form>
              </section>
            )}

            {/* Members */}
            <section className="border border-border mb-6 p-4">
              <h2 className="text-base font-semibold mb-4">Members ({members.length})</h2>
              <Table>
                <Thead>
                  <Tr>
                    <Th>User</Th>
                    {isOwner && <Th>Role</Th>}
                    <Th className="w-10" />
                  </Tr>
                </Thead>
                <Tbody>
                  {members.map((m) => {
                    const isSelf = m.user_id === currentUser?.id
                    return (
                      <Tr key={m.user_id}>
                        <Td>
                          <span className="font-medium">{m.username}</span>
                          {isSelf && <span className="text-muted text-xs ml-1">(you)</span>}
                          {m.email && <div className="text-xs text-muted">{m.email}</div>}
                        </Td>
                        {isOwner && (
                          <Td>
                            {!isSelf ? (
                              <Select
                                value={m.role}
                                onValueChange={(v) => handleRoleChange(m.user_id, v as OrgRole)}
                                options={ROLE_OPTIONS}
                                className="max-w-[140px]"
                              />
                            ) : (
                              m.role && roleBadge(m.role)
                            )}
                          </Td>
                        )}
                        <Td>
                          {(isOwner || isSelf) && (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => handleRemove(m.user_id)}
                              title={isSelf ? 'Leave organization' : 'Remove member'}
                              aria-label={isSelf ? 'Leave organization' : 'Remove member'}
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </Button>
                          )}
                        </Td>
                      </Tr>
                    )
                  })}
                </Tbody>
              </Table>
            </section>

            {canAddMembers && activeOrgId && (
              <AddUserSection orgId={activeOrgId} isOwner={isOwner} onCreated={refresh} />
            )}

            {isOwner && activeOrgId && (
              <McpPermissionsSection orgId={activeOrgId} myRole={myRole} />
            )}
          </TabsContent>

          {canAddMembers && activeOrgId && (
            <TabsContent value="activity">
              <ToolActivitySection orgId={activeOrgId} />
            </TabsContent>
          )}
        </Tabs>
      </div>
    </div>
  )
}
