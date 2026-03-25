import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  Check,
  Database,
  GitBranch,
  Github,
  GitlabIcon,
  HardDrive,
  Key,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  Settings2,
  SlidersHorizontal,
  Trash2,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { api, type Repo, type RepoCreateRequest } from '../lib/api'

type Tab = 'repositories' | 'access' | 'models' | 'runtime'

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

function Field({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-gray-500">{label}</span>
      {children}
    </label>
  )
}

function Input({
  value,
  onChange,
  placeholder,
  type = 'text',
}: {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  type?: string
}) {
  return (
    <input
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className="w-full rounded-xl border border-gray-700 bg-gray-950 px-3 py-2.5 text-sm text-gray-100 placeholder-gray-500 outline-none transition-colors focus:border-cyan-500"
    />
  )
}

function Select({
  value,
  onChange,
  options,
}: {
  value: string
  onChange: (value: string) => void
  options: { value: string; label: string }[]
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-full rounded-xl border border-gray-700 bg-gray-950 px-3 py-2.5 text-sm text-gray-100 outline-none transition-colors focus:border-cyan-500"
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  )
}

function Toggle({
  checked,
  onChange,
}: {
  checked: boolean
  onChange: (value: boolean) => void
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${checked ? 'bg-cyan-600' : 'bg-gray-700'}`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${checked ? 'translate-x-6' : 'translate-x-1'}`}
      />
    </button>
  )
}

function SecretInput({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string
  value?: string
  onChange: (value: string) => void
  placeholder: string
}) {
  const [visible, setVisible] = useState(false)
  return (
    <Field label={label}>
      <div className="relative">
        <Input
          value={value || ''}
          onChange={onChange}
          placeholder={placeholder}
          type={visible ? 'text' : 'password'}
        />
        <button
          type="button"
          onClick={() => setVisible((current) => !current)}
          className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-500 hover:text-gray-300"
        >
          {visible ? 'Hide' : 'Show'}
        </button>
      </div>
    </Field>
  )
}

function RepoTypeIcon({ type }: { type: Repo['type'] }) {
  if (type === 'github') return <Github className="h-4 w-4 text-gray-400" />
  if (type === 'gitlab') return <GitlabIcon className="h-4 w-4 text-orange-400" />
  return <HardDrive className="h-4 w-4 text-gray-500" />
}

function RepoModal({
  repo,
  onClose,
  onSave,
}: {
  repo?: Repo
  onClose: () => void
  onSave: (value: RepoCreateRequest) => void
}) {
  const [name, setName] = useState(repo?.name || '')
  const [type, setType] = useState<Repo['type']>(repo?.type || 'local')
  const [url, setUrl] = useState(repo?.url || '')
  const [path, setPath] = useState(repo?.path || '')
  const [branch, setBranch] = useState(repo?.branch || 'main')
  const [language, setLanguage] = useState(repo?.language || 'auto')

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
      <div className="w-full max-w-xl rounded-2xl border border-gray-800 bg-gray-950 p-6 shadow-2xl">
        <h3 className="text-lg font-semibold text-white">{repo ? 'Edit repository' : 'Add repository'}</h3>
        <p className="mt-1 text-sm text-gray-400">Manual repository entries are saved directly to runtime config.</p>

        <div className="mt-5 grid gap-4 md:grid-cols-2">
          <Field label="Name">
            <Input value={name} onChange={setName} placeholder="my-repo" />
          </Field>
          <Field label="Type">
            <Select
              value={type}
              onChange={(value) => setType(value as Repo['type'])}
              options={[
                { value: 'local', label: 'Local' },
                { value: 'github', label: 'GitHub' },
                { value: 'gitlab', label: 'GitLab' },
              ]}
            />
          </Field>
          {type === 'local' ? (
            <div className="md:col-span-2">
              <Field label="Local path">
                <Input value={path} onChange={setPath} placeholder="/repos/project" />
              </Field>
            </div>
          ) : (
            <div className="md:col-span-2">
              <Field label="Repository URL">
                <Input value={url} onChange={setUrl} placeholder={`https://${type}.com/owner/repo`} />
              </Field>
            </div>
          )}
          <Field label="Branch">
            <Input value={branch} onChange={setBranch} placeholder="main" />
          </Field>
          <Field label="Language">
            <Input value={language} onChange={setLanguage} placeholder="auto" />
          </Field>
        </div>

        <div className="mt-6 flex justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl border border-gray-700 bg-gray-900 px-4 py-2 text-sm text-gray-300"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() =>
              onSave({
                name,
                type,
                url: url || undefined,
                path: path || undefined,
                branch,
                language,
              })
            }
            disabled={!name || (type === 'local' ? !path : !url)}
            className="rounded-xl bg-cyan-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {repo ? 'Save changes' : 'Add repository'}
          </button>
        </div>
      </div>
    </div>
  )
}

function RepositoriesTab({
  repos,
  onReload,
}: {
  repos: Repo[]
  onReload: () => void
}) {
  const [editingRepo, setEditingRepo] = useState<Repo | undefined>()
  const [showAddModal, setShowAddModal] = useState(false)
  const [deletingRepo, setDeletingRepo] = useState<string | null>(null)
  const [indexingRepo, setIndexingRepo] = useState<string | null>(null)

  const handleSave = async (payload: RepoCreateRequest) => {
    try {
      if (editingRepo) {
        await api.repos.update(editingRepo.name, payload)
      } else {
        await api.repos.create(payload)
      }
      setEditingRepo(undefined)
      setShowAddModal(false)
      onReload()
    } catch (e) {
      window.alert(String(e))
    }
  }

  const handleDelete = async (repoName: string) => {
    if (!window.confirm(`Remove repository "${repoName}" from runtime config?`)) return
    setDeletingRepo(repoName)
    try {
      await api.repos.delete(repoName)
      onReload()
    } catch (e) {
      window.alert(String(e))
    } finally {
      setDeletingRepo(null)
    }
  }

  const handleIndex = async (repoName: string) => {
    setIndexingRepo(repoName)
    try {
      await api.repos.index(repoName)
      onReload()
    } catch (e) {
      window.alert(String(e))
    } finally {
      setIndexingRepo(null)
    }
  }

  return (
    <div className="space-y-5">
      <div className="rounded-2xl border border-gray-800 bg-gray-900/70 p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.18em] text-cyan-400">Runtime Repositories</p>
            <h2 className="mt-1 text-lg font-semibold text-white">Edit manual entries, import remotes from Repositories</h2>
            <p className="mt-1 text-sm text-gray-400">
              Local paths and manual URLs can be managed here. GitHub and GitLab browsing lives on the main Repositories page.
            </p>
          </div>
          <div className="flex flex-col gap-3 sm:flex-row">
            <Link
              to="/repos"
              className="inline-flex items-center justify-center rounded-xl border border-gray-700 bg-gray-950 px-4 py-2.5 text-sm text-gray-300 transition-colors hover:bg-gray-900"
            >
              Open Repositories home
            </Link>
            <button
              type="button"
              onClick={() => setShowAddModal(true)}
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-cyan-600 px-4 py-2.5 text-sm font-medium text-white"
            >
              <Plus className="h-4 w-4" />
              Add manual repository
            </button>
          </div>
        </div>
      </div>

      {repos.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-gray-800 bg-gray-900/40 p-12 text-center text-gray-500">
          No repositories configured yet.
        </div>
      ) : (
        <div className="overflow-hidden rounded-2xl border border-gray-800 bg-gray-900/60">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800 text-left text-xs uppercase tracking-wider text-gray-500">
                <th className="px-5 py-3 font-medium">Repository</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Source</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {repos.map((repo) => (
                <tr key={repo.name} className="hover:bg-gray-800/30">
                  <td className="px-5 py-4">
                    <div className="flex items-center gap-3">
                      <RepoTypeIcon type={repo.type} />
                      <div>
                        <p className="font-medium text-white">{repo.name}</p>
                        <p className="font-mono text-xs text-gray-500">{repo.url || repo.path || '-'}</p>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-4 text-xs text-gray-400">
                    {repo.status}
                    {repo.total_chunks > 0 ? ` - ${repo.total_chunks.toLocaleString()} chunks` : ''}
                  </td>
                  <td className="px-4 py-4 text-xs text-gray-500">{repo.branch}</td>
                  <td className="px-4 py-4 text-right">
                    <div className="inline-flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => handleIndex(repo.name)}
                        disabled={indexingRepo === repo.name || repo.status === 'indexing'}
                        className="rounded-lg border border-gray-700 bg-gray-950 px-3 py-1.5 text-xs text-gray-300 disabled:opacity-50"
                      >
                        {indexingRepo === repo.name ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                      </button>
                      <button
                        type="button"
                        onClick={() => setEditingRepo(repo)}
                        className="rounded-lg border border-gray-700 bg-gray-950 px-3 py-1.5 text-xs text-gray-300"
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDelete(repo.name)}
                        disabled={deletingRepo === repo.name}
                        className="rounded-lg border border-rose-500/20 bg-rose-500/10 px-3 py-1.5 text-xs text-rose-300 disabled:opacity-50"
                      >
                        {deletingRepo === repo.name ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {(showAddModal || editingRepo) && (
        <RepoModal
          repo={editingRepo}
          onClose={() => {
            setShowAddModal(false)
            setEditingRepo(undefined)
          }}
          onSave={handleSave}
        />
      )}
    </div>
  )
}

function AccessTab({
  settings,
  onChange,
}: {
  settings: SettingsData['settings_overrides']
  onChange: (key: keyof SettingsData['settings_overrides'], value: string) => void
}) {
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <section className="rounded-2xl border border-gray-800 bg-gray-900/70 p-5">
        <h2 className="text-sm font-medium text-white">LLM providers</h2>
        <p className="mt-1 text-sm text-gray-400">Used by the memory pipeline and by providers that need their own API keys.</p>
        <div className="mt-4 space-y-4">
          <SecretInput label="OpenAI API key" value={settings.openai_api_key} onChange={(value) => onChange('openai_api_key', value)} placeholder="sk-..." />
          <SecretInput label="Anthropic API key" value={settings.anthropic_api_key} onChange={(value) => onChange('anthropic_api_key', value)} placeholder="sk-ant-..." />
          <SecretInput label="DeepSeek API key" value={settings.deepseek_api_key} onChange={(value) => onChange('deepseek_api_key', value)} placeholder="..." />
        </div>
      </section>

      <section className="rounded-2xl border border-gray-800 bg-gray-900/70 p-5">
        <h2 className="text-sm font-medium text-white">Git provider tokens</h2>
        <p className="mt-1 text-sm text-gray-400">These tokens power GitHub and GitLab browsing on the Repositories page.</p>
        <div className="mt-4 space-y-4">
          <SecretInput label="GitHub token" value={settings.github_token} onChange={(value) => onChange('github_token', value)} placeholder="ghp_..." />
          <SecretInput label="GitLab token" value={settings.gitlab_token} onChange={(value) => onChange('gitlab_token', value)} placeholder="glpat-..." />
        </div>
      </section>
    </div>
  )
}

function ModelsTab({
  settings,
  onChange,
  embeddingRisk,
}: {
  settings: SettingsData['settings_overrides']
  onChange: (key: keyof SettingsData['settings_overrides'], value: string | number) => void
  embeddingRisk: { changed: boolean; dimsChanged: boolean }
}) {
  return (
    <div className="space-y-5">
      <div className="rounded-2xl border border-cyan-500/20 bg-cyan-500/10 p-5">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-cyan-300">Runtime-first control plane</p>
        <p className="mt-2 text-sm text-white">
          Use this page as the primary place to change providers, models, tokens, indexing behavior, and repositories after bootstrap.
        </p>
      </div>

      {embeddingRisk.changed && (
        <div className="rounded-2xl border border-amber-500/20 bg-amber-500/10 p-5 text-sm text-amber-100">
          <p className="font-medium text-amber-300">Embedding changes need follow-up work</p>
          <p className="mt-2">
            Changing the embeddings provider or model requires re-indexing repositories so semantic search uses the new vectors.
          </p>
          {embeddingRisk.dimsChanged && (
            <p className="mt-2">
              Changing embedding dimensions is more invasive: reset vector-backed data, restart the stack, and then re-index repositories before relying on search or memory.
            </p>
          )}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <section className="rounded-2xl border border-gray-800 bg-gray-900/70 p-5">
          <h2 className="text-sm font-medium text-white">Memory LLM</h2>
          <div className="mt-4 space-y-4">
            <Field label="LLM provider">
              <Select
                value={settings.llm_provider || 'openai'}
                onChange={(value) => onChange('llm_provider', value)}
                options={[
                  { value: 'openai', label: 'OpenAI' },
                  { value: 'anthropic', label: 'Anthropic' },
                  { value: 'deepseek', label: 'DeepSeek' },
                ]}
              />
            </Field>
            <Field label="LLM model">
              <Input value={settings.llm_model || ''} onChange={(value) => onChange('llm_model', value)} placeholder="gpt-4o-mini" />
            </Field>
          </div>
        </section>

        <section className="rounded-2xl border border-gray-800 bg-gray-900/70 p-5">
          <h2 className="text-sm font-medium text-white">Embeddings</h2>
          <div className="mt-4 space-y-4">
            <Field label="Provider">
              <Select
                value={settings.embeddings_provider || 'openai'}
                onChange={(value) => onChange('embeddings_provider', value)}
                options={[
                  { value: 'openai', label: 'OpenAI' },
                  { value: 'jina', label: 'Jina' },
                  { value: 'openai-compatible', label: 'OpenAI compatible' },
                  { value: 'local', label: 'Local' },
                ]}
              />
            </Field>
            <Field label="Model">
              <Input value={settings.embeddings_model || ''} onChange={(value) => onChange('embeddings_model', value)} placeholder="text-embedding-3-small" />
            </Field>
            <Field label="Dimensions">
              <Input
                type="number"
                value={String(settings.embeddings_dims || 1536)}
                onChange={(value) => onChange('embeddings_dims', parseInt(value, 10) || 1536)}
              />
            </Field>
          </div>
        </section>

        <section className="rounded-2xl border border-gray-800 bg-gray-900/70 p-5">
          <h2 className="text-sm font-medium text-white">Embedder connection</h2>
          <div className="mt-4 space-y-4">
            <SecretInput
              label="Dedicated embeddings key"
              value={settings.embeddings_api_key}
              onChange={(value) => onChange('embeddings_api_key', value)}
              placeholder="Leave empty to reuse provider key"
            />
            <Field label="OpenAI-compatible base URL">
              <Input
                value={settings.embeddings_base_url || ''}
                onChange={(value) => onChange('embeddings_base_url', value)}
                placeholder="https://api.openai.com/v1"
              />
            </Field>
          </div>
        </section>
      </div>
    </div>
  )
}

function RuntimeTab({
  config,
  onChange,
}: {
  config: SettingsData['forge_config']
  onChange: (path: string, value: unknown) => void
}) {
  const [excludeText, setExcludeText] = useState(config.indexing.exclude.join('\n'))

  useEffect(() => {
    setExcludeText(config.indexing.exclude.join('\n'))
  }, [config.indexing.exclude])

  return (
    <div className="space-y-6">
      <section className="rounded-2xl border border-gray-800 bg-gray-900/70 p-5">
        <h2 className="text-sm font-medium text-white">Indexing</h2>
        <div className="mt-4 grid gap-4 lg:grid-cols-3">
          <div className="flex items-center justify-between rounded-xl border border-gray-800 bg-gray-950/70 px-4 py-3 lg:col-span-3">
            <div>
              <p className="text-sm text-white">Auto indexing</p>
              <p className="text-xs text-gray-500">Use the configured schedule to re-index automatically.</p>
            </div>
            <Toggle checked={config.indexing.auto} onChange={(value) => onChange('indexing.auto', value)} />
          </div>
          <Field label="Schedule">
            <Input value={config.indexing.schedule} onChange={(value) => onChange('indexing.schedule', value)} placeholder="0 */6 * * *" />
          </Field>
          <Field label="Max file size (KB)">
            <Input
              type="number"
              value={String(config.indexing.max_file_size_kb)}
              onChange={(value) => onChange('indexing.max_file_size_kb', parseInt(value, 10) || 500)}
            />
          </Field>
          <Field label="Chunk size">
            <Input
              type="number"
              value={String(config.indexing.chunk_size)}
              onChange={(value) => onChange('indexing.chunk_size', parseInt(value, 10) || 400)}
            />
          </Field>
          <Field label="Chunk overlap">
            <Input
              type="number"
              value={String(config.indexing.chunk_overlap)}
              onChange={(value) => onChange('indexing.chunk_overlap', parseInt(value, 10) || 50)}
            />
          </Field>
          <div className="lg:col-span-3">
            <Field label="Exclude patterns">
              <textarea
                value={excludeText}
                onChange={(e) => {
                  setExcludeText(e.target.value)
                  onChange(
                    'indexing.exclude',
                    e.target.value
                      .split('\n')
                      .map((entry) => entry.trim())
                      .filter(Boolean)
                  )
                }}
                rows={8}
                className="w-full rounded-xl border border-gray-700 bg-gray-950 px-3 py-2.5 font-mono text-xs text-gray-200 outline-none transition-colors focus:border-cyan-500"
              />
            </Field>
          </div>
        </div>
      </section>

      <section className="rounded-2xl border border-gray-800 bg-gray-900/70 p-5">
        <h2 className="text-sm font-medium text-white">Memory defaults</h2>
        <div className="mt-4 max-w-sm">
          <Field label="Default user id">
            <Input value={config.memory.user_id} onChange={(value) => onChange('memory.user_id', value)} placeholder="default" />
          </Field>
        </div>
      </section>
    </div>
  )
}

function getEmbeddingSignature(settings: SettingsData['settings_overrides']) {
  return {
    provider: settings.embeddings_provider || 'openai',
    model: settings.embeddings_model || 'text-embedding-3-small',
    dims: settings.embeddings_dims || 1536,
  }
}

export default function Settings() {
  const [activeTab, setActiveTab] = useState<Tab>('repositories')
  const [data, setData] = useState<SettingsData | null>(null)
  const [baselineEmbeddingSignature, setBaselineEmbeddingSignature] = useState<ReturnType<typeof getEmbeddingSignature> | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [warnings, setWarnings] = useState<string[]>([])

  const load = useCallback(async () => {
    try {
      const next = (await api.settings.get()) as SettingsData
      setData(next)
      setBaselineEmbeddingSignature(getEmbeddingSignature(next.settings_overrides))
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const embeddingRisk = useMemo(() => {
    if (!data || !baselineEmbeddingSignature) {
      return { changed: false, dimsChanged: false }
    }
    const current = getEmbeddingSignature(data.settings_overrides)
    const dimsChanged = current.dims !== baselineEmbeddingSignature.dims
    const changed =
      dimsChanged ||
      current.provider !== baselineEmbeddingSignature.provider ||
      current.model !== baselineEmbeddingSignature.model
    return { changed, dimsChanged }
  }, [baselineEmbeddingSignature, data])

  const updateSettingsOverride = (key: keyof SettingsData['settings_overrides'], value: unknown) => {
    if (!data) return
    setData({
      ...data,
      settings_overrides: { ...data.settings_overrides, [key]: value },
    })
  }

  const updateForgeConfig = (path: string, value: unknown) => {
    if (!data) return
    const segments = path.split('.')
    const nextConfig = { ...data.forge_config } as Record<string, unknown>
    let current: Record<string, unknown> = nextConfig
    for (let index = 0; index < segments.length - 1; index += 1) {
      current[segments[index]] = { ...(current[segments[index]] as Record<string, unknown>) }
      current = current[segments[index]] as Record<string, unknown>
    }
    current[segments[segments.length - 1]] = value
    setData({ ...data, forge_config: nextConfig as SettingsData['forge_config'] })
  }

  const handleSave = async () => {
    if (!data) return
    if (
      embeddingRisk.dimsChanged &&
      !window.confirm(
        'Changing embedding dimensions requires resetting vector-backed data and re-indexing repositories. Save anyway?'
      )
    ) {
      return
    }

    setSaving(true)
    setError(null)
    setSuccess(null)
    setWarnings([])
    try {
      const result = await api.settings.update({
        forge_config: data.forge_config,
        settings_overrides: data.settings_overrides,
      })
      setBaselineEmbeddingSignature(getEmbeddingSignature(data.settings_overrides))
      setSuccess('Runtime settings saved successfully.')
      setWarnings(result.warnings)
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  const tabs: { id: Tab; label: string; icon: typeof GitBranch }[] = [
    { id: 'repositories', label: 'Repositories', icon: GitBranch },
    { id: 'access', label: 'API keys', icon: Key },
    { id: 'models', label: 'Models', icon: Database },
    { id: 'runtime', label: 'Runtime', icon: Settings2 },
  ]

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-950 text-gray-400">
        <Loader2 className="mr-2 h-5 w-5 animate-spin" />
        Loading settings...
      </div>
    )
  }

  if (!data) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-950 text-rose-400">
        Unable to load runtime settings.
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-950">
      <div className="mx-auto max-w-6xl p-8">
        <div className="mb-8 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.18em] text-cyan-400">Settings</p>
            <h1 className="mt-1 flex items-center gap-3 text-2xl font-semibold text-white">
              <SlidersHorizontal className="h-6 w-6 text-cyan-400" />
              Runtime configuration
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-gray-400">
              After bootstrap, this page is the primary control plane for providers, tokens, indexing behavior, and manual repository entries.
            </p>
          </div>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="inline-flex items-center justify-center gap-2 rounded-xl bg-cyan-600 px-5 py-3 text-sm font-medium text-white disabled:opacity-50"
          >
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            Save runtime settings
          </button>
        </div>

        <div className="mb-6 grid gap-4 lg:grid-cols-3">
          <div className="rounded-2xl border border-cyan-500/20 bg-cyan-500/10 p-4">
            <p className="text-xs font-medium uppercase tracking-[0.18em] text-cyan-300">Primary source of truth</p>
            <p className="mt-2 text-sm text-white">Runtime configuration in Postgres now drives the app.</p>
            <p className="mt-1 text-xs leading-relaxed text-cyan-100/80">Use files for bootstrap, recovery, and legacy import only.</p>
          </div>
          <div className="rounded-2xl border border-gray-800 bg-gray-900/70 p-4">
            <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-500">Remote-friendly</p>
            <p className="mt-2 text-sm text-white">Providers and tokens can be changed from the UI.</p>
            <p className="mt-1 text-xs leading-relaxed text-gray-400">No shell edits are required for day-to-day remote administration.</p>
          </div>
          <div className="rounded-2xl border border-gray-800 bg-gray-900/70 p-4">
            <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-500">Repositories first</p>
            <p className="mt-2 text-sm text-white">The main Repositories page is now the operational landing page.</p>
            <p className="mt-1 text-xs leading-relaxed text-gray-400">Use it for GitHub and GitLab browsing, indexing, and daily repo operations.</p>
          </div>
        </div>

        {error && (
          <div className="mb-4 rounded-xl border border-rose-500/30 bg-rose-500/10 p-4 text-sm text-rose-300">
            <div className="flex items-center gap-2">
              <AlertCircle className="h-4 w-4" />
              {error}
            </div>
          </div>
        )}
        {success && (
          <div className="mb-4 rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-4 text-sm text-emerald-300">
            <div className="flex items-center gap-2">
              <Check className="h-4 w-4" />
              {success}
            </div>
          </div>
        )}
        {warnings.length > 0 && (
          <div className="mb-6 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-200">
            {warnings.map((warning) => (
              <p key={warning}>{warning}</p>
            ))}
          </div>
        )}

        <div className="mb-6 flex flex-wrap gap-2 rounded-2xl border border-gray-800 bg-gray-900/70 p-2">
          {tabs.map((tab) => {
            const Icon = tab.icon
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={`inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm transition-colors ${
                  activeTab === tab.id
                    ? 'bg-cyan-600 text-white'
                    : 'text-gray-400 hover:bg-gray-800 hover:text-gray-200'
                }`}
              >
                <Icon className="h-4 w-4" />
                {tab.label}
              </button>
            )
          })}
        </div>

        <div className="rounded-2xl border border-gray-800 bg-gray-900/40 p-6">
          {activeTab === 'repositories' && <RepositoriesTab repos={data.forge_config.repos} onReload={load} />}
          {activeTab === 'access' && <AccessTab settings={data.settings_overrides} onChange={updateSettingsOverride} />}
          {activeTab === 'models' && (
            <ModelsTab settings={data.settings_overrides} onChange={updateSettingsOverride} embeddingRisk={embeddingRisk} />
          )}
          {activeTab === 'runtime' && <RuntimeTab config={data.forge_config} onChange={updateForgeConfig} />}
        </div>
      </div>
    </div>
  )
}
