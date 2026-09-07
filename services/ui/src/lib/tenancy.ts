// Pure tenancy helpers — no DOM/localStorage access, so they stay unit-testable
// in a plain Node environment.

/** Auth + tenancy headers sent on every authenticated request. */
export function tenancyHeaders(
  token: string | null,
  orgId: number | null,
  projectId: number | null,
): Record<string, string> {
  return {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(orgId ? { 'X-Org-Id': String(orgId) } : {}),
    ...(projectId ? { 'X-Project-Id': String(projectId) } : {}),
  }
}

/**
 * Choose the active project id: keep the stored one while it still belongs to
 * the current list, otherwise fall back to the first (oldest = default)
 * project, or null when the list is empty.
 */
export function resolveActiveProjectId(
  projects: { id: number }[],
  stored: number | null,
): number | null {
  if (stored !== null && projects.some((p) => p.id === stored)) return stored
  return projects[0]?.id ?? null
}
