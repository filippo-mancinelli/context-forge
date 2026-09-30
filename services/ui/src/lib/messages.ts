// Error texts name the action that failed ("Could not delete the repository"). Toasts carry
// no final period; banners are full sentences closed by a period.

export function errorDetail(error: unknown): string {
  const raw = error instanceof Error ? error.message : error == null ? '' : String(error)
  return raw.replace(/^Error:\s*/, '').trim().replace(/\.$/, '')
}

export function actionFailed(action: string, error?: unknown): string {
  const detail = errorDetail(error)
  return detail ? `Could not ${action}: ${detail}` : `Could not ${action}`
}

export function actionFailedSentence(action: string, error?: unknown): string {
  const detail = errorDetail(error)
  return detail ? `Could not ${action}. ${detail}.` : `Could not ${action}.`
}
