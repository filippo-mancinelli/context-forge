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
  source: 'repositories' | 'memory' | 'knowledge_base'
  query: string
  result_count: number
  results: Record<string, unknown>[]
  error?: string | null
}

export interface ChatResponse {
  reply: string
  tool_calls: ChatToolCall[]
  sources_used: { repositories: boolean; memory: boolean; knowledge_base: boolean }
  model: string
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
    indexAll: () => request('/api/repos/index-all', { method: 'POST' }),
    stats: (name: string) => request<RepoStats>(`/api/repos/${encodeURIComponent(name)}/stats`),
    files: (name: string, path = '') =>
      request<{ path: string; entries: { name: string; type: string; size?: number; path: string }[] }>(
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
  tools: {
    list: () => request<{ tools: Tool[]; count: number }>('/api/tools'),
  },
  chat: {
    send: (messages: ChatMessage[]) =>
      request<ChatResponse>('/api/chat', {
        method: 'POST',
        body: JSON.stringify({ messages }),
      }),
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
  mcpKeys: {
    list: () => request<{ keys: MCPApiKey[] }>('/api/mcp/keys'),
    create: (req: MCPApiKeyCreateRequest) =>
      request<{ key: string; id: number; name: string; scope: string; expires_at: string | null }>('/api/mcp/keys', {
        method: 'POST',
        body: JSON.stringify(req),
      }),
    revoke: (keyId: number) => request<{ status: string }>(`/api/mcp/keys/${keyId}`, { method: 'DELETE' }),
  },
  health: () => request<{ status: string }>('/api/health'),
}
