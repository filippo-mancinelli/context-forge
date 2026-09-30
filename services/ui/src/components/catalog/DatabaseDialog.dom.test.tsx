// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ToastProvider } from '../ui'
import DatabaseDialog from './DatabaseDialog'
import { api, type DbConnection } from '../../lib/api'

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      catalog: {
        databases: { create: vi.fn().mockResolvedValue({}), update: vi.fn().mockResolvedValue({}) },
      },
    },
  }
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function renderDialog(editing: DbConnection | null) {
  render(
    <ToastProvider>
      <DatabaseDialog open editing={editing} machines={[]} onOpenChange={() => {}} onSaved={() => {}} />
    </ToastProvider>,
  )
}

const warehouse = {
  id: 9,
  name: 'warehouse',
  engine: 'postgresql',
  host: 'pg.internal',
  port: 5432,
  database_name: 'app',
  username: 'reader',
  has_password: true,
  options: {},
  restricted: false,
  status: 'ok',
  available_scopes: [
    { database: 'app', schema: 'public', label: 'app.public' },
    { database: 'app', schema: 'sales', label: 'app.sales' },
  ],
  scopes_checked_at: '2026-09-18T10:00:00+00:00',
} as DbConnection

const crm = {
  id: 7,
  name: 'crm',
  engine: 'mysql',
  host: 'mysql.internal',
  port: 3306,
  username: 'reader',
  has_password: true,
  options: {},
  restricted: false,
  status: 'ok',
} as DbConnection

// Never tested: no scope seen, no check ever run.
const neverChecked = {
  id: 12,
  name: 'legacy',
  engine: 'mysql',
  host: 'legacy.internal',
  port: 3306,
  username: 'reader',
  has_password: true,
  options: {},
  restricted: false,
  status: 'ok',
} as DbConnection

// Check succeeded but no scope readable with these credentials.
const checkedEmpty = {
  id: 13,
  name: 'reporting',
  engine: 'mysql',
  host: 'reporting.internal',
  port: 3306,
  username: 'reader',
  has_password: true,
  options: {},
  restricted: false,
  status: 'ok',
  available_scopes: [],
  scopes_checked_at: '2026-09-20T08:00:00+00:00',
} as DbConnection

describe('DatabaseDialog', () => {
  // The context-forge dialog relies on native constraint validation: the browser blocks the
  // submit while a required field is empty.
  it('requires the default database on PostgreSQL and says the project chooses the scope', () => {
    renderDialog(null)
    expect(screen.getByText(/Each project chooses its own database and schema/)).toBeTruthy()
    const database = screen.getByLabelText('Default database') as HTMLInputElement
    expect(database.required).toBe(true)
    expect(database.validity.valueMissing).toBe(true)
    expect(api.catalog.databases.create).not.toHaveBeenCalled()
  })

  it('saves a MySQL endpoint without a default database', async () => {
    renderDialog(crm)
    const database = screen.getByLabelText('Default database') as HTMLInputElement
    expect(database.required).toBe(false)
    const form = database.closest('form') as HTMLFormElement
    expect(form.checkValidity()).toBe(true)
    fireEvent.submit(form)
    await waitFor(() =>
      expect(api.catalog.databases.update).toHaveBeenCalledWith(
        7,
        expect.objectContaining({ engine: 'mysql', host: 'mysql.internal', database_name: undefined }),
      ),
    )
  })

  it('shows the scopes seen at the last check, read-only', () => {
    renderDialog(warehouse)
    expect(screen.getByText('Scopes seen at last check')).toBeTruthy()
    expect(screen.getByText('app.public')).toBeTruthy()
    expect(screen.getByText('app.sales')).toBeTruthy()
    expect(screen.getByText(/^Checked on /)).toBeTruthy()
  })

  it('invites a test when no scope has been seen yet', () => {
    renderDialog(neverChecked)
    expect(screen.getByText('No scopes seen yet. Test the connection to read them from the server.')).toBeTruthy()
    expect(screen.queryByText(/no scopes visible with these credentials/)).toBeNull()
  })

  it('says a check found nothing, without also claiming it was never checked', () => {
    renderDialog(checkedEmpty)
    expect(screen.getByText(/^Checked on .*: no scopes visible with these credentials\.$/)).toBeTruthy()
    expect(
      screen.queryByText('No scopes seen yet. Test the connection to read them from the server.'),
    ).toBeNull()
  })
})
