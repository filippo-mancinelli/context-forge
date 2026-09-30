import { describe, expect, it } from 'vitest'
import { actionFailed, actionFailedSentence, errorDetail } from './messages'

describe('errorDetail', () => {
  it('reads the message of an Error and drops the constructor prefix of a string', () => {
    expect(errorDetail(new Error('Host unreachable'))).toBe('Host unreachable')
    expect(errorDetail('Error: 409 name already taken')).toBe('409 name already taken')
  })

  it('drops a trailing period and blank details', () => {
    expect(errorDetail(new Error('Permission denied.'))).toBe('Permission denied')
    expect(errorDetail(undefined)).toBe('')
  })
})

describe('actionFailed', () => {
  it('names the action, then the detail, with no final period', () => {
    expect(actionFailed('delete the repository', new Error('Permission denied.'))).toBe(
      'Could not delete the repository: Permission denied',
    )
    expect(actionFailed('load the repositories')).toBe('Could not load the repositories')
  })
})

describe('actionFailedSentence', () => {
  it('ends every sentence with a period for an alert', () => {
    expect(actionFailedSentence('load the settings', new Error('Timeout'))).toBe('Could not load the settings. Timeout.')
    expect(actionFailedSentence('load the settings')).toBe('Could not load the settings.')
  })
})
