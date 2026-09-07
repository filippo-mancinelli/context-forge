type BadgeVariant = 'default' | 'success' | 'warning' | 'danger' | 'accent' | 'muted'

interface BadgeProps {
  variant?: BadgeVariant
  children: React.ReactNode
  className?: string
}

const variantStyles: Record<BadgeVariant, string> = {
  default: 'bg-surface text-text border-border',
  success: 'bg-[var(--success-bg)] text-success border-[var(--success-border)]',
  warning: 'bg-[var(--warning-bg)] text-warning border-[var(--warning-border)]',
  danger: 'bg-[var(--danger-bg)] text-danger border-[var(--danger-border)]',
  accent: 'bg-primary-light text-text border-primary',
  muted: 'bg-surface text-muted border-border',
}

export function Badge({ variant = 'default', children, className = '' }: BadgeProps) {
  return (
    <span
      className={[
        'inline-flex items-center px-1.5 py-0.5 text-xs font-medium border rounded',
        variantStyles[variant],
        className,
      ].join(' ')}
    >
      {children}
    </span>
  )
}
