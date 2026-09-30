import { useAppStore } from '../store'

export function useOrgRole(): string | undefined {
  return useAppStore((s) => s.organizations.find((o) => o.id === s.activeOrgId)?.role)
}

export function useProjectRole(): string | undefined {
  return useAppStore((s) => s.projects.find((p) => p.id === s.activeProjectId)?.role)
}
