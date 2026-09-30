// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ToastProvider } from '../ui'
import { api, type DbAvailableScopes, type DbConnection } from '../../lib/api'
import ScopeDialog from './ScopeDialog'

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      catalog: { databases: { scopes: vi.fn() } },
      projects: { addDatabase: vi.fn(), updateDatabase: vi.fn() },
    },
  }
})

const erp = {
  id: 7, name: 'erp', engine: 'postgresql', host: 'db.internal', port: 5432,
  database_name: 'app', restricted: false, has_password: true, options: {}, status: 'ok',
} as DbConnection
const crm = { ...erp, id: 8, name: 'crm', engine: 'mysql', port: 3306, database_name: undefined } as DbConnection
const vault = { ...erp, id: 9, name: 'vault', restricted: true } as DbConnection

const LIVE: DbAvailableScopes = {
  scopes: [
    { database: 'app', schema: 'public', label: 'app.public' },
    { database: 'app', schema: 'sales', label: 'app.sales' },
  ],
  checked_at: '2026-09-19T08:00:00+00:00',
  live: true,
  error: null,
}

function renderDialog(props: Partial<Parameters<typeof ScopeDialog>[0]> = {}) {
  const onSaved = vi.fn()
  render(
    <ToastProvider>
      <ScopeDialog
        open
        projectId={18}
        catalog={[erp, crm, vault]}
        projectScopes={[]}
        editing={null}
        allowRestricted={false}
        onClose={() => {}}
        onSaved={onSaved}
        {...props}
      />
    </ToastProvider>,
  )
  return { onSaved }
}

beforeEach(() => {
  vi.mocked(api.catalog.databases.scopes).mockResolvedValue(LIVE)
  vi.mocked(api.projects.addDatabase).mockResolvedValue({ status: 'ok', scope: erp })
  vi.mocked(api.projects.updateDatabase).mockResolvedValue({ status: 'ok', scope: erp })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('ScopeDialog', () => {
  it('adds a scope picked from the live list, leaving the alias to the server', async () => {
    const { onSaved } = renderDialog()
    fireEvent.click(screen.getByRole('button', { name: /^erp/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'app.sales' }))
    expect((screen.getByRole('textbox', { name: 'Alias' }) as HTMLInputElement).value).toBe('erp')
    fireEvent.click(screen.getByRole('button', { name: 'Add data source' }))
    await waitFor(() => expect(api.projects.addDatabase).toHaveBeenCalled())
    // The alias shown previews the server's proposal: left untouched, the request
    // does not carry it and the server proposes it.
    const body = vi.mocked(api.projects.addDatabase).mock.calls[0][2]
    expect(body).toEqual({ database: 'app', schema: 'sales' })
    expect('alias' in body).toBe(false)
    expect(api.catalog.databases.scopes).toHaveBeenCalledWith(7, true)
    expect(onSaved).toHaveBeenCalled()
  })

  it('lets the server propose the alias of a connection named like a number', async () => {
    const numeric = { ...erp, id: 42, name: '42' } as DbConnection
    renderDialog({ catalog: [numeric] })
    fireEvent.click(screen.getByRole('button', { name: /^42/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'app.sales' }))
    fireEvent.click(screen.getByRole('button', { name: 'Add data source' }))
    await waitFor(() => expect(api.projects.addDatabase).toHaveBeenCalled())
    expect(vi.mocked(api.projects.addDatabase).mock.calls[0][2]).toEqual({
      database: 'app', schema: 'sales',
    })
  })

  it('sends the alias the user typed, at most a hundred characters', async () => {
    renderDialog()
    fireEvent.click(screen.getByRole('button', { name: /^erp/ }))
    const alias = await screen.findByRole('textbox', { name: 'Alias' })
    expect(alias.getAttribute('maxlength')).toBe('100')
    fireEvent.change(alias, { target: { value: 'sales' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add data source' }))
    await waitFor(() =>
      expect(api.projects.addDatabase).toHaveBeenCalledWith(18, 7, {
        database: 'app', schema: 'public', alias: 'sales',
      }),
    )
  })

  it('says that the scopes are being read while the server answers', async () => {
    let answer: (scopes: DbAvailableScopes) => void = () => {}
    vi.mocked(api.catalog.databases.scopes).mockReturnValue(
      new Promise<DbAvailableScopes>((resolve) => {
        answer = resolve
      }),
    )
    renderDialog()
    fireEvent.click(screen.getByRole('button', { name: /^erp/ }))
    expect(await screen.findByText('Reading the scopes from the server…')).toBeTruthy()
    answer(LIVE)
    expect(await screen.findByRole('button', { name: 'app.sales' })).toBeTruthy()
  })

  it('ignores a scope listing that arrives after another connection was chosen', async () => {
    let answerErp: (scopes: DbAvailableScopes) => void = () => {}
    vi.mocked(api.catalog.databases.scopes).mockImplementation((connectionId: number) =>
      connectionId === 7
        ? new Promise<DbAvailableScopes>((resolve) => {
            answerErp = resolve
          })
        : Promise.resolve({
            scopes: [{ database: 'crm', schema: null, label: 'crm' }],
            checked_at: null, live: true, error: null,
          }),
    )
    renderDialog()
    fireEvent.click(screen.getByRole('button', { name: /^erp/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Back' }))
    fireEvent.click(screen.getByRole('button', { name: /^crm/ }))
    expect(await screen.findByRole('button', { name: 'crm' })).toBeTruthy()
    answerErp(LIVE)
    await waitFor(() => expect(screen.queryByRole('button', { name: 'app.sales' })).toBeNull())
  })

  it('pre-fills an inferred scope with the database the connection works on today', async () => {
    const editing = {
      ...erp, database_name: 'app', scope_id: 3, alias: 'erp', scope_database: 'app_2024',
      scope_schema: 'public', scope_label: 'app', scope_inferred: true,
    } as DbConnection
    renderDialog({ editing, projectScopes: [editing] })
    const database = (await screen.findByRole('textbox', { name: 'Database' })) as HTMLInputElement
    expect(database.value).toBe('app')
  })

  it('proposes connection/scope as alias and marks the scopes already in use', async () => {
    const linked = { ...erp, scope_id: 3, alias: 'erp', scope_database: 'app', scope_schema: 'public' } as DbConnection
    renderDialog({ projectScopes: [linked] })
    fireEvent.click(screen.getByRole('button', { name: /^erp/ }))
    const used = await screen.findByRole('button', { name: /app\.public/ })
    expect(used.hasAttribute('disabled')).toBe(true)
    expect(screen.getByText('In use')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'app.sales' }))
    expect((screen.getByRole('textbox', { name: 'Alias' }) as HTMLInputElement).value).toBe('erp/app.sales')
  })

  it('says when the list is a snapshot and still accepts a scope typed by hand', async () => {
    vi.mocked(api.catalog.databases.scopes).mockResolvedValue({
      scopes: [], checked_at: '2026-09-18T08:00:00+00:00', live: false, error: 'timeout',
    })
    renderDialog()
    fireEvent.click(screen.getByRole('button', { name: /^erp/ }))
    expect(await screen.findByText(/The server did not answer: timeout/)).toBeTruthy()
    expect(screen.getByText(/last seen/)).toBeTruthy()
    fireEvent.change(screen.getByRole('textbox', { name: 'Schema' }), { target: { value: 'history' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add data source' }))
    await waitFor(() =>
      expect(api.projects.addDatabase).toHaveBeenCalledWith(18, 7, {
        database: 'app', schema: 'history',
      }),
    )
  })

  it('requires the database under the field', async () => {
    renderDialog()
    fireEvent.click(screen.getByRole('button', { name: /^crm/ }))
    await screen.findByRole('textbox', { name: 'Database' })
    fireEvent.click(screen.getByRole('button', { name: 'Add data source' }))
    expect(screen.getByText('Database is required.')).toBeTruthy()
    expect(api.projects.addDatabase).not.toHaveBeenCalled()
  })

  it('keeps restricted connections locked for non-admins', () => {
    renderDialog()
    expect(screen.getByRole('button', { name: /^vault/ }).hasAttribute('disabled')).toBe(true)
    expect(screen.getByText('Admin only')).toBeTruthy()
  })

  it('asks for an explicit confirmation before changing a scope', async () => {
    const editing = {
      ...erp, scope_id: 3, alias: 'erp', scope_database: 'app', scope_schema: 'public',
      scope_label: 'app', scope_inferred: true,
    } as DbConnection
    renderDialog({ editing, projectScopes: [editing] })
    expect(screen.getByText('Change scope of erp')).toBeTruthy()
    fireEvent.change(await screen.findByRole('textbox', { name: 'Schema' }), { target: { value: 'sales' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save scope' }))
    expect(api.projects.updateDatabase).not.toHaveBeenCalled()
    expect(screen.getByText(/will work on app\.sales of erp/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm scope change' }))
    await waitFor(() =>
      expect(api.projects.updateDatabase).toHaveBeenCalledWith(18, 3, {
        database: 'app', schema: 'sales', alias: 'erp',
      }),
    )
  })
})
