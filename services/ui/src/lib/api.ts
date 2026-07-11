const BASE = import.meta.env.VITE_API_URL || ''
const AUTH_TOKEN_KEY = 'cf_admin_token'
const ACTIVE_ORG_KEY = 'cf_active_org'

export function getAuthToken() {
  return localStorage.getItem(AUTH_TOKEN_KEY)
}

export function setAuthToken(token: string) {
  localStorage.setItem(AUTH_TOKEN_KEY, token)
}

export function clearAuthToken() {
  localStorage.removeItem(AUTH_TOKEN_KEY)
}

export function getActiveOrgId(): number | null {
  const raw = localStorage.getItem(ACTIVE_ORG_KEY)
  return raw ? Number(raw) : null
}

export function setActiveOrgId(orgId: number | null) {
  if (orgId === null) localStorage.removeItem(ACTIVE_ORG_KEY)
  else localStorage.setItem(ACTIVE_ORG_KEY, String(orgId))
}

async function request<T>(path: string, options?: RequestInit, includeAuth = true): Promise<T> {
  const token = includeAuth ? getAuthToken() : null
  const activeOrg = includeAuth ? getActiveOrgId() : null
  const headers: Record<string, string> = {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(activeOrg ? { 'X-Org-Id': String(activeOrg) } : {}),
    ...((options?.headers as Record<string, string> | undefined) || {}),
  }

  if (options?.body !== undefined && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json'
  }

  const res = await fetch(`${BASE}${path}`, {
    headers,
    ...options,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${text}`)
  }
  return res.json()
}

function authHeaders(): Record<string, string> {
  const token = getAuthToken()
  const activeOrg = getActiveOrgId()
  return {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(activeOrg ? { 'X-Org-Id': String(activeOrg) } : {}),
  }
}

async function uploadRequest<T>(path: string, formData: FormData): Promise<T> {
  // Note: do NOT set Content-Type — the browser adds the multipart boundary.
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: authHeaders(),
    body: formData,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${text}`)
  }
  return res.json()
}

async function downloadRequest(path: string): Promise<Blob> {
  const res = await fetch(`${BASE}${path}`, { headers: authHeaders() })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${text}`)
  }
  return res.blob()
}

export interface Repo {
  name: string
  type: 'local' | 'github' | 'gitlab'
  url?: string
  path?: string
  branch: string
  language: string
  status: 'pending' | 'indexing' | 'indexed' | 'error'
  last_indexed_at?: string
  total_chunks: number
  error_message?: string
}

export interface GitHubRepo {
  id: number
  name: string
  full_name: string
  description?: string
  url: string
  clone_url: string
  default_branch: string
  private: boolean
  language?: string
  stargazers_count: number
  fork: boolean
}

export interface GitLabRepo {
  id: number
  name: string
  full_name: string
  description?: string
  url: string
  clone_url: string
  default_branch: string
  private: boolean
  language?: string
  star_count: number
  forked_from_project: boolean
}

export type RemoteRepo = GitHubRepo | GitLabRepo

export interface RepoCreateRequest {
  name: string
  type: 'local' | 'github' | 'gitlab'
  url?: string
  path?: string
  branch: string
  language?: string
}

export interface RepoSearchResult {
  repo_name: string
  file_path: string
  chunk_type: string
  content: string
  metadata?: Record<string, unknown> | null
  score: number
}

export interface RepoRelationship {
  repo_a: string
  repo_b: string
  similarity: number
  chunks_a: number
  chunks_b: number
}

export interface RepoStats {
  repo: Repo
  chunk_types: { chunk_type: string; count: number }[]
  by_extension: { extension: string; count: number }[]
}

export type KbDocumentStatus = 'pending' | 'processing' | 'ready' | 'error'

export interface KbDocument {
  id: number
  title: string
  filename: string
  content_type?: string
  extension?: string
  size_bytes: number
  status: KbDocumentStatus
  total_chunks: number
  char_count: number
  error_message?: string
  metadata?: Record<string, unknown>
  uploaded_at?: string
  processed_at?: string
}

export interface KbUploadResult {
  status: string
  created: { id: number; title: string; filename: string; extension?: string; size_bytes: number; status: string; supported?: boolean }[]
  rejected: { filename: string; reason: string }[]
}

export interface KbSearchResult {
  document_id: number
  title: string
  filename: string
  extension?: string
  chunk_index: number
  content: string
  metadata?: Record<string, unknown> | null
  score: number
}

export type WebPageStatus = 'pending' | 'processing' | 'ready' | 'error'

export interface WebPage {
  id: number
  url: string
  title?: string
  status: WebPageStatus
  site_id?: number | null
  total_chunks: number
  char_count: number
  error_message?: string
  metadata?: Record<string, unknown>
  created_at?: string
  fetched_at?: string
}

export type WebSiteStatus = 'pending' | 'crawling' | 'ready' | 'error'

export interface WebSite {
  id: number
  root_url: string
  status: WebSiteStatus
  max_pages: number
  exclude_patterns: string[]
  pages_found: number
  error_message?: string
  created_at?: string
  crawled_at?: string
  total_pages: number
  ready_pages: number
  error_pages: number
  total_chunks: number
}

export interface WebAddResult {
  status: string
  created: { id: number; url: string; title?: string; status: string }[]
  rejected: { url: string; reason: string }[]
}

export interface WebSearchResult {
  page_id: number
  title?: string
  url: string
  chunk_index: number
  content: string
  metadata?: Record<string, unknown> | null
  score: number
}

export interface Memory {
  id: string
  memory: string
  metadata?: Record<string, unknown>
  score?: number
  created_at?: string
}

export interface MemoryCreateRequest {
  content: string
  metadata?: Record<string, unknown>
  user_id?: string
  infer?: boolean
}

export interface Tool {
  name: string
  description: string
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface ChatToolCall {
  tool: string
  source: 'repositories' | 'memory' | 'knowledge_base' | 'web' | 'databases'
  query: string
  result_count: number
  results: Record<string, unknown>[]
  error?: string | null
}

export interface ChatSourcesUsed {
  repositories: boolean
  memory: boolean
  knowledge_base: boolean
  web: boolean
  databases: boolean
}

export interface ChatResponse {
  reply: string
  tool_calls: ChatToolCall[]
  sources_used: ChatSourcesUsed
  model: string
}

export interface ChatModel {
  id: string
  provider: string
  label: string
}

export interface ChatModelsResponse {
  models: ChatModel[]
  default: { provider: string; model: string } | null
}

export type ChatStreamEvent =
  | { type: 'reasoning'; delta: string }
  | { type: 'text'; delta: string }
  | ({ type: 'tool_start' } & Pick<ChatToolCall, 'tool' | 'source' | 'query'>)
  | ({ type: 'tool_result' } & ChatToolCall)
  | { type: 'done'; model: string; sources_used: ChatSourcesUsed }
  | { type: 'error'; message: string }

// One conversation turn as persisted in a saved session / shared snapshot.
export interface StoredChatTurn {
  role: 'user' | 'assistant'
  content: string
  reasoning?: string
  toolCalls?: ChatToolCall[]
  model?: string
  stopped?: boolean
}

export interface ChatSessionSummary {
  id: number
  title: string
  turn_count: number
  shared: boolean
  created_at?: string
  updated_at?: string
}

export interface ChatSessionDetail extends ChatSessionSummary {
  turns: StoredChatTurn[]
}

export interface SharedChatResponse {
  title: string
  turns: StoredChatTurn[]
  shared_at?: string
}

export interface Job {
  id: string
  tool: string
  status: 'pending' | 'running' | 'done' | 'error'
  error_message?: string
  created_at: string
  updated_at: string
}

export interface MCPApiKey {
  id: number
  name: string
  scope: string
  created_at: string
  last_used_at?: string
  expires_at?: string
  created_by: number
}

export interface MCPApiKeyCreateRequest {
  name: string
  scope?: string
  expires_days?: number
}

export type OrgRole = 'viewer' | 'member' | 'admin' | 'owner'

export interface Organization {
  id: number
  name: string
  slug: string
  memory_namespace: string
  created_at?: string
  role?: OrgRole
}

export interface OrgMember {
  user_id: number
  username: string
  email?: string
  role: OrgRole
  created_at?: string
}

export interface OrgInvitation {
  id: number
  org_id: number
  email: string
  role: OrgRole
  expires_at?: string
  accepted_at?: string
  created_at?: string
}

export interface CurrentUser {
  id: number
  username: string
  email?: string
}

export interface MeResponse {
  user: CurrentUser
  organizations: Organization[]
}

export type DbEngine = 'postgresql' | 'mysql' | 'mariadb' | 'sqlite'

export interface DbConnection {
  id: number
  name: string
  engine: DbEngine
  host?: string
  port?: number
  database_name?: string
  username?: string
  has_password: boolean
  options: Record<string, unknown>
  description?: string
  status: 'unknown' | 'ok' | 'error'
  error_message?: string
  last_checked_at?: string
  created_at?: string
  updated_at?: string
  annotation_count?: number
}

export interface DbConnectionRequest {
  name: string
  engine: DbEngine
  host?: string
  port?: number
  database_name?: string
  username?: string
  password?: string
  options?: Record<string, unknown>
  description?: string
}

export interface DbSchemaTable {
  name: string
  comment?: string | null
  description?: string | null
  column_count: number
  estimated_rows?: number | null
}

export interface DbSchemaOverview {
  connection: string
  connection_id: number
  dialect: string
  default_schema?: string
  schema?: string
  schemas: string[]
  tables: DbSchemaTable[]
  views: string[]
}

export interface DbColumn {
  name: string
  type: string
  nullable: boolean
  default?: string | null
  comment?: string | null
  description?: string | null
  autoincrement: boolean
}

export interface DbTableDetail {
  connection: string
  connection_id: number
  schema?: string
  table: string
  comment?: string | null
  description?: string | null
  columns: DbColumn[]
  primary_key: string[]
  foreign_keys: { columns: string[]; referred_schema?: string; referred_table: string; referred_columns: string[] }[]
  indexes: { name?: string; columns: string[]; unique: boolean }[]
  unique_constraints: { name?: string; columns: string[] }[]
  estimated_rows?: number | null
  sample_rows?: Record<string, unknown>[]
  sample_error?: string
}

export interface DbAnnotation {
  schema_name: string
  table_name: string
  column_name: string
  description: string
}

export interface DbQueryResult {
  connection: string
  sql: string
  columns: string[]
  rows: Record<string, unknown>[]
  row_count: number
  truncated: boolean
  duration_ms: number
}

export interface DbQueryLogEntry {
  id: number
  source: string
  sql_text: string
  success: boolean
  error_message?: string
  rows_returned: number
  duration_ms: number
  created_at?: string
}

export type ApiContractType = 'openapi' | 'graphql'

export interface ApiContract {
  id: number
  name: string
  type: ApiContractType
  source_url?: string
  description?: string
  title?: string
  version?: string
  status: 'pending' | 'ready' | 'error'
  error_message?: string
  endpoint_count: number
  fetched_at?: string
  created_at?: string
  updated_at?: string
}

export interface ApiContractCreateRequest {
  name: string
  type: ApiContractType
  source_url?: string
  raw_spec?: string
  description?: string
}

export interface ApiEndpointSummary {
  id: number
  contract: string
  method: string
  path: string
  operation_id?: string
  summary?: string
  tags: string[]
  deprecated: boolean
}

export interface ApiEndpointDetail {
  id: number
  contract: string
  method: string
  path: string
  operation_id?: string
  summary?: string
  description?: string
  tags: string[]
  deprecated: boolean
  request_schema: Record<string, unknown>
  response_schema: Record<string, unknown>
}

export interface CiRun {
  id: number
  name?: string
  status?: string
  conclusion?: string
  branch?: string
  commit?: string
  event?: string
  url?: string
  created_at?: string
  updated_at?: string
}

export interface CiFailedJob {
  name?: string
  failed_steps: string[]
  log_tail: string
  url?: string
}

export interface CiFailureDetail {
  found: boolean
  message?: string
  run?: CiRun
  failed_jobs?: CiFailedJob[]
}

export interface SetupStatus {
  is_configured: boolean
  mode: 'configured' | 'admin' | 'full'
  has_admin: boolean
  has_runtime_config: boolean
}

export interface SettingsUpdateResponse {
  status: string
  warnings: string[]
  requires_reindex: boolean
  requires_vector_reset: boolean
}

export const api = {
  setup: {
    status: () => request<SetupStatus>('/api/setup/status', undefined, false),
    init: (payload: {
      bootstrap_token: string
      admin_username: string
      admin_password: string
      forge_config?: Record<string, unknown>
      settings_overrides?: Record<string, unknown>
    }) =>
      request('/api/setup/init', {
        method: 'POST',
        body: JSON.stringify(payload),
      }, false),
  },
  auth: {
    login: (username: string, password: string) =>
      request<{ token: string; token_type: string }>(
        '/api/auth/login',
        { method: 'POST', body: JSON.stringify({ username, password }) },
        false
      ),
    session: () => request<{ status: string }>('/api/auth/session'),
    me: () => request<MeResponse>('/api/auth/me'),
    logout: () => request('/api/auth/logout', { method: 'POST' }),
  },
  organizations: {
    list: () => request<{ organizations: Organization[] }>('/api/organizations'),
    create: (name: string) =>
      request<{ status: string; organization: Organization }>('/api/organizations', {
        method: 'POST',
        body: JSON.stringify({ name }),
      }),
    update: (orgId: number, name: string) =>
      request<{ status: string; organization: Organization }>(`/api/organizations/${orgId}`, {
        method: 'PATCH',
        body: JSON.stringify({ name }),
      }),
    delete: (orgId: number) =>
      request<{ status: string }>(`/api/organizations/${orgId}`, { method: 'DELETE' }),
    members: (orgId: number) =>
      request<{ members: OrgMember[] }>(`/api/organizations/${orgId}/members`),
    updateMember: (orgId: number, userId: number, role: OrgRole) =>
      request<{ status: string }>(`/api/organizations/${orgId}/members/${userId}`, {
        method: 'PATCH',
        body: JSON.stringify({ role }),
      }),
    removeMember: (orgId: number, userId: number) =>
      request<{ status: string }>(`/api/organizations/${orgId}/members/${userId}`, {
        method: 'DELETE',
      }),
    invitations: (orgId: number) =>
      request<{ invitations: OrgInvitation[] }>(`/api/organizations/${orgId}/invitations`),
    invite: (orgId: number, email: string, role: OrgRole) =>
      request<{ status: string; invitation?: OrgInvitation; invite_token?: string; added_existing_user?: boolean }>(
        `/api/organizations/${orgId}/invitations`,
        { method: 'POST', body: JSON.stringify({ email, role }) }
      ),
    revokeInvite: (orgId: number, invitationId: number) =>
      request<{ status: string }>(`/api/organizations/${orgId}/invitations/${invitationId}`, {
        method: 'DELETE',
      }),
  },
  invitations: {
    preview: (token: string) =>
      request<{ email: string; role: OrgRole; org_name: string; expires_at?: string }>(
        `/api/invitations/${encodeURIComponent(token)}`,
        undefined,
        false
      ),
    accept: (token: string, username: string, password: string) =>
      request<{ status: string; token: string; token_type: string }>(
        '/api/invitations/accept',
        { method: 'POST', body: JSON.stringify({ token, username, password }) },
        false
      ),
  },
  repos: {
    list: () => request<Repo[]>('/api/repos'),
    create: (req: RepoCreateRequest) =>
      request<{ status: string; repo: { name: string; type: string } }>('/api/repos', {
        method: 'POST',
        body: JSON.stringify(req),
      }),
    update: (name: string, req: RepoCreateRequest) =>
      request<{ status: string; repo: { name: string; type: string } }>(`/api/repos/${encodeURIComponent(name)}`, {
        method: 'PUT',
        body: JSON.stringify(req),
      }),
    delete: (name: string) =>
      request<{ status: string; message: string }>(`/api/repos/${encodeURIComponent(name)}`, { method: 'DELETE' }),
    search: (query: string, repos?: string[], limit = 20) =>
      request<{ results: RepoSearchResult[]; count: number }>('/api/repos/search', {
        method: 'POST',
        body: JSON.stringify({ query, repos, limit }),
      }),
    relationships: (repo?: string) =>
      request<{ relationships: RepoRelationship[]; count: number }>(
        `/api/repos/relationships${repo ? `?repo=${encodeURIComponent(repo)}` : ''}`
      ),
    index: (name: string) => request(`/api/repos/${encodeURIComponent(name)}/index`, { method: 'POST' }),
    cancelIndex: (name: string) => request(`/api/repos/${encodeURIComponent(name)}/cancel-index`, { method: 'POST' }),
    indexAll: () => request('/api/repos/index-all', { method: 'POST' }),
    stats: (name: string) => request<RepoStats>(`/api/repos/${encodeURIComponent(name)}/stats`),
    files: (name: string, path = '') =>
      request<{ path: string; entries: { name: string; type: string; size?: number; path: string }[]; available?: boolean }>(
        `/api/repos/${encodeURIComponent(name)}/files?path=${encodeURIComponent(path)}`
      ),
  },
  github: {
    listRepos: () => request<GitHubRepo[]>('/api/github/repos'),
    searchRepos: (q: string) =>
      request<{ repos: GitHubRepo[]; total_count: number }>(`/api/github/search?q=${encodeURIComponent(q)}`),
    addRepo: (fullName: string, branch?: string) =>
      request<{ status: string; message: string; repo: unknown }>('/api/github/repos/add', {
        method: 'POST',
        body: JSON.stringify({ full_name: fullName, branch }),
      }),
  },
  gitlab: {
    listRepos: () => request<GitLabRepo[]>('/api/gitlab/repos'),
    searchRepos: (q: string) =>
      request<{ repos: GitLabRepo[]; total_count: number }>(`/api/gitlab/search?q=${encodeURIComponent(q)}`),
    addRepo: (fullName: string, branch?: string) =>
      request<{ status: string; message: string; repo: unknown }>('/api/gitlab/repos/add', {
        method: 'POST',
        body: JSON.stringify({ full_name: fullName, branch }),
      }),
  },
  memory: {
    create: (req: MemoryCreateRequest) =>
      request<{ status: string; memory: unknown }>('/api/memory', {
        method: 'POST',
        body: JSON.stringify(req),
      }),
    list: (limit = 50) => request<{ memories: Memory[]; count: number }>(`/api/memory?limit=${limit}`),
    search: (query: string, limit = 20) =>
      request<{ memories: Memory[]; count: number }>('/api/memory/search', {
        method: 'POST',
        body: JSON.stringify({ query, limit }),
      }),
    delete: (id: string) => request(`/api/memory/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  },
  kb: {
    formats: () => request<{ extensions: string[] }>('/api/kb/formats'),
    list: () => request<KbDocument[]>('/api/kb/documents'),
    get: (id: number) => request<KbDocument>(`/api/kb/documents/${id}`),
    chunks: (id: number, limit = 50) =>
      request<{ document_id: number; chunks: { chunk_index: number; content: string }[]; count: number }>(
        `/api/kb/documents/${id}/chunks?limit=${limit}`
      ),
    upload: (files: File[]) => {
      const form = new FormData()
      for (const file of files) form.append('files', file, file.name)
      return uploadRequest<KbUploadResult>('/api/kb/documents', form)
    },
    reprocess: (id: number) =>
      request<{ status: string; id: number }>(`/api/kb/documents/${id}/reprocess`, { method: 'POST' }),
    delete: (id: number) =>
      request<{ status: string; deleted: number }>(`/api/kb/documents/${id}`, { method: 'DELETE' }),
    download: (id: number) => downloadRequest(`/api/kb/documents/${id}/download`),
    search: (query: string, limit = 10) =>
      request<{ results: KbSearchResult[]; count: number }>('/api/kb/search', {
        method: 'POST',
        body: JSON.stringify({ query, limit }),
      }),
  },
  web: {
    list: () => request<WebPage[]>('/api/web/pages'),
    add: (urls: string[]) =>
      request<WebAddResult>('/api/web/pages', {
        method: 'POST',
        body: JSON.stringify({ urls }),
      }),
    refetch: (id: number) =>
      request<{ status: string; id: number }>(`/api/web/pages/${id}/refetch`, { method: 'POST' }),
    delete: (id: number) =>
      request<{ status: string; deleted: number }>(`/api/web/pages/${id}`, { method: 'DELETE' }),
    chunks: (id: number, limit = 50) =>
      request<{ page_id: number; chunks: { chunk_index: number; content: string }[]; count: number }>(
        `/api/web/pages/${id}/chunks?limit=${limit}`
      ),
    search: (query: string, limit = 10) =>
      request<{ results: WebSearchResult[]; count: number }>('/api/web/search', {
        method: 'POST',
        body: JSON.stringify({ query, limit }),
      }),
    sites: {
      list: () => request<WebSite[]>('/api/web/sites'),
      add: (rootUrl: string, maxPages?: number, excludePatterns?: string[]) =>
        request<{ status: string; site: WebSite }>('/api/web/sites', {
          method: 'POST',
          body: JSON.stringify({
            root_url: rootUrl,
            ...(maxPages ? { max_pages: maxPages } : {}),
            ...(excludePatterns ? { exclude_patterns: excludePatterns } : {}),
          }),
        }),
      update: (id: number, patch: { max_pages?: number; exclude_patterns?: string[] }) =>
        request<WebSite>(`/api/web/sites/${id}`, {
          method: 'PATCH',
          body: JSON.stringify(patch),
        }),
      recrawl: (id: number) =>
        request<{ status: string; id: number }>(`/api/web/sites/${id}/recrawl`, {
          method: 'POST',
        }),
      pages: (id: number) => request<WebPage[]>(`/api/web/sites/${id}/pages`),
      delete: (id: number) =>
        request<{ status: string; deleted: number }>(`/api/web/sites/${id}`, {
          method: 'DELETE',
        }),
    },
  },
  tools: {
    list: () => request<{ tools: Tool[]; count: number }>('/api/tools'),
  },
  chat: {
    send: (messages: ChatMessage[]) =>
      request<ChatResponse>('/api/chat', {
        method: 'POST',
        body: JSON.stringify({ messages }),
      }),
    models: () => request<ChatModelsResponse>('/api/chat/models'),
    // Streams SSE frames from POST /api/chat/stream, invoking onEvent per frame.
    // Uses fetch (not EventSource) because auth/org headers are required.
    stream: async (
      payload: { messages: ChatMessage[]; provider?: string; model?: string },
      onEvent: (ev: ChatStreamEvent) => void,
      signal?: AbortSignal
    ): Promise<void> => {
      const res = await fetch(`${BASE}/api/chat/stream`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal,
      })
      if (!res.ok || !res.body) {
        const text = await res.text()
        throw new Error(`${res.status} ${res.statusText}: ${text}`)
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        let sep: number
        while ((sep = buf.indexOf('\n\n')) !== -1) {
          const frame = buf.slice(0, sep)
          buf = buf.slice(sep + 2)
          for (const line of frame.split('\n')) {
            if (!line.startsWith('data: ')) continue
            try {
              onEvent(JSON.parse(line.slice(6)) as ChatStreamEvent)
            } catch {
              // Ignore malformed frames rather than killing the stream.
            }
          }
        }
      }
    },
    sessions: {
      list: () => request<{ sessions: ChatSessionSummary[] }>('/api/chat/sessions'),
      get: (id: number) => request<ChatSessionDetail>(`/api/chat/sessions/${id}`),
      create: (turns: StoredChatTurn[], title?: string) =>
        request<{ status: string; session: ChatSessionSummary }>('/api/chat/sessions', {
          method: 'POST',
          body: JSON.stringify({ turns, title }),
        }),
      update: (id: number, turns: StoredChatTurn[], title?: string) =>
        request<{ status: string; session: ChatSessionSummary }>(`/api/chat/sessions/${id}`, {
          method: 'PUT',
          body: JSON.stringify({ turns, title }),
        }),
      delete: (id: number) =>
        request<{ status: string }>(`/api/chat/sessions/${id}`, { method: 'DELETE' }),
      share: (id: number) =>
        request<{ status: string; share_token: string; shared_at?: string }>(
          `/api/chat/sessions/${id}/share`,
          { method: 'POST' }
        ),
      unshare: (id: number) =>
        request<{ status: string }>(`/api/chat/sessions/${id}/share`, { method: 'DELETE' }),
    },
    shared: (token: string) =>
      request<SharedChatResponse>(
        `/api/chat/shared/${encodeURIComponent(token)}`,
        undefined,
        false
      ),
  },
  jobs: {
    list: (limit = 50) => request<{ jobs: Job[]; count: number }>(`/api/jobs?limit=${limit}`),
    get: (id: string) => request<Job>(`/api/jobs/${encodeURIComponent(id)}`),
  },
  settings: {
    get: () => request<{ forge_config: Record<string, unknown>; settings_overrides: Record<string, unknown>; settings_overrides_editable?: boolean }>('/api/settings'),
    update: (payload: { forge_config: Record<string, unknown>; settings_overrides: Record<string, unknown> }) =>
      request<SettingsUpdateResponse>('/api/settings', { method: 'PUT', body: JSON.stringify(payload) }),
  },
  datasources: {
    list: () => request<{ connections: DbConnection[]; engines: DbEngine[] }>('/api/datasources'),
    create: (req: DbConnectionRequest) =>
      request<{ status: string; connection: DbConnection }>('/api/datasources', {
        method: 'POST',
        body: JSON.stringify(req),
      }),
    update: (id: number, req: DbConnectionRequest) =>
      request<{ status: string; connection: DbConnection }>(`/api/datasources/${id}`, {
        method: 'PUT',
        body: JSON.stringify(req),
      }),
    delete: (id: number) => request<{ status: string }>(`/api/datasources/${id}`, { method: 'DELETE' }),
    test: (id: number) =>
      request<{ status: 'ok' | 'error'; error?: string; suggested_host?: string | null }>(
        `/api/datasources/${id}/test`,
        { method: 'POST' }
      ),
    schema: (id: number, schema?: string) =>
      request<DbSchemaOverview>(
        `/api/datasources/${id}/schema${schema ? `?schema=${encodeURIComponent(schema)}` : ''}`
      ),
    table: (id: number, table: string, opts?: { schema?: string; sampleRows?: number }) => {
      const params = new URLSearchParams()
      if (opts?.schema) params.set('schema', opts.schema)
      if (opts?.sampleRows) params.set('sample_rows', String(opts.sampleRows))
      const qs = params.toString()
      return request<DbTableDetail>(
        `/api/datasources/${id}/tables/${encodeURIComponent(table)}${qs ? `?${qs}` : ''}`
      )
    },
    annotations: (id: number) =>
      request<{ annotations: DbAnnotation[]; count: number }>(`/api/datasources/${id}/annotations`),
    saveAnnotations: (id: number, annotations: DbAnnotation[]) =>
      request<{ status: string; written: number }>(`/api/datasources/${id}/annotations`, {
        method: 'PUT',
        body: JSON.stringify({ annotations }),
      }),
    query: (id: number, sql: string, maxRows = 100) =>
      request<DbQueryResult>(`/api/datasources/${id}/query`, {
        method: 'POST',
        body: JSON.stringify({ sql, max_rows: maxRows }),
      }),
    log: (id: number, limit = 50) =>
      request<{ log: DbQueryLogEntry[]; count: number }>(`/api/datasources/${id}/log?limit=${limit}`),
  },
  contracts: {
    list: () => request<{ contracts: ApiContract[]; types: ApiContractType[] }>('/api/contracts'),
    create: (req: ApiContractCreateRequest) =>
      request<{ status: string; contract: ApiContract }>('/api/contracts', {
        method: 'POST',
        body: JSON.stringify(req),
      }),
    refresh: (id: number, rawSpec?: string) =>
      request<{ status: string; contract: ApiContract }>(`/api/contracts/${id}/refresh`, {
        method: 'POST',
        body: JSON.stringify({ raw_spec: rawSpec ?? null }),
      }),
    delete: (id: number) => request<{ status: string }>(`/api/contracts/${id}`, { method: 'DELETE' }),
    endpoints: (id: number, opts?: { tag?: string; search?: string }) => {
      const params = new URLSearchParams()
      if (opts?.tag) params.set('tag', opts.tag)
      if (opts?.search) params.set('search', opts.search)
      const qs = params.toString()
      return request<{ endpoints: ApiEndpointSummary[]; count: number }>(
        `/api/contracts/${id}/endpoints${qs ? `?${qs}` : ''}`
      )
    },
    endpoint: (id: number, method: string, path: string) =>
      request<ApiEndpointDetail>(
        `/api/contracts/${id}/endpoint?method=${encodeURIComponent(method)}&path=${encodeURIComponent(path)}`
      ),
    search: (q: string, limit = 100) =>
      request<{ endpoints: ApiEndpointSummary[]; count: number }>(
        `/api/contracts/search?q=${encodeURIComponent(q)}&limit=${limit}`
      ),
  },
  ci: {
    runs: (repoName: string, limit = 10) =>
      request<{ repo: string; runs: CiRun[]; count: number }>(
        `/api/ci/${encodeURIComponent(repoName)}/runs?limit=${limit}`
      ),
    failure: (repoName: string, runId?: number) =>
      request<CiFailureDetail>(
        `/api/ci/${encodeURIComponent(repoName)}/failure${runId ? `?run_id=${runId}` : ''}`
      ),
  },
  mcpKeys: {
    list: () => request<{ keys: MCPApiKey[] }>('/api/mcp/keys'),
    create: (req: MCPApiKeyCreateRequest) =>
      request<{ key: string; id: number; name: string; scope: string; expires_at: string | null }>('/api/mcp/keys', {
        method: 'POST',
        body: JSON.stringify(req),
      }),
    revoke: (keyId: number) => request<{ status: string }>(`/api/mcp/keys/${keyId}`, { method: 'DELETE' }),
  },
  telegram: {
    registerWebhook: (url: string) =>
      request<{ status: string; result: Record<string, unknown> }>('/api/telegram/register-webhook', {
        method: 'POST',
        body: JSON.stringify({ url }),
      }),
    getWebhookInfo: () =>
      request<{ status: string; url: string; has_custom_certificate: boolean; pending_update_count: number; last_error_date?: number; last_error_message?: string }>('/api/telegram/webhook-info'),
  },
  health: () => request<{ status: string }>('/api/health'),
}
