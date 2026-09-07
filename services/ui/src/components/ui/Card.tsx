interface CardProps {
  children: React.ReactNode
  className?: string
}

export function Card({ children, className = '' }: CardProps) {
  return (
    <div className={['rounded border border-border bg-surface', className].join(' ')}>
      {children}
    </div>
  )
}
