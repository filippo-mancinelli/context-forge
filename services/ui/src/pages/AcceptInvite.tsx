import { useEffect, useState } from 'react'
import { api, setAuthToken } from '../lib/api'
import { Button, Input, Badge } from '../components/ui'

type AcceptInviteProps = { token: string; onAccepted: () => void }

export default function AcceptInvite({ token, onAccepted }: AcceptInviteProps) {
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [invite, setInvite] = useState<{ email: string; role: string; org_name: string } | null>(null)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')

  useEffect(() => {
    let active = true
    api.invitations
      .preview(token)
      .then((res) => {
        if (active) setInvite(res)
      })
      .catch((e) => {
        if (active) setError(e instanceof Error ? e.message : 'Invitation not found or expired')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [token])

  const handleAccept = async () => {
    setSubmitting(true)
    setError(null)
    try {
      const res = await api.invitations.accept(token, username.trim(), password)
      setAuthToken(res.token)
      onAccepted()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to accept invitation')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      style={{ minHeight: '100vh', background: 'var(--surface)' }}
      className="flex items-center justify-center p-6"
    >
      <div style={{ maxWidth: '380px', border: '1px solid var(--border)' }} className="w-full bg-bg p-8">
        <h1 className="text-xl font-semibold mb-1">context-forge</h1>

        {loading ? (
          <p className="text-sm text-muted">Loading invitation…</p>
        ) : !invite ? (
          <>
            <p className="text-sm text-muted mb-4">Invitation</p>
            <div
              style={{ border: '1px solid var(--danger)', color: 'var(--danger)' }}
              className="text-sm p-3 bg-[#fef2f2]"
            >
              {error || 'Invitation not found or expired.'}
            </div>
            <Button variant="secondary" className="w-full justify-center mt-4" onClick={onAccepted}>
              Go to login
            </Button>
          </>
        ) : (
          <>
            <p className="text-sm text-muted mb-6">
              Join <span className="font-medium text-text">{invite.org_name}</span> as{' '}
              <Badge variant="accent">{invite.role}</Badge>
            </p>

            {error && (
              <div
                style={{ border: '1px solid var(--danger)', color: 'var(--danger)' }}
                className="text-sm p-3 mb-4 bg-[#fef2f2]"
              >
                {error}
              </div>
            )}

            <div className="space-y-4">
              <Input label="Email" value={invite.email} disabled />
              <Input
                id="username"
                label="Choose a username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="your-username"
                autoComplete="username"
              />
              <Input
                id="password"
                label="Choose a password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && username && password.length >= 8 && handleAccept()}
                placeholder="••••••••"
                hint="At least 8 characters"
                autoComplete="new-password"
              />
              <Button
                variant="primary"
                className="w-full justify-center mt-2"
                onClick={handleAccept}
                disabled={submitting || username.length < 3 || password.length < 8}
                loading={submitting}
              >
                Accept invitation
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
