const BASE = import.meta.env.VITE_API_URL || ''

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${text}`)
  }
  return res.json()
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

export interface Memory {
  id: string
  memory: string
  metadata?: Record<string, unknown>
  score?: number
  created_at?: string
}

export interface Tool {
  name: string
  description: string
}

export interface Job {
  id: string
  tool: string
  status: 'pending' | 'running' | 'done' | 'error'
  error_message?: string
  created_at: string
  updated_at: string
}

export const api = {
  repos: {
    list: () => request<Repo[]>('/api/repos'),
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
    syncConfig: () => request('/api/repos/sync-config', { method: 'POST' }),
    stats: (name: string) => request<RepoStats>(`/api/repos/${encodeURIComponent(name)}/stats`),
    files: (name: string, path = '') =>
      request<{ path: string; entries: { name: string; type: string; size?: number; path: string }[] }>(
        `/api/repos/${encodeURIComponent(name)}/files?path=${encodeURIComponent(path)}`
      ),
  },
  memory: {
    list: (limit = 50) => request<{ memories: Memory[]; count: number }>(`/api/memory?limit=${limit}`),
    search: (query: string, limit = 20) =>
      request<{ memories: Memory[]; count: number }>('/api/memory/search', {
        method: 'POST',
        body: JSON.stringify({ query, limit }),
      }),
    delete: (id: string) => request(`/api/memory/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  },
  tools: {
    list: () => request<{ tools: Tool[]; count: number }>('/api/tools'),
  },
  jobs: {
    list: (limit = 50) => request<{ jobs: Job[]; count: number }>(`/api/jobs?limit=${limit}`),
    get: (id: string) => request<Job>(`/api/jobs/${encodeURIComponent(id)}`),
  },
  health: () => request<{ status: string }>('/api/health'),
}
