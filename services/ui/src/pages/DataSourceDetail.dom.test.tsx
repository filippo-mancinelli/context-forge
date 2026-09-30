// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ToastProvider } from '../components/ui'
import { api, type DbAnnotation, type DbConnection } from '../lib/api'
import DataSourceDetail from './DataSourceDetail'

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>()
  return {
    ...actual,
    api: {
      datasources: {
        list: vi.fn(),
        schema: vi.fn(),
        table: vi.fn(),
        saveAnnotations: vi.fn(),
        query: vi.fn(),
        log: vi.fn(),
      },
    },
  }
})

const source = {
  id: 42,
  scope_id: 7,
  name: 'Billing DB',
  alias: 'billing',
  engine: 'postgresql',
  host: 'db.example.com',
  port: 5432,
  database_name: 'billing',
  scope_database: 'billing',
  scope_schema: 'public',
  scope_label: 'billing.public',
  scope_inferred: true,
  status: 'ok',
  annotation_count: 3,
} as DbConnection

const invoices = {
  table: 'invoices',
  schema: 'public',
  columns: [{ name: 'id', type: 'integer', nullable: false, autoincrement: true }],
  primary_key: ['id'],
  foreign_keys: [],
  indexes: [],
  estimated_rows: 1200,
}

function renderPage(path = '/datasources/7') {
  render(
    <MemoryRouter initialEntries={[path]}>
      <ToastProvider>
        <Routes>
          <Route path="/datasources/:scopeId" element={<DataSourceDetail />} />
        </Routes>
      </ToastProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.mocked(api.datasources.list).mockResolvedValue({ connections: [source] } as never)
  vi.mocked(api.datasources.schema).mockResolvedValue({
    schema: 'public',
    schemas: ['public'],
    tables: [{ name: 'invoices', estimated_rows: 1200, description: 'Issued invoices' }],
    views: [],
  } as never)
  vi.mocked(api.datasources.table).mockResolvedValue(invoices as never)
  vi.mocked(api.datasources.saveAnnotations).mockResolvedValue({ status: 'ok', written: 2 })
  vi.mocked(api.datasources.log).mockResolvedValue({
    log: [{ id: 1, source: 'ui', sql_text: 'SELECT 1', success: true, rows_returned: 1,
            duration_ms: 3, schema_name: 'reporting' }],
    count: 1,
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('Data source detail', () => {
  it('names the data source by its alias in the page header with its status', async () => {
    renderPage()
    const title = await screen.findByRole('heading', { level: 1, name: /^billing/ })
    expect(title.textContent).toContain('Reachable')
  })

  it('describes the connection server in its own card with key/value rows', async () => {
    renderPage()
    const card = (await screen.findByRole('heading', { level: 3, name: 'Connection' })).parentElement as HTMLElement
    expect(within(card).getByText('Engine').tagName).toBe('DT')
    expect(within(card).getByText('postgresql')).toBeTruthy()
    expect(within(card).getByText('db.example.com:5432')).toBeTruthy()
    expect(within(card).getByText('Default database')).toBeTruthy()
  })

  it('shows the scope in its own side card, with the inferred warning', async () => {
    renderPage()
    const heading = await screen.findByRole('heading', { level: 3, name: 'Scope' })
    const card = heading.parentElement as HTMLElement
    expect(within(card).getByText('billing.public')).toBeTruthy()
    expect(within(card).getByText('Inferred')).toBeTruthy()
  })

  it('has no free schema selector and loads the schema of the scope', async () => {
    renderPage()
    await screen.findByRole('button', { name: /invoices/ })
    expect(screen.queryByRole('combobox')).toBeNull()
    expect(api.datasources.schema).toHaveBeenCalledWith(7)
  })

  it('opens a table by scope id', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /invoices/ }))
    expect(await screen.findByRole('heading', { level: 2, name: /^invoices/ })).toBeTruthy()
    expect(api.datasources.table).toHaveBeenCalledWith(7, 'invoices')
  })

  it('saves descriptions on the schema of the scope, never on the wildcard', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /invoices/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Edit descriptions' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Save descriptions' }))
    await waitFor(() => expect(api.datasources.saveAnnotations).toHaveBeenCalled())
    const [sourceId, annotations] = vi.mocked(api.datasources.saveAnnotations).mock.calls[0]
    expect(sourceId).toBe(7)
    expect((annotations as DbAnnotation[]).map((a) => a.schema_name)).toEqual(['public', 'public'])
  })

  it('opens from the id of a connection with a single scope', async () => {
    renderPage('/datasources/42')
    expect(await screen.findByRole('heading', { level: 1, name: /^billing/ })).toBeTruthy()
  })

  it('does not guess between scopes of the same connection', async () => {
    vi.mocked(api.datasources.list).mockResolvedValue({
      connections: [source, { ...source, scope_id: 8, alias: 'billing/billing.archive' }],
    } as never)
    renderPage('/datasources/42')
    expect(await screen.findByText('Data source not found.')).toBeTruthy()
  })

  it('treats a resolved source without a scope id as not found, with no API call', async () => {
    vi.mocked(api.datasources.list).mockResolvedValue({
      connections: [{ ...source, scope_id: undefined }],
    } as never)
    renderPage('/datasources/42')
    expect(await screen.findByText('Data source not found.')).toBeTruthy()
    expect(api.datasources.schema).not.toHaveBeenCalled()
  })

  it('refuses to save descriptions when the schema of the scope is unknown', async () => {
    vi.mocked(api.datasources.table).mockResolvedValue({ ...invoices, schema: undefined } as never)
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /invoices/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Edit descriptions' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Save descriptions' }))
    expect(
      await screen.findByText('The schema of this scope is unknown; descriptions cannot be saved.'),
    ).toBeTruthy()
    expect(api.datasources.saveAnnotations).not.toHaveBeenCalled()
  })

  it('shows the schema of each query in the log', async () => {
    renderPage()
    fireEvent.mouseDown(await screen.findByRole('tab', { name: 'Query Log' }), { button: 0 })
    expect(await screen.findByText('reporting')).toBeTruthy()
    expect(api.datasources.log).toHaveBeenCalledWith(7, 100)
  })
})
