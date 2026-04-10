import * as RadixSelect from '@radix-ui/react-select'
import { ChevronDown, Check } from 'lucide-react'

interface SelectOption {
  value: string
  label: string
  disabled?: boolean
}

interface SelectProps {
  value?: string
  onValueChange?: (value: string) => void
  options: SelectOption[]
  placeholder?: string
  label?: string
  disabled?: boolean
  className?: string
}

export function Select({
  value,
  onValueChange,
  options,
  placeholder = 'Select...',
  label,
  disabled,
  className = '',
}: SelectProps) {
  return (
    <div className={['flex flex-col gap-1', className].join(' ')}>
      {label && <label className="text-sm font-medium text-text">{label}</label>}
      <RadixSelect.Root value={value} onValueChange={onValueChange} disabled={disabled}>
        <RadixSelect.Trigger className="inline-flex items-center justify-between gap-2 w-full px-3 py-1.5 text-sm bg-bg text-text border border-border rounded hover:border-accent focus:outline-none focus:border-accent disabled:bg-surface disabled:text-muted disabled:cursor-not-allowed transition-colors">
          <RadixSelect.Value placeholder={placeholder} />
          <RadixSelect.Icon>
            <ChevronDown className="w-3.5 h-3.5 text-muted" />
          </RadixSelect.Icon>
        </RadixSelect.Trigger>
        <RadixSelect.Portal>
          <RadixSelect.Content className="z-50 bg-bg border border-border shadow-md overflow-hidden rounded">
            <RadixSelect.Viewport className="p-1">
              {options.map((opt) => (
                <RadixSelect.Item
                  key={opt.value}
                  value={opt.value}
                  disabled={opt.disabled}
                  className="flex items-center justify-between gap-2 px-3 py-1.5 text-sm text-text cursor-pointer rounded select-none hover:bg-surface focus:bg-surface focus:outline-none data-[disabled]:text-muted data-[disabled]:cursor-not-allowed"
                >
                  <RadixSelect.ItemText>{opt.label}</RadixSelect.ItemText>
                  <RadixSelect.ItemIndicator>
                    <Check className="w-3.5 h-3.5 text-accent" />
                  </RadixSelect.ItemIndicator>
                </RadixSelect.Item>
              ))}
            </RadixSelect.Viewport>
          </RadixSelect.Content>
        </RadixSelect.Portal>
      </RadixSelect.Root>
    </div>
  )
}
