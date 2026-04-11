import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../lib/api'
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
    // Check if user is authenticated
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

      // Call authorize endpoint
      const response = await fetch(`${api.defaults.baseURL}/mcp/oauth/authorize?${new URLSearchParams({
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
          // Need to login first
          navigate('/login', { replace: true })
          return
        }
        throw new Error(data.detail || 'Authorization failed')
      }

      // Redirect with authorization code
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
    // Redirect back with error
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
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-center">
          <h1 className="text-2xl font-bold mb-4">Authentication Required</h1>
          <p className="text-muted mb-6">Please log in to authorize this application</p>
          <Button onClick={() => navigate('/login')}>Go to Login</Button>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-surface p-4">
      <div className="max-w-md w-full bg-background border rounded-lg p-6 shadow-lg">
        <div className="text-center mb-6">
          <div className="w-16 h-16 bg-primary/10 rounded-full flex items-center justify-center mx-auto mb-4">
            <svg className="w-8 h-8 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
            </svg>
          </div>
          <h1 className="text-2xl font-bold mb-2">Authorize Application</h1>
          <p className="text-muted text-sm">
            An application wants to access your context-forge account
          </p>
        </div>

        <div className="space-y-4 mb-6">
          <div className="bg-surface rounded-lg p-4">
            <div className="text-sm text-muted mb-1">Application</div>
            <div className="font-medium">{params.client_id || 'Unknown'}</div>
          </div>

          <div className="bg-surface rounded-lg p-4">
            <div className="text-sm text-muted mb-1">Permissions Requested</div>
            <div className="text-sm">
              {params.scope?.split(',').map((s, i) => (
                <div key={i} className="flex items-center gap-2 mb-1">
                  <svg className="w-4 h-4 text-success" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                  <span className="capitalize">{s.trim()}</span>
                </div>
              ))}
            </div>
          </div>

          {error && (
            <div className="bg-destructive/10 text-destructive text-sm p-3 rounded-lg">
              {error}
            </div>
          )}
        </div>

        <div className="flex gap-3">
          <Button
            variant="secondary"
            className="flex-1"
            onClick={handleCancel}
            disabled={loading}
          >
            Cancel
          </Button>
          <Button
            variant="primary"
            className="flex-1"
            onClick={handleAuthorize}
            disabled={loading}
          >
            {loading ? 'Authorizing...' : 'Authorize'}
          </Button>
        </div>

        <p className="text-xs text-muted text-center mt-4">
          By authorizing, you allow this application to access your context-forge data
        </p>
      </div>
    </div>
  )
}
