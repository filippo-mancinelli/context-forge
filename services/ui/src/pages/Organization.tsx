import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Trash2, Copy, Check } from 'lucide-react'
import {
  api,
  type OrgMember,
  type OrgInvitation,
  type OrgRole,
} from '../lib/api'
import { useAppStore } from '../store'
import { Button, Input, Select, Badge, Table, Thead, Tbody, Tr, Th, Td } from '../components/ui'

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

export default function Organization() {
  const currentUser = useAppStore((s) => s.currentUser)
  const organizations = useAppStore((s) => s.organizations)
  const activeOrgId = useAppStore((s) => s.activeOrgId)
  const loadIdentity = useAppStore((s) => s.loadIdentity)

  const activeOrg = organizations.find((o) => o.id === activeOrgId) || null
  const myRole: OrgRole = activeOrg?.role ?? 'viewer'
  const canManage = ROLE_RANK[myRole] >= ROLE_RANK.admin

  const [members, setMembers] = useState<OrgMember[]>([])
  const [invitations, setInvitations] = useState<OrgInvitation[]>([])
  const [error, setError] = useState<string | null>(null)
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteRole, setInviteRole] = useState<OrgRole>('member')
  const [inviting, setInviting] = useState(false)
  const [inviteLink, setInviteLink] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [orgName, setOrgName] = useState('')

  const refresh = useCallback(async () => {
    if (!activeOrgId) return
    setError(null)
    try {
      const m = await api.organizations.members(activeOrgId)
      setMembers(m.members)
      if (canManage) {
        const inv = await api.organizations.invitations(activeOrgId)
        setInvitations(inv.invitations)
      } else {
        setInvitations([])
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load organization')
    }
  }, [activeOrgId, canManage])

  useEffect(() => {
    refresh()
    setOrgName(activeOrg?.name ?? '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeOrgId])

  const handleInvite = async (e: FormEvent) => {
    e.preventDefault()
    if (!activeOrgId) return
    setInviting(true)
    setError(null)
    setInviteLink(null)
    try {
      const res = await api.organizations.invite(activeOrgId, inviteEmail.trim(), inviteRole)
      setInviteEmail('')
      if (res.added_existing_user) {
        await refresh()
      } else if (res.invite_token) {
        const link = `${window.location.origin}/invite/${res.invite_token}`
        setInviteLink(link)
        await refresh()
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to invite')
    } finally {
      setInviting(false)
    }
  }

  const handleRoleChange = async (userId: number, role: OrgRole) => {
    if (!activeOrgId) return
    try {
      await api.organizations.updateMember(activeOrgId, userId, role)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update role')
    }
  }

  const handleRemove = async (userId: number) => {
    if (!activeOrgId) return
    try {
      await api.organizations.removeMember(activeOrgId, userId)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to remove member')
    }
  }

  const handleRevoke = async (invitationId: number) => {
    if (!activeOrgId) return
    try {
      await api.organizations.revokeInvite(activeOrgId, invitationId)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to revoke invite')
    }
  }

  const handleRename = async (e: FormEvent) => {
    e.preventDefault()
    if (!activeOrgId || !orgName.trim()) return
    try {
      await api.organizations.update(activeOrgId, orgName.trim())
      await loadIdentity()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to rename organization')
    }
  }

  const copyLink = async () => {
    if (!inviteLink) return
    await navigator.clipboard.writeText(inviteLink)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  if (!activeOrg) {
    return (
      <div className="p-4 sm:p-8">
        <div className="page-content">
          <h1>Organization</h1>
          <p className="text-muted text-sm">No active organization.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="p-4 sm:p-8">
      <div className="page-content">
        <div className="mb-6">
          <h1>Organization</h1>
          <p className="text-muted text-sm">
            {activeOrg.name} · namespace <code className="font-mono">{activeOrg.memory_namespace}</code> · your role{' '}
            {roleBadge(myRole)}
          </p>
        </div>

        {error && (
          <div
            style={{ border: '1px solid var(--danger)', color: 'var(--danger)' }}
            className="text-sm p-3 mb-4 bg-[#fef2f2]"
          >
            {error}
          </div>
        )}

        {canManage && (
          <section style={{ border: '1px solid var(--border)' }} className="mb-6 p-4">
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
        <section style={{ border: '1px solid var(--border)' }} className="mb-6 p-4">
          <h2 className="text-base font-semibold mb-4">Members ({members.length})</h2>
          <Table>
            <Thead>
              <Tr>
                <Th>User</Th>
                <Th>Role</Th>
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
                    <Td>
                      {canManage && !isSelf ? (
                        <Select
                          value={m.role}
                          onValueChange={(v) => handleRoleChange(m.user_id, v as OrgRole)}
                          options={ROLE_OPTIONS}
                          className="max-w-[140px]"
                        />
                      ) : (
                        roleBadge(m.role)
                      )}
                    </Td>
                    <Td>
                      {(canManage || isSelf) && (
                        <button
                          onClick={() => handleRemove(m.user_id)}
                          className="text-muted hover:text-danger transition-colors"
                          title={isSelf ? 'Leave organization' : 'Remove member'}
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </Td>
                  </Tr>
                )
              })}
            </Tbody>
          </Table>
        </section>

        {/* Invitations */}
        {canManage && (
          <section style={{ border: '1px solid var(--border)' }} className="mb-6 p-4">
            <h2 className="text-base font-semibold mb-4">Invite a member</h2>
            <form onSubmit={handleInvite} className="flex flex-col sm:flex-row gap-2 sm:items-end">
              <Input
                label="Email"
                type="email"
                placeholder="teammate@example.com"
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                required
                className="sm:max-w-xs"
              />
              <Select
                label="Role"
                value={inviteRole}
                onValueChange={(v) => setInviteRole(v as OrgRole)}
                options={ROLE_OPTIONS.filter((r) => r.value !== 'owner')}
                className="max-w-[140px]"
              />
              <Button type="submit" variant="primary" loading={inviting}>
                Invite
              </Button>
            </form>

            {inviteLink && (
              <div className="mt-4 p-3 bg-surface border border-border rounded">
                <p className="text-xs text-muted mb-1">
                  Share this invite link (no email is sent in self-hosted mode):
                </p>
                <div className="flex items-center gap-2">
                  <code className="text-xs font-mono break-all flex-1">{inviteLink}</code>
                  <Button size="sm" variant="ghost" onClick={copyLink}>
                    {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                  </Button>
                </div>
              </div>
            )}

            {invitations.length > 0 && (
              <div className="mt-4">
                <h3 className="text-sm font-medium mb-2">Pending invitations</h3>
                <Table>
                  <Thead>
                    <Tr>
                      <Th>Email</Th>
                      <Th>Role</Th>
                      <Th className="w-10" />
                    </Tr>
                  </Thead>
                  <Tbody>
                    {invitations.map((inv) => (
                      <Tr key={inv.id}>
                        <Td>{inv.email}</Td>
                        <Td>{roleBadge(inv.role)}</Td>
                        <Td>
                          <button
                            onClick={() => handleRevoke(inv.id)}
                            className="text-muted hover:text-danger transition-colors"
                            title="Revoke invitation"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </Td>
                      </Tr>
                    ))}
                  </Tbody>
                </Table>
              </div>
            )}
          </section>
        )}
      </div>
    </div>
  )
}
