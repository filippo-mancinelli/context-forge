import { Lock } from 'lucide-react'

export default function RestrictedIcon() {
  return (
    <span
      className="inline-flex align-middle ml-1.5 text-muted"
      title="Restricted: only organization admins can add it to a project"
    >
      <Lock className="w-3 h-3" aria-label="Restricted" />
    </span>
  )
}
