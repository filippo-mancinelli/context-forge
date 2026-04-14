import * as RadixTabs from '@radix-ui/react-tabs'

export const Tabs = RadixTabs.Root

export function TabsList({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <div className="overflow-x-auto scrollbar-thin mb-6">
      <RadixTabs.List
        className={[
          'flex border-b border-border gap-0 min-w-max',
          className,
        ].join(' ')}
      >
        {children}
      </RadixTabs.List>
    </div>
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
