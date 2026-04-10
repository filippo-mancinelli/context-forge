type BadgeVariant = 'default' | 'success' | 'warning' | 'danger' | 'accent' | 'muted'

interface BadgeProps {
  variant?: BadgeVariant
  children: React.ReactNode
  className?: string
}

const variantStyles: Record<BadgeVariant, string> = {
  default: 'bg-surface text-text border-border',
  success: 'bg-[#eafaf1] text-[#1a7a45] border-[#a9dfbf]',
  warning: 'bg-[#fef9e7] text-[#9a6108] border-[#f9e4b7]',
  danger: 'bg-[#fef2f2] text-danger border-[#fca5a5]',
  accent: 'bg-[#eaf4fb] text-accent border-[#aed6f1]',
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
