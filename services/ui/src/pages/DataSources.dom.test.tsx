// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom'
import { ConfirmProvider, ToastProvider } from '../components/ui'
import { api, type DbConnection } from '../lib/api'
import { useAppStore } from '../store'
import DataSources from './DataSources'

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>()
  return {
    ...actual,
    api: {
      datasources: { list: vi.fn() },
      catalog: {
        databases: {
          list: vi.fn().mockResolvedValue({ connections: [], engines: [] }),
          scopes: vi.fn().mockResolvedValue({ scopes: [], checked_at: null, live: true, error: null }),
        },
      },
      projects: { addDatabase: vi.fn(), updateDatabase: vi.fn(), removeDatabase: vi.fn() },
    },
  }
})

const inferred = {
  id: 7, name: 'erp', engine: 'postgresql', host: 'db.internal', port: 5432, database_name: 'app',
  restricted: false, has_password: true, options: {}, status: 'ok', annotation_count: 0,
  scope_id: 3, alias: 'erp', scope_database: 'app', scope_schema: 'public', scope_label: 'app',
  scope_inferred: true,
} as DbConnection
const confirmed = {
  ...inferred, scope_id: 4, alias: 'erp/app.sales', scope_schema: 'sales',
  scope_label: 'app.sales', scope_inferred: false,
} as DbConnection
const single = {
  id: 9, name: 'billing', engine: 'mysql', host: 'db2.internal', port: 3306, database_name: 'billing',
  restricted: false, has_password: true, options: {}, status: 'ok', annotation_count: 0,
  scope_id: 5, alias: 'billing', scope_database: 'billing', scope_schema: null, scope_label: 'billing',
  scope_inferred: false,
} as DbConnection

// Shows the scopeId resolved by the route, to check which scope was opened.
function DetailStub() {
  const { scopeId } = useParams<{ scopeId: string }>()
  return <p>data source detail {scopeId}</p>
}

function renderPage() {
  render(
    <MemoryRouter initialEntries={['/datasources']}>
      <ToastProvider>
        <ConfirmProvider>
          <Routes>
            <Route path="/datasources" element={<DataSources />} />
            <Route path="/datasources/:scopeId" element={<DetailStub />} />
          </Routes>
        </ConfirmProvider>
      </ToastProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  useAppStore.setState({
    organizations: [{ id: 1, name: 'Acme', role: 'admin' }] as never,
    activeOrgId: 1,
    projects: [{ id: 18, name: 'Demo', role: 'admin' }] as never,
    activeProjectId: 18,
  })
  vi.mocked(api.datasources.list).mockResolvedValue({ connections: [inferred, confirmed, single], engines: [] })
  vi.mocked(api.projects.removeDatabase).mockResolvedValue({ status: 'ok' })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('Data Sources page', () => {
  it('lists one row per scope, with its label and the inferred warning', async () => {
    renderPage()
    const row = (await screen.findByText('erp/app.sales')).closest('tr') as HTMLElement
    expect(within(row).getByText('app.sales')).toBeTruthy()
    expect(within(row).queryByText('Inferred')).toBeNull()
    expect(screen.getAllByText('Inferred')).toHaveLength(1)
  })

  it('opens the scope dialog from Change scope', async () => {
    renderPage()
    const row = (await screen.findByText('erp/app.sales')).closest('tr') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Change scope' }))
    expect(await screen.findByText('Change scope of erp/app.sales')).toBeTruthy()
  })

  it('removes a single scope from the project', async () => {
    renderPage()
    const row = (await screen.findByText('erp/app.sales')).closest('tr') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Remove data source from project' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Remove data source' }))
    await waitFor(() => expect(api.projects.removeDatabase).toHaveBeenCalledWith(18, 4))
  })

  it('adds a data source through the scope dialog', async () => {
    renderPage()
    await screen.findByText('erp/app.sales')
    fireEvent.click(screen.getByRole('button', { name: 'Add data source' }))
    expect(await screen.findByText('Choose a database connection from the organization catalog.')).toBeTruthy()
    expect(api.catalog.databases.list).toHaveBeenCalled()
  })

  it('opens each scope of a multi-scope connection from its own row', async () => {
    renderPage()
    const firstScope = (await screen.findByText('Inferred')).closest('tr') as HTMLElement
    fireEvent.click(within(firstScope).getByRole('link', { name: 'erp' }))
    expect(await screen.findByText(`data source detail ${inferred.scope_id}`)).toBeTruthy()

    cleanup()
    renderPage()
    const secondScope = (await screen.findByText('erp/app.sales')).closest('tr') as HTMLElement
    fireEvent.click(within(secondScope).getByRole('link', { name: 'erp/app.sales' }))
    expect(await screen.findByText(`data source detail ${confirmed.scope_id}`)).toBeTruthy()
  })

  it('opens a connection linked on a single scope', async () => {
    renderPage()
    const singleRow = (await screen.findByRole('link', { name: 'billing' })).closest('tr') as HTMLElement
    fireEvent.click(within(singleRow).getByRole('link', { name: 'billing' }))
    expect(await screen.findByText(`data source detail ${single.scope_id}`)).toBeTruthy()
  })
})
