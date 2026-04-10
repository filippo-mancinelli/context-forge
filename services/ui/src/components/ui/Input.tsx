import { forwardRef, type InputHTMLAttributes, type TextareaHTMLAttributes } from 'react'

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string
  error?: string
  hint?: string
}

const inputBase =
  'w-full px-3 py-1.5 text-sm bg-bg text-text border border-border rounded transition-colors duration-100 placeholder:text-muted focus:outline-none focus:border-accent disabled:bg-surface disabled:text-muted disabled:cursor-not-allowed'

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ label, error, hint, className = '', id, ...props }, ref) => {
    return (
      <div className="flex flex-col gap-1">
        {label && (
          <label htmlFor={id} className="text-sm font-medium text-text">
            {label}
          </label>
        )}
        <input
          ref={ref}
          id={id}
          className={[inputBase, error ? 'border-danger' : '', className].join(' ')}
          {...props}
        />
        {error && <span className="text-xs text-danger">{error}</span>}
        {hint && !error && <span className="text-xs text-muted">{hint}</span>}
      </div>
    )
  }
)

Input.displayName = 'Input'

interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string
  error?: string
  hint?: string
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ label, error, hint, className = '', id, ...props }, ref) => {
    return (
      <div className="flex flex-col gap-1">
        {label && (
          <label htmlFor={id} className="text-sm font-medium text-text">
            {label}
          </label>
        )}
        <textarea
          ref={ref}
          id={id}
          className={[
            inputBase,
            'resize-y min-h-[80px]',
            error ? 'border-danger' : '',
            className,
          ].join(' ')}
          {...props}
        />
        {error && <span className="text-xs text-danger">{error}</span>}
        {hint && !error && <span className="text-xs text-muted">{hint}</span>}
      </div>
    )
  }
)

Textarea.displayName = 'Textarea'
