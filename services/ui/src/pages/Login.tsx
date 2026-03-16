import { useState } from 'react'
import { Lock, Loader2 } from 'lucide-react'
import { api, setAuthToken } from '../lib/api'

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
    <div className="min-h-screen bg-gray-950 text-gray-100 flex items-center justify-center p-6">
      <div className="w-full max-w-md bg-gray-900 border border-gray-800 rounded-2xl p-6 space-y-4">
        <h1 className="text-xl font-semibold text-white inline-flex items-center gap-2">
          <Lock className="w-5 h-5 text-indigo-400" />
          Admin Login
        </h1>
        {error && <div className="text-sm text-red-300 bg-red-500/10 border border-red-500/30 rounded-lg p-3">{error}</div>}
        <input
          value={username}
          onChange={e => setUsername(e.target.value)}
          placeholder="Username"
          className="w-full px-3 py-2 text-sm bg-gray-950 border border-gray-700 rounded-lg"
        />
        <input
          type="password"
          value={password}
          onChange={e => setPassword(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleLogin()}
          placeholder="Password"
          className="w-full px-3 py-2 text-sm bg-gray-950 border border-gray-700 rounded-lg"
        />
        <button
          onClick={handleLogin}
          disabled={loading || !username || !password}
          className="w-full px-4 py-2 text-sm text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg disabled:opacity-50 inline-flex items-center justify-center gap-2"
        >
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
          Login
        </button>
      </div>
    </div>
  )
}

