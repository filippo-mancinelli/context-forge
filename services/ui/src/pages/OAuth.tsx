import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Button } from '../components/ui'

interface OAuthParams {
  client_id?: string
  redirect_uri?: string
  response_type?: string
  scope?: string
  state?: string
}

export default function OAuth() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [isAuthenticated, setIsAuthenticated] = useState(false)

  const params: OAuthParams = {
    client_id: searchParams.get('client_id') || undefined,
    redirect_uri: searchParams.get('redirect_uri') || undefined,
    response_type: searchParams.get('response_type') || undefined,
    scope: searchParams.get('scope') || undefined,
    state: searchParams.get('state') || undefined,
  }

  useEffect(() => {
    const token = localStorage.getItem('auth_token')
    setIsAuthenticated(!!token)
  }, [])

  const handleAuthorize = async () => {
    if (!params.client_id || !params.redirect_uri) {
      setError('Missing required parameters')
      return
    }

    setLoading(true)
    setError(null)

    try {
      const token = localStorage.getItem('auth_token')
      if (!token) {
        navigate('/login', { replace: true })
        return
      }

      const baseUrl = import.meta.env.VITE_API_URL || ''
      const response = await fetch(`${baseUrl}/mcp/oauth/authorize?${new URLSearchParams({
        client_id: params.client_id,
        redirect_uri: params.redirect_uri,
        response_type: params.response_type || 'code',
        scope: params.scope || 'read,write',
        state: params.state || '',
      })}`, {
        headers: {
          'Authorization': `Bearer ${token}`,
        },
      })

      const data = await response.json()

      if (!response.ok) {
        if (response.status === 401 && data.error === 'login_required') {
          navigate('/login', { replace: true })
          return
        }
        throw new Error(data.detail || 'Authorization failed')
      }

      if (data.authorization_url) {
        window.location.href = data.authorization_url
      } else {
        throw new Error('Invalid response from server')
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  const handleCancel = () => {
    if (params.redirect_uri) {
      const url = new URL(params.redirect_uri)
      url.searchParams.set('error', 'access_denied')
      if (params.state) {
        url.searchParams.set('state', params.state)
      }
      window.location.href = url.toString()
    } else {
      navigate('/', { replace: true })
    }
  }

  if (!isAuthenticated) {
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <div className="text-center">
          <h1 className="text-2xl font-semibold mb-4">Authentication Required</h1>
          <p className="text-muted text-sm mb-6">Please log in to authorize this application</p>
          <Button onClick={() => navigate('/login')}>Go to Login</Button>
        </div>
      </div>
    )
  }

  return (
    <div
      style={{ minHeight: '100vh', background: 'var(--surface)' }}
      className="flex items-center justify-center p-4"
    >
      <div
        style={{ maxWidth: '420px', border: '1px solid var(--border)' }}
        className="w-full bg-bg p-6 sm:p-8"
      >
        <div className="text-center mb-6">
          <div
            style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
            className="w-14 h-14 rounded-full flex items-center justify-center mx-auto mb-4"
          >
            <svg className="w-7 h-7 text-accent" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
            </svg>
          </div>
          <h1 className="text-xl font-semibold mb-1">Authorize Application</h1>
          <p className="text-muted text-sm">
            An application wants to access your ContextForge account
          </p>
        </div>

        <div className="space-y-3 mb-6">
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)' }} className="p-3">
            <div className="text-xs text-muted mb-0.5 uppercase tracking-wide font-medium">Application</div>
            <div className="text-sm font-medium">{params.client_id || 'Unknown'}</div>
          </div>

          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)' }} className="p-3">
            <div className="text-xs text-muted mb-1.5 uppercase tracking-wide font-medium">Permissions Requested</div>
            <div className="text-sm space-y-1">
              {params.scope?.split(',').map((s, i) => (
                <div key={i} className="flex items-center gap-2">
                  <svg className="w-3.5 h-3.5 flex-shrink-0" style={{ color: 'var(--success)' }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                  <span className="capitalize">{s.trim()}</span>
                </div>
              ))}
            </div>
          </div>

          {error && (
            <div
              style={{ border: '1px solid var(--danger)', color: 'var(--danger)' }}
              className="text-sm p-3 bg-[#fef2f2]"
            >
              {error}
            </div>
          )}
        </div>

        <div className="flex gap-3">
          <Button
            variant="secondary"
            className="flex-1 justify-center"
            onClick={handleCancel}
            disabled={loading}
          >
            Cancel
          </Button>
          <Button
            variant="primary"
            className="flex-1 justify-center"
            onClick={handleAuthorize}
            disabled={loading}
            loading={loading}
          >
            Authorize
          </Button>
        </div>

        <p className="text-xs text-muted text-center mt-4">
          By authorizing, you allow this application to access your ContextForge data
        </p>
      </div>
    </div>
  )
}
