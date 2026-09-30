export type FieldErrors<K extends string> = Partial<Record<K, string>>

type FieldValue = string | number | null | undefined

// Required fields are checked when the user clicks the call to action: the dialog never
// disables it, and the message sits under the field.
export function requiredError(label: string, value: FieldValue): string | undefined {
  if (value === null || value === undefined) return `${label} is required.`
  return String(value).trim() === '' ? `${label} is required.` : undefined
}

export function requiredErrors<K extends string>(
  fields: Record<K, { label: string; value: FieldValue }>,
): FieldErrors<K> {
  const errors: FieldErrors<K> = {}
  for (const key of Object.keys(fields) as K[]) {
    const message = requiredError(fields[key].label, fields[key].value)
    if (message) errors[key] = message
  }
  return errors
}

export function hasErrors(errors: Partial<Record<string, string>>): boolean {
  return Object.values(errors).some(Boolean)
}
