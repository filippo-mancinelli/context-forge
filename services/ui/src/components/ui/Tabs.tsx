import * as RadixTabs from '@radix-ui/react-tabs'

export const Tabs = RadixTabs.Root

export function TabsList({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <RadixTabs.List
      className={[
        'flex border-b border-border gap-0 mb-6',
        className,
      ].join(' ')}
    >
      {children}
    </RadixTabs.List>
  )
}

export function TabsTrigger({
  value,
  children,
}: {
  value: string
  children: React.ReactNode
}) {
  return (
    <RadixTabs.Trigger
      value={value}
      className="px-4 py-2 text-sm font-medium text-muted border-b-2 border-transparent -mb-px transition-colors hover:text-text data-[state=active]:text-accent data-[state=active]:border-accent focus:outline-none"
    >
      {children}
    </RadixTabs.Trigger>
  )
}

export const TabsContent = RadixTabs.Content
