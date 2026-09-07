import { useEffect, useState } from 'react'
import { api, setAuthToken, BASE } from '../lib/api'
import { Banner, Button } from '../components/ui'
import { Input } from '../components/ui'
import { Logo } from '../components/Logo'

type LoginProps = { onLoggedIn: () => void }

const oidcLoginUrl = `${BASE}/api/auth/oidc/login`

export default function Login({ onLoggedIn }: LoginProps) {
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // null = still checking; true = OIDC enabled (redirecting to the provider);
  // false = no OIDC configured, show the local credentials form.
  const [oidcEnabled, setOidcEnabled] = useState<boolean | null>(null)

  useEffect(() => {
    fetch(`${BASE}/api/auth/oidc/config`)
      .then(r => r.json())
      .then(d => {
        if (d.enabled) {
          // With OIDC active the form is skipped: go straight to the provider.
          window.location.href = oidcLoginUrl
        } else {
          setOidcEnabled(false)
        }
      })
      .catch(() => setOidcEnabled(false))
  }, [])

  const handleLogin = async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await api.auth.login(username.trim(), password)
      setAuthToken(result.token)
      onLoggedIn()
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      style={{ minHeight: '100vh', background: 'var(--surface)' }}
      className="flex items-center justify-center p-6"
    >
      <div
        style={{ maxWidth: '360px', border: '1px solid var(--border)' }}
        className="w-full bg-bg p-8"
      >
        <div className="mb-1">
          <Logo size={26} />
        </div>

        {oidcEnabled === false ? (
          <>
            <p className="text-sm text-muted mb-6">Admin login</p>

            {error && <Banner variant="danger" className="mb-4">{error}</Banner>}

            <div className="space-y-4">
              <Input
                id="username"
                label="Username"
                value={username}
                onChange={e => setUsername(e.target.value)}
                placeholder="admin"
                autoComplete="username"
              />
              <Input
                id="password"
                label="Password"
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleLogin()}
                placeholder="••••••••"
                autoComplete="current-password"
              />
              <Button
                variant="primary"
                className="w-full justify-center mt-2"
                onClick={handleLogin}
                disabled={loading || !username || !password}
                loading={loading}
              >
                Sign in
              </Button>
            </div>
          </>
        ) : (
          <p className="text-sm text-muted text-center py-6">Redirecting to your identity provider…</p>
        )}
      </div>
    </div>
  )
}
