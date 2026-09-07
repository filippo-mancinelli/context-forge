type BannerVariant = 'danger' | 'warning' | 'success' | 'info'

interface BannerProps {
  variant?: BannerVariant
  children: React.ReactNode
  className?: string
}

const variantStyles: Record<BannerVariant, string> = {
  danger: 'bg-[var(--danger-bg)] text-danger border-[var(--danger-border)]',
  warning: 'bg-[var(--warning-bg)] text-warning border-[var(--warning-border)]',
  success: 'bg-[var(--success-bg)] text-success border-[var(--success-border)]',
  info: 'bg-surface text-text border-border',
}

export function Banner({ variant = 'info', children, className = '' }: BannerProps) {
  return (
    <div
      role={variant === 'danger' ? 'alert' : 'status'}
      className={[
        'rounded border px-3 py-2 text-sm',
        variantStyles[variant],
        className,
      ].join(' ')}
    >
      {children}
    </div>
  )
}
