import { useState } from 'react'
import { api, setAuthToken } from '../lib/api'
import { Button } from '../components/ui'
import { Input } from '../components/ui'

type LoginProps = { onLoggedIn: () => void }

export default function Login({ onLoggedIn }: LoginProps) {
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

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
        <h1 className="text-xl font-semibold mb-1">context-forge</h1>
        <p className="text-sm text-muted mb-6">Admin login</p>

        {error && (
          <div
            style={{ border: '1px solid var(--danger)', color: 'var(--danger)' }}
            className="text-sm p-3 mb-4 bg-[#fef2f2]"
          >
            {error}
          </div>
        )}

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
      </div>
    </div>
  )
}
