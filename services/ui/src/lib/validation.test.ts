import { describe, expect, it } from 'vitest'
import { hasErrors, requiredError, requiredErrors } from './validation'

describe('requiredError', () => {
  it('names the empty field in a sentence', () => {
    expect(requiredError('Name', '')).toBe('Name is required.')
    expect(requiredError('Name', '   ')).toBe('Name is required.')
    expect(requiredError('Port', undefined)).toBe('Port is required.')
    expect(requiredError('Port', null)).toBe('Port is required.')
  })

  it('accepts any non-blank value, zero included', () => {
    expect(requiredError('Name', 'billing')).toBeUndefined()
    expect(requiredError('Port', 0)).toBeUndefined()
  })
})

describe('requiredErrors', () => {
  it('returns one message per empty field and nothing for filled ones', () => {
    const errors = requiredErrors({
      name: { label: 'Name', value: '' },
      host: { label: 'Host', value: '10.0.0.9' },
      username: { label: 'Username', value: ' ' },
    })
    expect(errors).toEqual({ name: 'Name is required.', username: 'Username is required.' })
    expect(hasErrors(errors)).toBe(true)
  })

  it('reports no errors when every field is filled', () => {
    expect(hasErrors(requiredErrors({ name: { label: 'Name', value: 'x' } }))).toBe(false)
  })
})
