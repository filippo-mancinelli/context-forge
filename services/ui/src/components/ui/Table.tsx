import { type HTMLAttributes, type TdHTMLAttributes, type ThHTMLAttributes } from 'react'

export function Table({ className = '', ...props }: HTMLAttributes<HTMLTableElement>) {
  return (
    <div className="w-full overflow-x-auto">
      <table
        className={['w-full border-collapse text-sm', className].join(' ')}
        {...props}
      />
    </div>
  )
}

export function Thead({ className = '', ...props }: HTMLAttributes<HTMLTableSectionElement>) {
  return <thead className={className} {...props} />
}

export function Tbody({ className = '', ...props }: HTMLAttributes<HTMLTableSectionElement>) {
  return <tbody className={className} {...props} />
}

export function Tr({ className = '', ...props }: HTMLAttributes<HTMLTableRowElement>) {
  return (
    <tr
      className={['border-b border-border last:border-b-0', className].join(' ')}
      {...props}
    />
  )
}

export function Th({ className = '', ...props }: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={[
        'text-left text-xs font-semibold uppercase tracking-wide text-muted border-b-2 border-border px-3 py-2',
        className,
      ].join(' ')}
      {...props}
    />
  )
}

export function Td({ className = '', ...props }: TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td
      className={['px-3 py-2.5 text-sm align-top', className].join(' ')}
      {...props}
    />
  )
}
