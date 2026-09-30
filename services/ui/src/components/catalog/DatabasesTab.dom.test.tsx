// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { ConfirmProvider, ToastProvider } from '../ui'
import { api, type DbConnection } from '../../lib/api'
import DatabasesTab from './DatabasesTab'

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      catalog: {
        machines: { list: vi.fn().mockResolvedValue({ machines: [] }) },
        databases: {
          list: vi.fn(),
          projects: vi.fn(),
          delete: vi.fn().mockResolvedValue({ status: 'ok' }),
          test: vi.fn(),
          update: vi.fn(),
        },
      },
    },
  }
})

const erp = {
  id: 5,
  name: 'erp',
  engine: 'mysql',
  host: 'db.internal',
  port: 3306,
  has_password: true,
  options: {},
  restricted: false,
  status: 'ok',
  scope_count: 2,
} as DbConnection

beforeEach(() => {
  vi.mocked(api.catalog.databases.list).mockResolvedValue({ connections: [erp], engines: ['mysql'] })
  vi.mocked(api.catalog.databases.projects).mockResolvedValue({
    projects: [
      { project_id: 1, project_name: 'alpha', scope_id: 10, alias: 'erp', scope_label: 'erp' },
      { project_id: 2, project_name: 'beta', scope_id: 11, alias: 'erp-archive', scope_label: 'erp_archive' },
    ],
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function renderTab() {
  render(
    <ToastProvider>
      <ConfirmProvider>
        <DatabasesTab canManage />
      </ConfirmProvider>
    </ToastProvider>,
  )
}

describe('DatabasesTab', () => {
  it('counts project scopes and shows the endpoint when no default database is set', async () => {
    renderTab()
    await screen.findByText('erp')
    expect(screen.getByText('Project scopes')).toBeTruthy()
    const row = screen.getAllByRole('row')[1]
    expect(within(row).getByText('2')).toBeTruthy()
    expect(within(row).getByText('db.internal:3306')).toBeTruthy()
  })

  it('lists every project scope in the delete confirmation', async () => {
    renderTab()
    await screen.findByText('erp')
    const row = screen.getAllByRole('row')[1]
    fireEvent.click(within(row).getByRole('button', { name: 'Delete' }))
    expect(await screen.findByText('alpha — erp (erp)')).toBeTruthy()
    expect(screen.getByText('beta — erp-archive (erp_archive)')).toBeTruthy()
    expect(screen.getByText('These project scopes use it and will be removed:')).toBeTruthy()
  })
})
