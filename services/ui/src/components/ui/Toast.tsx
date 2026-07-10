import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react'
import { AlertCircle, Check, X } from 'lucide-react'

type ToastKind = 'success' | 'error'

interface ToastItem {
  id: number
  kind: ToastKind
  message: string
}

interface ToastApi {
  success: (message: string) => void
  error: (message: string) => void
}

const ToastContext = createContext<ToastApi | null>(null)

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within <ToastProvider>')
  return ctx
}

const KIND_STYLE: Record<ToastKind, { color: string; bg: string }> = {
  success: { color: 'var(--success)', bg: '#eafaf1' },
  error: { color: 'var(--danger)', bg: '#fef2f2' },
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const nextId = useRef(1)

  const dismiss = useCallback((id: number) => {
    setToasts(ts => ts.filter(t => t.id !== id))
  }, [])

  const push = useCallback(
    (kind: ToastKind, message: string) => {
      const id = nextId.current++
      setToasts(ts => [...ts.slice(-4), { id, kind, message }])
      window.setTimeout(() => dismiss(id), kind === 'error' ? 6000 : 3500)
    },
    [dismiss]
  )

  const api = useMemo<ToastApi>(
    () => ({
      success: m => push('success', m),
      error: m => push('error', m),
    }),
    [push]
  )

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="fixed bottom-4 right-4 z-[60] flex flex-col gap-2 items-end pointer-events-none">
        {toasts.map(t => (
          <div
            key={t.id}
            style={{
              border: `1px solid ${KIND_STYLE[t.kind].color}`,
              color: KIND_STYLE[t.kind].color,
              background: KIND_STYLE[t.kind].bg,
            }}
            className="text-sm px-3 py-2 flex items-start gap-2 shadow-md min-w-[240px] max-w-[380px] pointer-events-auto animate-in fade-in-0 slide-in-from-bottom-2"
          >
            {t.kind === 'success' ? (
              <Check className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
            ) : (
              <AlertCircle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
            )}
            <span className="flex-1 break-words">{t.message}</span>
            <button
              type="button"
              onClick={() => dismiss(t.id)}
              className="mt-0.5 flex-shrink-0 opacity-60 hover:opacity-100"
              aria-label="Dismiss"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}
