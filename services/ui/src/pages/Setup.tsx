import { useState } from 'react'
import { Loader2, Plus, ShieldCheck, Trash2 } from 'lucide-react'
import { api } from '../lib/api'

export type SetupMode = 'full' | 'admin'

type SetupProps = {
  mode: SetupMode
  onCompleted: () => void
}

type RepoDraft = {
  name: string
  type: 'local' | 'github' | 'gitlab'
  path: string
  url: string
  branch: string
  language: string
}

function Section({
  title,
  description,
  children,
}: {
  title: string
  description: string
  children: React.ReactNode
}) {
  return (
    <section className="rounded-2xl border border-gray-800 bg-gray-900/70 p-5">
      <h2 className="text-sm font-medium text-white">{title}</h2>
      <p className="mt-1 text-sm text-gray-400">{description}</p>
      <div className="mt-4">{children}</div>
    </section>
  )
}

function TextInput({
  value,
  onChange,
  placeholder,
  type = 'text',
}: {
  value: string
  onChange: (value: string) => void
  placeholder: string
  type?: string
}) {
  return (
    <input
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className="w-full rounded-lg border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-100 placeholder-gray-500"
    />
  )
}

export default function Setup({ mode, onCompleted }: SetupProps) {
  const [bootstrapToken, setBootstrapToken] = useState('')
  const [adminUsername, setAdminUsername] = useState('admin')
  const [adminPassword, setAdminPassword] = useState('')
  const [memoryUserId, setMemoryUserId] = useState('default')
  const [indexingAuto, setIndexingAuto] = useState(true)
  const [indexingSchedule, setIndexingSchedule] = useState('0 */6 * * *')
  const [indexingExclude, setIndexingExclude] = useState('**/.git/**\n**/node_modules/**\n**/__pycache__/**')
  const [indexingMaxSize, setIndexingMaxSize] = useState(500)
  const [indexingChunkSize, setIndexingChunkSize] = useState(400)
  const [indexingChunkOverlap, setIndexingChunkOverlap] = useState(50)

  const [llmProvider, setLlmProvider] = useState('openai')
  const [llmModel, setLlmModel] = useState('gpt-4o-mini')
  const [embeddingsProvider, setEmbeddingsProvider] = useState('openai')
  const [embeddingsModel, setEmbeddingsModel] = useState('text-embedding-3-small')
  const [embeddingsDims, setEmbeddingsDims] = useState(1536)
  const [openAiKey, setOpenAiKey] = useState('')
  const [anthropicKey, setAnthropicKey] = useState('')
  const [deepseekKey, setDeepseekKey] = useState('')
  const [embeddingsApiKey, setEmbeddingsApiKey] = useState('')
  const [embeddingsBaseUrl, setEmbeddingsBaseUrl] = useState('')
  const [githubToken, setGithubToken] = useState('')
  const [gitlabToken, setGitlabToken] = useState('')

  const [repos, setRepos] = useState<RepoDraft[]>([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const addRepo = () =>
    setRepos((prev) => [
      ...prev,
      { name: '', type: 'local', path: '', url: '', branch: 'main', language: 'auto' },
    ])

  const handleSubmit = async () => {
    setSaving(true)
    setError(null)
    try {
      const payload =
        mode === 'full'
          ? {
              forge_config: {
                repos: repos
                  .filter((repo) => repo.name.trim())
                  .map((repo) => ({
                    name: repo.name.trim(),
                    type: repo.type,
                    ...(repo.type === 'local' ? { path: repo.path.trim() } : { url: repo.url.trim() }),
                    branch: repo.branch.trim() || 'main',
                    language: repo.language.trim() || 'auto',
                  })),
                memory: { user_id: memoryUserId.trim() || 'default' },
                indexing: {
                  auto: indexingAuto,
                  schedule: indexingSchedule.trim() || '0 */6 * * *',
                  exclude: indexingExclude
                    .split('\n')
                    .map((entry) => entry.trim())
                    .filter(Boolean),
                  max_file_size_kb: indexingMaxSize,
                  chunk_size: indexingChunkSize,
                  chunk_overlap: indexingChunkOverlap,
                },
              },
              settings_overrides: {
                llm_provider: llmProvider,
                llm_model: llmModel,
                embeddings_provider: embeddingsProvider,
                embeddings_model: embeddingsModel,
                embeddings_dims: embeddingsDims,
                openai_api_key: openAiKey.trim(),
                anthropic_api_key: anthropicKey.trim(),
                deepseek_api_key: deepseekKey.trim(),
                embeddings_api_key: embeddingsApiKey.trim(),
                embeddings_base_url: embeddingsBaseUrl.trim(),
                github_token: githubToken.trim(),
                gitlab_token: gitlabToken.trim(),
              },
            }
          : {}

      await api.setup.init({
        bootstrap_token: bootstrapToken.trim(),
        admin_username: adminUsername.trim(),
        admin_password: adminPassword,
        ...payload,
      })
      onCompleted()
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="min-h-screen bg-gray-950 px-6 py-8 text-gray-100">
      <div className="mx-auto max-w-5xl space-y-6">
        <div className="rounded-2xl border border-gray-800 bg-gradient-to-br from-gray-900 via-gray-900 to-gray-950 p-6">
          <div className="flex items-center gap-3">
            <ShieldCheck className="h-6 w-6 text-cyan-400" />
            <div>
              <h1 className="text-xl font-semibold text-white">
                {mode === 'full' ? 'First-time Runtime Setup' : 'Finish Admin Setup'}
              </h1>
              <p className="mt-1 text-sm text-gray-400">
                {mode === 'full'
                  ? 'Bootstrap context-forge once, then manage runtime configuration from the web UI.'
                  : 'Bootstrap configuration is already available. Create the admin account to activate the runtime-first control plane.'}
              </p>
            </div>
          </div>
        </div>

        {error && (
          <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-4 text-sm text-rose-300">
            {error}
          </div>
        )}

        <Section
          title="Admin Access"
          description="The bootstrap token authorizes setup on remote servers. The admin account is used for the web UI."
        >
          <div className="grid gap-3 md:grid-cols-3">
            <TextInput value={bootstrapToken} onChange={setBootstrapToken} placeholder="SETUP_BOOTSTRAP_TOKEN" />
            <TextInput value={adminUsername} onChange={setAdminUsername} placeholder="Admin username" />
            <TextInput value={adminPassword} onChange={setAdminPassword} placeholder="Admin password" type="password" />
          </div>
        </Section>

        {mode === 'full' && (
          <>
            <Section
              title="Runtime Defaults"
              description="These values seed the initial runtime configuration. After setup they can be changed from Settings."
            >
              <div className="grid gap-3 md:grid-cols-2">
                <TextInput value={memoryUserId} onChange={setMemoryUserId} placeholder="Memory user id" />
                <TextInput value={indexingSchedule} onChange={setIndexingSchedule} placeholder="Indexing schedule (cron)" />
                <label className="inline-flex items-center gap-2 rounded-lg border border-gray-800 bg-gray-950 px-3 py-2 text-sm text-gray-300">
                  <input type="checkbox" checked={indexingAuto} onChange={(e) => setIndexingAuto(e.target.checked)} />
                  Auto indexing
                </label>
                <div className="grid gap-3 md:grid-cols-3 md:col-span-2">
                  <input
                    type="number"
                    value={indexingMaxSize}
                    onChange={(e) => setIndexingMaxSize(Number(e.target.value))}
                    placeholder="Max file size KB"
                    className="rounded-lg border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-100"
                  />
                  <input
                    type="number"
                    value={indexingChunkSize}
                    onChange={(e) => setIndexingChunkSize(Number(e.target.value))}
                    placeholder="Chunk size"
                    className="rounded-lg border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-100"
                  />
                  <input
                    type="number"
                    value={indexingChunkOverlap}
                    onChange={(e) => setIndexingChunkOverlap(Number(e.target.value))}
                    placeholder="Chunk overlap"
                    className="rounded-lg border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-100"
                  />
                </div>
              </div>

              <div className="mt-4">
                <p className="mb-2 text-xs text-gray-500">Exclude patterns (one glob per line)</p>
                <textarea
                  value={indexingExclude}
                  onChange={(e) => setIndexingExclude(e.target.value)}
                  rows={6}
                  className="w-full rounded-lg border border-gray-700 bg-gray-950 px-3 py-2 font-mono text-xs text-gray-200"
                />
              </div>
            </Section>

            <Section
              title="Providers and Tokens"
              description="Configure runtime providers now, or leave fields empty and update them later from Settings."
            >
              <div className="grid gap-3 md:grid-cols-3">
                <TextInput value={llmProvider} onChange={setLlmProvider} placeholder="LLM provider" />
                <TextInput value={llmModel} onChange={setLlmModel} placeholder="LLM model" />
                <TextInput value={embeddingsProvider} onChange={setEmbeddingsProvider} placeholder="Embeddings provider" />
                <TextInput value={embeddingsModel} onChange={setEmbeddingsModel} placeholder="Embeddings model" />
                <input
                  type="number"
                  value={embeddingsDims}
                  onChange={(e) => setEmbeddingsDims(Number(e.target.value))}
                  placeholder="Embeddings dims"
                  className="rounded-lg border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-100"
                />
                <TextInput value={embeddingsBaseUrl} onChange={setEmbeddingsBaseUrl} placeholder="Embeddings base URL (optional)" />
                <TextInput value={openAiKey} onChange={setOpenAiKey} placeholder="OPENAI_API_KEY" />
                <TextInput value={anthropicKey} onChange={setAnthropicKey} placeholder="ANTHROPIC_API_KEY" />
                <TextInput value={deepseekKey} onChange={setDeepseekKey} placeholder="DEEPSEEK_API_KEY" />
                <TextInput value={embeddingsApiKey} onChange={setEmbeddingsApiKey} placeholder="EMBEDDINGS_API_KEY" />
                <TextInput value={githubToken} onChange={setGithubToken} placeholder="GITHUB_TOKEN" />
                <TextInput value={gitlabToken} onChange={setGitlabToken} placeholder="GITLAB_TOKEN" />
              </div>
            </Section>

            <Section
              title="Initial Repositories"
              description="Optional. Add repositories now, or manage them later from the Repositories page after login."
            >
              <div className="mb-3 flex items-center justify-between">
                <p className="text-sm text-gray-400">Repositories are stored in runtime config after setup.</p>
                <button
                  type="button"
                  onClick={addRepo}
                  className="inline-flex items-center gap-2 rounded-lg bg-cyan-600 px-3 py-2 text-sm font-medium text-white"
                >
                  <Plus className="h-4 w-4" />
                  Add repo
                </button>
              </div>

              <div className="space-y-3">
                {repos.map((repo, index) => (
                  <div key={index} className="grid gap-3 rounded-xl border border-gray-800 bg-gray-950/80 p-3 md:grid-cols-7">
                    <TextInput
                      value={repo.name}
                      onChange={(value) => setRepos((prev) => prev.map((entry, i) => (i === index ? { ...entry, name: value } : entry)))}
                      placeholder="name"
                    />
                    <select
                      value={repo.type}
                      onChange={(e) =>
                        setRepos((prev) => prev.map((entry, i) => (i === index ? { ...entry, type: e.target.value as RepoDraft['type'] } : entry)))
                      }
                      className="rounded-lg border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-100"
                    >
                      <option value="local">local</option>
                      <option value="github">github</option>
                      <option value="gitlab">gitlab</option>
                    </select>
                    <input
                      value={repo.type === 'local' ? repo.path : repo.url}
                      onChange={(e) =>
                        setRepos((prev) =>
                          prev.map((entry, i) =>
                            i === index
                              ? entry.type === 'local'
                                ? { ...entry, path: e.target.value }
                                : { ...entry, url: e.target.value }
                              : entry
                          )
                        )
                      }
                      placeholder={repo.type === 'local' ? '/repos/project' : 'https://provider/owner/repo'}
                      className="rounded-lg border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-100 md:col-span-2"
                    />
                    <TextInput
                      value={repo.branch}
                      onChange={(value) => setRepos((prev) => prev.map((entry, i) => (i === index ? { ...entry, branch: value } : entry)))}
                      placeholder="branch"
                    />
                    <TextInput
                      value={repo.language}
                      onChange={(value) => setRepos((prev) => prev.map((entry, i) => (i === index ? { ...entry, language: value } : entry)))}
                      placeholder="language"
                    />
                    <button
                      type="button"
                      onClick={() => setRepos((prev) => prev.filter((_, i) => i !== index))}
                      className="inline-flex items-center justify-center rounded-lg border border-rose-500/20 bg-rose-500/10 text-rose-300"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                ))}
                {repos.length === 0 && <p className="text-sm text-gray-500">No repositories queued for setup.</p>}
              </div>
            </Section>
          </>
        )}

        <div className="flex justify-end">
          <button
            onClick={handleSubmit}
            disabled={saving || !bootstrapToken || !adminUsername || !adminPassword}
            className="inline-flex items-center gap-2 rounded-xl bg-cyan-600 px-5 py-3 text-sm font-medium text-white disabled:opacity-50"
          >
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {mode === 'full' ? 'Complete setup' : 'Create admin account'}
          </button>
        </div>
      </div>
    </div>
  )
}
