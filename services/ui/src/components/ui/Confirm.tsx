import { createContext, useCallback, useContext, useState, type ReactNode } from 'react'
import { Button } from './Button'
import { Dialog, DialogFooter } from './Dialog'
import { useToast } from './Toast'

export interface ConfirmOptions {
  title: string
  message?: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
  /**
   * Optional async action executed while the dialog stays open with a spinner
   * on the confirm button. If it throws, the error is shown as a toast and
   * the confirm() promise resolves false.
   */
  onConfirm?: () => Promise<unknown> | unknown
}

type ConfirmFn = (opts: ConfirmOptions) => Promise<boolean>

const ConfirmContext = createContext<ConfirmFn | null>(null)

export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmContext)
  if (!ctx) throw new Error('useConfirm must be used within <ConfirmProvider>')
  return ctx
}

interface PendingConfirm {
  opts: ConfirmOptions
  resolve: (confirmed: boolean) => void
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const toast = useToast()
  const [pending, setPending] = useState<PendingConfirm | null>(null)
  const [running, setRunning] = useState(false)

  const confirm = useCallback<ConfirmFn>(opts => {
    return new Promise<boolean>(resolve => {
      setPending({ opts, resolve })
    })
  }, [])

  const close = (confirmed: boolean) => {
    pending?.resolve(confirmed)
    setPending(null)
    setRunning(false)
  }

  const handleConfirm = async () => {
    if (!pending) return
    const { onConfirm } = pending.opts
    if (!onConfirm) {
      close(true)
      return
    }
    setRunning(true)
    try {
      await onConfirm()
      close(true)
    } catch (e) {
      toast.error(String(e))
      close(false)
    }
  }

  const opts = pending?.opts

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <Dialog
        open={!!pending}
        onOpenChange={o => {
          if (!o && !running) close(false)
        }}
        title={opts?.title ?? ''}
      >
        {opts?.message && <div className="text-sm text-text break-words">{opts.message}</div>}
        <DialogFooter>
          <Button variant="ghost" onClick={() => close(false)} disabled={running}>
            {opts?.cancelLabel ?? 'Cancel'}
          </Button>
          <Button
            variant={opts?.danger ? 'danger' : 'primary'}
            onClick={handleConfirm}
            loading={running}
            disabled={running}
          >
            {opts?.confirmLabel ?? 'Confirm'}
          </Button>
        </DialogFooter>
      </Dialog>
    </ConfirmContext.Provider>
  )
}
