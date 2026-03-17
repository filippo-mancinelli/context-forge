import { useEffect, useState, useCallback } from 'react'
import {
  Loader2, Save, SlidersHorizontal, Key, Database, GitBranch, Settings2,
  Plus, Trash2, Edit2, X, Check, Github, AlertCircle, RefreshCw,
  ExternalLink, HardDrive, GitlabIcon
} from 'lucide-react'
import { api, type Repo, type GitHubRepo, type RepoCreateRequest } from '../lib/api'

type Tab = 'repos' | 'api-keys' | 'embeddings' | 'advanced'

interface SettingsData {
  forge_config: {
    repos: Repo[]
    memory: { user_id: string }
    indexing: {
      auto: boolean
      schedule: string
      exclude: string[]
      max_file_size_kb: number
      chunk_size: number
      chunk_overlap: number
    }
  }
  settings_overrides: {
    openai_api_key?: string
    anthropic_api_key?: string
    deepseek_api_key?: string
    embeddings_provider?: string
    embeddings_model?: string
    embeddings_dims?: number
    embeddings_api_key?: string
    embeddings_base_url?: string
    llm_provider?: string
    llm_model?: string
    github_token?: string
    gitlab_token?: string
  }
}

// --- Components ---

function Input({
  label,
  value,
  onChange,
  type = 'text',
  placeholder = '',
  disabled = false,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  type?: string
  placeholder?: string
  disabled?: boolean
}) {
  return (
    <div className="mb-4">
      <label className="block text-xs font-medium text-gray-400 mb-1.5">{label}</label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        className="w-full px-3 py-2 bg-gray-900 border border-gray-700 rounded-lg text-sm text-gray-200 
                   placeholder-gray-600 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500
                   disabled:opacity-50 disabled:cursor-not-allowed"
      />
    </div>
  )
}

function Select({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  options: { value: string; label: string }[]
}) {
  return (
    <div className="mb-4">
      <label className="block text-xs font-medium text-gray-400 mb-1.5">{label}</label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full px-3 py-2 bg-gray-900 border border-gray-700 rounded-lg text-sm text-gray-200 
                   focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
      >
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
    </div>
  )
}

function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string
  checked: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <div className="flex items-center justify-between mb-4">
      <span className="text-sm text-gray-300">{label}</span>
      <button
        onClick={() => onChange(!checked)}
        className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
          checked ? 'bg-indigo-600' : 'bg-gray-700'
        }`}
      >
        <span
          className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
            checked ? 'translate-x-5' : 'translate-x-1'
          }`}
        />
      </button>
    </div>
  )
}

// --- Repo Editor Modal ---

function RepoModal({
  repo,
  onSave,
  onClose,
}: {
  repo?: Repo
  onSave: (r: RepoCreateRequest) => void
  onClose: () => void
}) {
  const [name, setName] = useState(repo?.name || '')
  const [type, setType] = useState<'local' | 'github' | 'gitlab'>(repo?.type || 'github')
  const [url, setUrl] = useState(repo?.url || '')
  const [path, setPath] = useState(repo?.path || '')
  const [branch, setBranch] = useState(repo?.branch || 'main')
  const [language, setLanguage] = useState(repo?.language || 'auto')

  const handleSave = () => {
    onSave({
      name,
      type,
      url: url || undefined,
      path: path || undefined,
      branch,
      language,
    })
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="w-full max-w-md bg-gray-900 border border-gray-800 rounded-xl shadow-2xl p-6">
        <div className="flex items-center justify-between mb-5">
          <h3 className="text-lg font-semibold text-white">
            {repo ? 'Edit Repository' : 'Add Repository'}
          </h3>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300">
            <X className="w-5 h-5" />
          </button>
        </div>

        <Input label="Name" value={name} onChange={setName} placeholder="my-repo" />

        <div className="mb-4">
          <label className="block text-xs font-medium text-gray-400 mb-1.5">Type</label>
          <div className="flex gap-2">
            {(['github', 'gitlab', 'local'] as const).map((t) => (
              <button
                key={t}
                onClick={() => setType(t)}
                className={`flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm capitalize transition-colors ${
                  type === t
                    ? 'bg-indigo-600 text-white'
                    : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
                }`}
              >
                {t === 'github' && <Github className="w-4 h-4" />}
                {t === 'gitlab' && <GitlabIcon className="w-4 h-4" />}
                {t === 'local' && <HardDrive className="w-4 h-4" />}
                {t}
              </button>
            ))}
          </div>
        </div>

        {type !== 'local' ? (
          <Input
            label="Repository URL"
            value={url}
            onChange={setUrl}
            placeholder={type === 'github' ? 'https://github.com/owner/repo' : 'https://gitlab.com/owner/repo'}
          />
        ) : (
          <Input
            label="Local Path (container path)"
            value={path}
            onChange={setPath}
            placeholder="/repos/my-project"
          />
        )}

        <div className="grid grid-cols-2 gap-4">
          <Input label="Branch" value={branch} onChange={setBranch} placeholder="main" />
          <Input label="Language" value={language} onChange={setLanguage} placeholder="auto" />
        </div>

        <div className="flex gap-3 mt-6">
          <button
            onClick={onClose}
            className="flex-1 px-4 py-2 text-sm text-gray-300 bg-gray-800 hover:bg-gray-700 rounded-lg transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={!name || (!url && !path)}
            className="flex-1 px-4 py-2 text-sm text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors disabled:opacity-50"
          >
            {repo ? 'Update' : 'Add'}
          </button>
        </div>
      </div>
    </div>
  )
}

// --- GitHub Browser Modal ---

function GitHubBrowserModal({
  onAdd,
  onClose,
}: {
  onAdd: (repo: GitHubRepo) => void
  onClose: () => void
}) {
  const [repos, setRepos] = useState<GitHubRepo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [adding, setAdding] = useState<number | null>(null)

  useEffect(() => {
    loadRepos()
  }, [])

  const loadRepos = async () => {
    try {
      setLoading(true)
      const data = await api.github.listRepos()
      setRepos(data)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  const handleSearch = async () => {
    if (!searchQuery.trim()) {
      loadRepos()
      return
    }
    try {
      setLoading(true)
      const data = await api.github.searchRepos(searchQuery)
      setRepos(data.repos)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  const handleAdd = async (repo: GitHubRepo) => {
    setAdding(repo.id)
    try {
      await api.github.addRepo(repo.full_name, repo.default_branch)
      onAdd(repo)
    } catch (e) {
      setError(String(e))
    } finally {
      setAdding(null)
    }
  }

  const filteredRepos = repos.filter(
    (r) =>
      r.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      r.full_name.toLowerCase().includes(searchQuery.toLowerCase())
  )

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="w-full max-w-2xl bg-gray-900 border border-gray-800 rounded-xl shadow-2xl flex flex-col max-h-[80vh]">
        <div className="flex items-center justify-between p-5 border-b border-gray-800">
          <div>
            <h3 className="text-lg font-semibold text-white flex items-center gap-2">
              <Github className="w-5 h-5" />
              Import from GitHub
            </h3>
            <p className="text-xs text-gray-500 mt-0.5">Select repositories to add to context-forge</p>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-4 border-b border-gray-800">
          <div className="flex gap-2">
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search repositories..."
              className="flex-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 
                       placeholder-gray-500 focus:outline-none focus:border-indigo-500"
              onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
            />
            <button
              onClick={handleSearch}
              disabled={loading}
              className="px-4 py-2 text-sm text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors disabled:opacity-50"
            >
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Search'}
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-auto p-2">
          {loading ? (
            <div className="flex items-center justify-center py-12 text-gray-500">
              <Loader2 className="w-5 h-5 animate-spin mr-2" />
              Loading repositories...
            </div>
          ) : error ? (
            <div className="flex items-center gap-2 p-4 text-sm text-red-400 bg-red-500/10 rounded-lg m-2">
              <AlertCircle className="w-4 h-4" />
              {error}
            </div>
          ) : filteredRepos.length === 0 ? (
            <div className="text-center py-12 text-gray-500">
              <Github className="w-10 h-10 mx-auto mb-3 opacity-40" />
              <p className="text-sm">No repositories found</p>
            </div>
          ) : (
            <div className="space-y-1">
              {filteredRepos.map((repo) => (
                <div
                  key={repo.id}
                  className="flex items-center gap-4 p-3 hover:bg-gray-800/50 rounded-lg group"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-white truncate">{repo.full_name}</span>
                      {repo.private && (
                        <span className="px-1.5 py-0.5 text-[10px] bg-yellow-500/20 text-yellow-400 rounded">
                          Private
                        </span>
                      )}
                      {repo.fork && (
                        <span className="px-1.5 py-0.5 text-[10px] bg-gray-700 text-gray-400 rounded">
                          Fork
                        </span>
                      )}
                    </div>
                    {repo.description && (
                      <p className="text-xs text-gray-500 truncate mt-0.5">{repo.description}</p>
                    )}
                    <div className="flex items-center gap-3 mt-1.5 text-xs text-gray-500">
                      {repo.language && <span>{repo.language}</span>}
                      <span>★ {repo.stargazers_count.toLocaleString()}</span>
                      <span className="text-gray-600">default: {repo.default_branch}</span>
                    </div>
                  </div>
                  <a
                    href={repo.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="p-2 text-gray-500 hover:text-gray-300 opacity-0 group-hover:opacity-100 transition-opacity"
                    title="View on GitHub"
                  >
                    <ExternalLink className="w-4 h-4" />
                  </a>
                  <button
                    onClick={() => handleAdd(repo)}
                    disabled={adding === repo.id}
                    className="px-3 py-1.5 text-xs text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors disabled:opacity-50"
                  >
                    {adding === repo.id ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <Plus className="w-3.5 h-3.5" />
                    )}
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// --- Repositories Tab ---

function RepositoriesTab({
  repos,
  onReposChange,
}: {
  repos: Repo[]
  onReposChange: () => void
}) {
  const [editingRepo, setEditingRepo] = useState<Repo | undefined>()
  const [showAddModal, setShowAddModal] = useState(false)
  const [showGitHubModal, setShowGitHubModal] = useState(false)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [indexing, setIndexing] = useState<string | null>(null)

  const handleSave = async (data: RepoCreateRequest) => {
    try {
      if (editingRepo) {
        await api.repos.update(editingRepo.name, data)
      } else {
        await api.repos.create(data)
      }
      setEditingRepo(undefined)
      setShowAddModal(false)
      onReposChange()
    } catch (e) {
      alert(String(e))
    }
  }

  const handleDelete = async (name: string) => {
    if (!confirm(`Delete repository "${name}"?`)) return
    setDeleting(name)
    try {
      await api.repos.delete(name)
      onReposChange()
    } catch (e) {
      alert(String(e))
    } finally {
      setDeleting(null)
    }
  }

  const handleIndex = async (name: string) => {
    setIndexing(name)
    try {
      await api.repos.index(name)
      onReposChange()
    } catch (e) {
      alert(String(e))
    } finally {
      setIndexing(null)
    }
  }

  const handleGitHubAdd = (repo: GitHubRepo) => {
    onReposChange()
    setShowGitHubModal(false)
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-5">
        <h3 className="text-sm font-medium text-gray-400">Configured Repositories</h3>
        <div className="flex gap-2">
          <button
            onClick={() => setShowGitHubModal(true)}
            className="inline-flex items-center gap-2 px-3 py-1.5 text-sm text-white bg-gray-800 hover:bg-gray-700 rounded-lg transition-colors"
          >
            <Github className="w-4 h-4" />
            Import from GitHub
          </button>
          <button
            onClick={() => setShowAddModal(true)}
            className="inline-flex items-center gap-2 px-3 py-1.5 text-sm text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors"
          >
            <Plus className="w-4 h-4" />
            Add Repository
          </button>
        </div>
      </div>

      {repos.length === 0 ? (
        <div className="text-center py-12 bg-gray-900/50 rounded-xl border border-gray-800 border-dashed">
          <GitBranch className="w-10 h-10 mx-auto mb-3 text-gray-600" />
          <p className="text-sm text-gray-500">No repositories configured</p>
          <p className="text-xs text-gray-600 mt-1">Add a repository to get started</p>
        </div>
      ) : (
        <div className="space-y-2">
          {repos.map((repo) => (
            <div
              key={repo.name}
              className="flex items-center gap-4 p-4 bg-gray-900 rounded-lg border border-gray-800"
            >
              <div className="flex-shrink-0">
                {repo.type === 'github' && <Github className="w-5 h-5 text-gray-400" />}
                {repo.type === 'gitlab' && <GitlabIcon className="w-5 h-5 text-orange-400" />}
                {repo.type === 'local' && <HardDrive className="w-5 h-5 text-gray-500" />}
              </div>

              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-white">{repo.name}</span>
                  <span className="px-1.5 py-0.5 text-[10px] bg-gray-800 text-gray-400 rounded">
                    {repo.type}
                  </span>
                  <span
                    className={`px-1.5 py-0.5 text-[10px] rounded ${
                      repo.status === 'indexed'
                        ? 'bg-emerald-500/20 text-emerald-400'
                        : repo.status === 'indexing'
                        ? 'bg-indigo-500/20 text-indigo-400'
                        : repo.status === 'error'
                        ? 'bg-red-500/20 text-red-400'
                        : 'bg-yellow-500/20 text-yellow-400'
                    }`}
                  >
                    {repo.status}
                  </span>
                </div>
                <div className="text-xs text-gray-500 mt-0.5 truncate">
                  {repo.url || repo.path}
                  {repo.branch && ` • ${repo.branch}`}
                  {repo.total_chunks > 0 && ` • ${repo.total_chunks.toLocaleString()} chunks`}
                </div>
              </div>

              <div className="flex items-center gap-1">
                <button
                  onClick={() => handleIndex(repo.name)}
                  disabled={indexing === repo.name || repo.status === 'indexing'}
                  className="p-2 text-gray-500 hover:text-indigo-400 hover:bg-indigo-500/10 rounded-lg transition-colors disabled:opacity-50"
                  title="Re-index"
                >
                  <RefreshCw className={`w-4 h-4 ${indexing === repo.name ? 'animate-spin' : ''}`} />
                </button>
                <button
                  onClick={() => setEditingRepo(repo)}
                  className="p-2 text-gray-500 hover:text-gray-300 hover:bg-gray-800 rounded-lg transition-colors"
                  title="Edit"
                >
                  <Edit2 className="w-4 h-4" />
                </button>
                <button
                  onClick={() => handleDelete(repo.name)}
                  disabled={deleting === repo.name}
                  className="p-2 text-gray-500 hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-colors disabled:opacity-50"
                  title="Delete"
                >
                  {deleting === repo.name ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Trash2 className="w-4 h-4" />
                  )}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {(showAddModal || editingRepo) && (
        <RepoModal repo={editingRepo} onSave={handleSave} onClose={() => {
          setShowAddModal(false)
          setEditingRepo(undefined)
        }} />
      )}

      {showGitHubModal && (
        <GitHubBrowserModal onAdd={handleGitHubAdd} onClose={() => setShowGitHubModal(false)} />
      )}
    </div>
  )
}

// --- API Keys Tab ---

function ApiKeysTab({
  settings,
  onChange,
}: {
  settings: SettingsData['settings_overrides']
  onChange: (k: keyof SettingsData['settings_overrides'], v: string) => void
}) {
  const [showKeys, setShowKeys] = useState<Record<string, boolean>>({})

  const toggleKey = (key: string) => {
    setShowKeys((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  const maskValue = (value?: string) => {
    if (!value) return ''
    if (value.length <= 8) return '••••••••'
    return value.slice(0, 4) + '••••••••••••' + value.slice(-4)
  }

  const KeyInput = ({
    label,
    value,
    onChange,
    placeholder,
  }: {
    label: string
    value?: string
    onChange: (v: string) => void
    placeholder?: string
  }) => {
    const keyName = label.toLowerCase().replace(/\s+/g, '_')
    return (
      <div className="mb-4">
        <label className="block text-xs font-medium text-gray-400 mb-1.5">{label}</label>
        <div className="relative">
          <input
            type={showKeys[keyName] ? 'text' : 'password'}
            value={value || ''}
            onChange={(e) => onChange(e.target.value)}
            placeholder={placeholder}
            className="w-full px-3 py-2 pr-10 bg-gray-900 border border-gray-700 rounded-lg text-sm text-gray-200 
                     placeholder-gray-600 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
          />
          <button
            onClick={() => toggleKey(keyName)}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-500 hover:text-gray-300"
          >
            {showKeys[keyName] ? 'Hide' : 'Show'}
          </button>
        </div>
      </div>
    )
  }

  return (
    <div>
      <h3 className="text-sm font-medium text-gray-400 mb-5">API Keys & Tokens</h3>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="p-4 bg-gray-900 rounded-lg border border-gray-800">
          <h4 className="text-sm font-medium text-white mb-4 flex items-center gap-2">
            <Database className="w-4 h-4 text-indigo-400" />
            LLM Providers
          </h4>
          <KeyInput
            label="OpenAI API Key"
            value={settings.openai_api_key}
            onChange={(v) => onChange('openai_api_key', v)}
            placeholder="sk-..."
          />
          <KeyInput
            label="Anthropic API Key"
            value={settings.anthropic_api_key}
            onChange={(v) => onChange('anthropic_api_key', v)}
            placeholder="sk-ant-..."
          />
          <KeyInput
            label="DeepSeek API Key"
            value={settings.deepseek_api_key}
            onChange={(v) => onChange('deepseek_api_key', v)}
            placeholder="..."
          />
        </div>

        <div className="p-4 bg-gray-900 rounded-lg border border-gray-800">
          <h4 className="text-sm font-medium text-white mb-4 flex items-center gap-2">
            <GitBranch className="w-4 h-4 text-indigo-400" />
            Git Providers
          </h4>
          <KeyInput
            label="GitHub Token"
            value={settings.github_token}
            onChange={(v) => onChange('github_token', v)}
            placeholder="ghp_..."
          />
          <KeyInput
            label="GitLab Token"
            value={settings.gitlab_token}
            onChange={(v) => onChange('gitlab_token', v)}
            placeholder="glpat-..."
          />
        </div>
      </div>
    </div>
  )
}

// --- Embeddings Tab ---

function EmbeddingsTab({
  settings,
  onChange,
}: {
  settings: SettingsData['settings_overrides']
  onChange: (k: keyof SettingsData['settings_overrides'], v: string | number) => void
}) {
  return (
    <div>
      <h3 className="text-sm font-medium text-gray-400 mb-5">Embeddings Configuration</h3>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="p-4 bg-gray-900 rounded-lg border border-gray-800">
          <Select
            label="Provider"
            value={settings.embeddings_provider || 'openai'}
            onChange={(v) => onChange('embeddings_provider', v)}
            options={[
              { value: 'openai', label: 'OpenAI' },
              { value: 'jina', label: 'Jina' },
              { value: 'openai-compatible', label: 'OpenAI Compatible' },
              { value: 'local', label: 'Local (sentence-transformers)' },
            ]}
          />

          <Input
            label="Model"
            value={settings.embeddings_model || ''}
            onChange={(v) => onChange('embeddings_model', v)}
            placeholder="text-embedding-3-small"
          />

          <Input
            label="Dimensions"
            value={String(settings.embeddings_dims || 1536)}
            onChange={(v) => onChange('embeddings_dims', parseInt(v) || 1536)}
            type="number"
          />
        </div>

        <div className="p-4 bg-gray-900 rounded-lg border border-gray-800">
          <Input
            label="API Key (optional)"
            value={settings.embeddings_api_key || ''}
            onChange={(v) => onChange('embeddings_api_key', v)}
            placeholder="Leave empty to use provider key"
          />

          <Input
            label="Base URL (optional)"
            value={settings.embeddings_base_url || ''}
            onChange={(v) => onChange('embeddings_base_url', v)}
            placeholder="https://api.openai.com/v1"
          />

          <div className="mt-4 p-3 bg-gray-800/50 rounded-lg">
            <p className="text-xs text-gray-500">
              <strong className="text-gray-400">LLM Provider:</strong>{' '}
              {settings.llm_provider || 'openai'}
            </p>
            <p className="text-xs text-gray-500 mt-1">
              <strong className="text-gray-400">LLM Model:</strong>{' '}
              {settings.llm_model || 'gpt-4o-mini'}
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}

// --- Advanced Tab ---

function AdvancedTab({
  config,
  onChange,
}: {
  config: SettingsData['forge_config']
  onChange: (path: string, value: unknown) => void
}) {
  const [excludes, setExcludes] = useState(config.indexing.exclude.join('\n'))

  const handleExcludesChange = (value: string) => {
    setExcludes(value)
    onChange('indexing.exclude', value.split('\n').filter((s) => s.trim()))
  }

  return (
    <div>
      <h3 className="text-sm font-medium text-gray-400 mb-5">Advanced Configuration</h3>

      <div className="space-y-6">
        <div className="p-4 bg-gray-900 rounded-lg border border-gray-800">
          <h4 className="text-sm font-medium text-white mb-4">Indexing Settings</h4>

          <Toggle
            label="Auto-indexing"
            checked={config.indexing.auto}
            onChange={(v) => onChange('indexing.auto', v)}
          />

          <Input
            label="Schedule (cron expression)"
            value={config.indexing.schedule}
            onChange={(v) => onChange('indexing.schedule', v)}
            placeholder="0 */6 * * *"
          />

          <div className="grid grid-cols-3 gap-4">
            <Input
              label="Max file size (KB)"
              value={String(config.indexing.max_file_size_kb)}
              onChange={(v) => onChange('indexing.max_file_size_kb', parseInt(v) || 500)}
              type="number"
            />
            <Input
              label="Chunk size"
              value={String(config.indexing.chunk_size)}
              onChange={(v) => onChange('indexing.chunk_size', parseInt(v) || 400)}
              type="number"
            />
            <Input
              label="Chunk overlap"
              value={String(config.indexing.chunk_overlap)}
              onChange={(v) => onChange('indexing.chunk_overlap', parseInt(v) || 50)}
              type="number"
            />
          </div>
        </div>

        <div className="p-4 bg-gray-900 rounded-lg border border-gray-800">
          <h4 className="text-sm font-medium text-white mb-4">Exclusion Patterns</h4>
          <p className="text-xs text-gray-500 mb-2">One pattern per line (glob syntax)</p>
          <textarea
            value={excludes}
            onChange={(e) => handleExcludesChange(e.target.value)}
            rows={10}
            className="w-full px-3 py-2 text-xs font-mono bg-gray-800 border border-gray-700 rounded-lg text-gray-300
                     placeholder-gray-600 focus:outline-none focus:border-indigo-500"
          />
        </div>

        <div className="p-4 bg-gray-900 rounded-lg border border-gray-800">
          <h4 className="text-sm font-medium text-white mb-4">Memory Settings</h4>
          <Input
            label="User ID"
            value={config.memory.user_id}
            onChange={(v) => onChange('memory.user_id', v)}
            placeholder="default"
          />
        </div>
      </div>
    </div>
  )
}

// --- Main Settings Component ---

export default function Settings() {
  const [activeTab, setActiveTab] = useState<Tab>('repos')
  const [data, setData] = useState<SettingsData | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const d = await api.settings.get()
      setData(d as SettingsData)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const handleSave = async () => {
    if (!data) return
    setSaving(true)
    setError(null)
    setSuccess(null)
    try {
      await api.settings.update({
        forge_config: data.forge_config,
        settings_overrides: data.settings_overrides,
      })
      setSuccess('Settings saved successfully')
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  const updateSettingsOverride = (key: keyof SettingsData['settings_overrides'], value: unknown) => {
    if (!data) return
    setData({
      ...data,
      settings_overrides: { ...data.settings_overrides, [key]: value },
    })
  }

  const updateForgeConfig = (path: string, value: unknown) => {
    if (!data) return
    const parts = path.split('.')
    const newConfig = { ...data.forge_config }
    let current: Record<string, unknown> = newConfig
    for (let i = 0; i < parts.length - 1; i++) {
      current[parts[i]] = { ...(current[parts[i]] as Record<string, unknown>) }
      current = current[parts[i]] as Record<string, unknown>
    }
    current[parts[parts.length - 1]] = value
    setData({ ...data, forge_config: newConfig as SettingsData['forge_config'] })
  }

  const tabs: { id: Tab; label: string; icon: typeof GitBranch }[] = [
    { id: 'repos', label: 'Repositories', icon: GitBranch },
    { id: 'api-keys', label: 'API Keys', icon: Key },
    { id: 'embeddings', label: 'Embeddings', icon: Database },
    { id: 'advanced', label: 'Advanced', icon: Settings2 },
  ]

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center">
        <Loader2 className="w-6 h-6 animate-spin text-indigo-500" />
      </div>
    )
  }

  if (!data) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center text-red-400">
        Failed to load settings
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-950">
      <div className="max-w-6xl mx-auto p-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-2xl font-semibold text-white flex items-center gap-3">
              <SlidersHorizontal className="w-6 h-6 text-indigo-400" />
              Settings
            </h1>
            <p className="text-sm text-gray-500 mt-1">
              Manage repositories, API keys, and application configuration
            </p>
          </div>
          <button
            onClick={handleSave}
            disabled={saving}
            className="inline-flex items-center gap-2 px-5 py-2.5 text-sm font-medium text-white 
                     bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors disabled:opacity-50"
          >
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            Save Changes
          </button>
        </div>

        {/* Alerts */}
        {error && (
          <div className="mb-6 p-4 bg-red-500/10 border border-red-500/30 rounded-xl flex items-center gap-3 text-red-400">
            <AlertCircle className="w-5 h-5" />
            <span className="text-sm">{error}</span>
          </div>
        )}
        {success && (
          <div className="mb-6 p-4 bg-emerald-500/10 border border-emerald-500/30 rounded-xl flex items-center gap-3 text-emerald-400">
            <Check className="w-5 h-5" />
            <span className="text-sm">{success}</span>
          </div>
        )}

        {/* Tabs */}
        <div className="flex gap-1 p-1 bg-gray-900 rounded-xl border border-gray-800 mb-6">
          {tabs.map((tab) => {
            const Icon = tab.icon
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium rounded-lg transition-all ${
                  activeTab === tab.id
                    ? 'bg-indigo-600 text-white'
                    : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800'
                }`}
              >
                <Icon className="w-4 h-4" />
                {tab.label}
              </button>
            )
          })}
        </div>

        {/* Content */}
        <div className="bg-gray-900/50 rounded-xl border border-gray-800 p-6">
          {activeTab === 'repos' && (
            <RepositoriesTab
              repos={data.forge_config.repos}
              onReposChange={load}
            />
          )}
          {activeTab === 'api-keys' && (
            <ApiKeysTab settings={data.settings_overrides} onChange={updateSettingsOverride} />
          )}
          {activeTab === 'embeddings' && (
            <EmbeddingsTab settings={data.settings_overrides} onChange={updateSettingsOverride} />
          )}
          {activeTab === 'advanced' && (
            <AdvancedTab config={data.forge_config} onChange={updateForgeConfig} />
          )}
        </div>
      </div>
    </div>
  )
}
