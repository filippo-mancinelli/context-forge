import { useEffect, useState } from 'react'
import { MessagesSquare } from 'lucide-react'
import { api, type SharedChatResponse } from '../lib/api'
import { Spinner } from '../components/ui'
import { Transcript } from '../components/ChatTranscript'

/** Public, read-only view of a shared agent-chat snapshot. No auth required. */
export default function SharedChat({ token }: { token: string }) {
  const [data, setData] = useState<SharedChatResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.chat
      .shared(token)
      .then(setData)
      .catch(() =>
        setError('This shared chat does not exist or its link has been revoked.')
      )
  }, [token])

  return (
    <div className="min-h-screen flex flex-col bg-bg">
      <header
        style={{ borderBottom: '1px solid var(--border)' }}
        className="px-4 sm:px-8 py-3 bg-surface"
      >
        <div className="page-content mx-auto flex items-center justify-between gap-4">
          <span className="font-semibold text-text text-sm tracking-tight">context-forge</span>
          <span className="text-xs text-muted flex items-center gap-1.5">
            <MessagesSquare className="w-3.5 h-3.5" />
            Shared chat
          </span>
        </div>
      </header>

      <main className="flex-1 px-4 sm:px-8 py-6">
        <div className="page-content mx-auto">
          {error ? (
            <div
              style={{ border: '1px solid var(--border)' }}
              className="rounded bg-surface p-6 text-center text-sm text-muted"
            >
              {error}
            </div>
          ) : data === null ? (
            <div className="flex items-center justify-center gap-2 text-sm text-muted py-16">
              <Spinner size={14} />
              Loading conversation…
            </div>
          ) : (
            <>
              <div className="mb-6">
                <h1 style={{ fontSize: '1.25rem', margin: 0 }}>{data.title}</h1>
                {data.shared_at && (
                  <p className="text-xs text-muted mt-1">
                    Snapshot shared on {new Date(data.shared_at).toLocaleString()}
                  </p>
                )}
              </div>
              <Transcript turns={data.turns} />
              <p
                style={{ borderTop: '1px solid var(--border)' }}
                className="text-[11px] text-muted mt-8 pt-4"
              >
                Read-only snapshot of an agent-chat conversation from a self-hosted{' '}
                <span className="font-mono">context-forge</span> instance. Retrieval traces
                (repositories, memory, knowledge base, databases) are shown as they ran.
              </p>
            </>
          )}
        </div>
      </main>
    </div>
  )
}
