// Validazione condivisa del rate limit di una MCP key: stessa finestra
// accettata dal server (1..100000), vuoto = illimitato.
export const RATE_LIMIT_MAX = 100000

export const RATE_LIMIT_HINT =
  'Rate limit must be a positive integer between 1 and 100000, or left empty for unlimited.'

export type ParsedRateLimit = { value: number | null } | { error: string }

export function parseRateLimit(input: string): ParsedRateLimit {
  const trimmed = input.trim()
  if (trimmed === '') return { value: null }
  if (!/^\d+$/.test(trimmed)) return { error: RATE_LIMIT_HINT }
  const value = Number(trimmed)
  if (!Number.isInteger(value) || value < 1 || value > RATE_LIMIT_MAX) {
    return { error: RATE_LIMIT_HINT }
  }
  return { value }
}
