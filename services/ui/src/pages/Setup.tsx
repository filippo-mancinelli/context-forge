import { useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { api } from '../lib/api'
import { Button, Input, Textarea } from '../components/ui'

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

function Section({ title, description, children }: { title: string; description: string; children: React.ReactNode }) {
  return (
    <section style={{ border: '1px solid var(--border)' }} className="p-6">
      <h2 className="text-base font-semibold mb-1">{title}</h2>
      <p className="text-sm text-muted mb-4">{description}</p>
      {children}
    </section>
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
    setRepos(prev => [...prev, { name: '', type: 'local', path: '', url: '', branch: 'main', language: 'auto' }])

  const updateRepo = (index: number, field: keyof RepoDraft, value: string) =>
    setRepos(prev => prev.map((r, i) => i === index ? { ...r, [field]: value } : r))

  const removeRepo = (index: number) =>
    setRepos(prev => prev.filter((_, i) => i !== index))

  const handleSubmit = async () => {
    setSaving(true)
    setError(null)
    try {
      const payload = mode === 'full' ? {
        forge_config: {
          repos: repos.filter(r => r.name.trim()).map(r => ({
            name: r.name.trim(),
            type: r.type,
            ...(r.type === 'local' ? { path: r.path.trim() } : { url: r.url.trim() }),
            branch: r.branch.trim() || 'main',
            language: r.language.trim() || 'auto',
          })),
          memory: { user_id: memoryUserId.trim() || 'default' },
          indexing: {
            auto: indexingAuto,
            schedule: indexingSchedule.trim() || '0 */6 * * *',
            exclude: indexingExclude.split('\n').map(s => s.trim()).filter(Boolean),
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
      } : {}

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
    <div
      style={{ minHeight: '100vh', background: 'var(--surface)' }}
      className="px-4 sm:px-6 py-8 sm:py-10"
    >
      <div className="mx-auto" style={{ maxWidth: '860px' }}>
        <div className="mb-8">
          <h1 className="text-2xl font-semibold">
            {mode === 'full' ? 'Setup context-forge' : 'Create admin account'}
          </h1>
          <p className="text-muted text-sm mt-1">
            {mode === 'full'
              ? 'Bootstrap once, then manage everything from the web UI.'
              : 'Bootstrap configuration is detected. Create the admin account to proceed.'}
          </p>
        </div>

        {error && (
          <div style={{ border: '1px solid var(--danger)', color: 'var(--danger)' }} className="text-sm p-3 mb-6 bg-[#fef2f2]">
            {error}
          </div>
        )}

        <div className="space-y-6">
          <Section
            title="Admin access"
            description="The bootstrap token authorizes this setup. The admin account is used for the web UI login."
          >
            <div className="grid gap-3 md:grid-cols-3">
              <Input value={bootstrapToken} onChange={e => setBootstrapToken(e.target.value)} placeholder="Bootstrap token" type="password" />
              <Input value={adminUsername} onChange={e => setAdminUsername(e.target.value)} placeholder="Admin username" />
              <Input value={adminPassword} onChange={e => setAdminPassword(e.target.value)} placeholder="Admin password" type="password" />
            </div>
          </Section>

          {mode === 'full' && (
            <>
              <Section
                title="Runtime defaults"
                description="Initial values for the runtime config. All fields can be changed from Settings after login."
              >
                <div className="grid gap-3 md:grid-cols-2 mb-4">
                  <Input label="Memory user ID" value={memoryUserId} onChange={e => setMemoryUserId(e.target.value)} placeholder="default" />
                  <Input label="Indexing schedule (cron)" value={indexingSchedule} onChange={e => setIndexingSchedule(e.target.value)} placeholder="0 */6 * * *" />
                  <div className="flex items-center gap-2 md:col-span-2">
                    <input type="checkbox" id="auto" checked={indexingAuto} onChange={e => setIndexingAuto(e.target.checked)} className="w-3.5 h-3.5" />
                    <label htmlFor="auto" className="text-sm cursor-pointer">Enable automatic re-indexing</label>
                  </div>
                  <Input label="Max file size (KB)" type="number" value={String(indexingMaxSize)} onChange={e => setIndexingMaxSize(Number(e.target.value))} />
                  <div className="grid grid-cols-2 gap-3">
                    <Input label="Chunk size" type="number" value={String(indexingChunkSize)} onChange={e => setIndexingChunkSize(Number(e.target.value))} />
                    <Input label="Chunk overlap" type="number" value={String(indexingChunkOverlap)} onChange={e => setIndexingChunkOverlap(Number(e.target.value))} />
                  </div>
                </div>
                <Textarea
                  label="Exclude patterns (one glob per line)"
                  value={indexingExclude}
                  onChange={e => setIndexingExclude(e.target.value)}
                  rows={5}
                  className="font-mono text-xs"
                />
              </Section>

              <Section
                title="Providers and tokens"
                description="Optional — leave empty and configure from Settings later."
              >
                <div className="grid gap-3 md:grid-cols-3">
                  <Input label="LLM provider" value={llmProvider} onChange={e => setLlmProvider(e.target.value)} placeholder="openai" />
                  <Input label="LLM model" value={llmModel} onChange={e => setLlmModel(e.target.value)} placeholder="gpt-4o-mini" />
                  <Input label="Embeddings provider" value={embeddingsProvider} onChange={e => setEmbeddingsProvider(e.target.value)} placeholder="openai" />
                  <Input label="Embeddings model" value={embeddingsModel} onChange={e => setEmbeddingsModel(e.target.value)} placeholder="text-embedding-3-small" />
                  <Input label="Embeddings dims" type="number" value={String(embeddingsDims)} onChange={e => setEmbeddingsDims(Number(e.target.value))} />
                  <Input label="Embeddings base URL" value={embeddingsBaseUrl} onChange={e => setEmbeddingsBaseUrl(e.target.value)} placeholder="https://api.openai.com/v1" />
                  <Input label="OpenAI API key" value={openAiKey} onChange={e => setOpenAiKey(e.target.value)} placeholder="sk-..." type="password" />
                  <Input label="Anthropic API key" value={anthropicKey} onChange={e => setAnthropicKey(e.target.value)} placeholder="sk-ant-..." type="password" />
                  <Input label="DeepSeek API key" value={deepseekKey} onChange={e => setDeepseekKey(e.target.value)} placeholder="..." type="password" />
                  <Input label="Embeddings API key" value={embeddingsApiKey} onChange={e => setEmbeddingsApiKey(e.target.value)} placeholder="Leave empty to reuse" type="password" />
                  <Input label="GitHub token" value={githubToken} onChange={e => setGithubToken(e.target.value)} placeholder="ghp_..." type="password" />
                  <Input label="GitLab token" value={gitlabToken} onChange={e => setGitlabToken(e.target.value)} placeholder="glpat-..." type="password" />
                </div>
              </Section>

              <Section
                title="Initial repositories"
                description="Optional. Add repositories now or manage them from the Repositories page after login."
              >
                <div className="flex justify-end mb-3">
                  <Button variant="secondary" size="sm" onClick={addRepo}>
                    <Plus className="w-3.5 h-3.5" />
                    Add repository
                  </Button>
                </div>

                {repos.length === 0 ? (
                  <p className="text-muted text-sm">No repositories queued for setup.</p>
                ) : (
                  <div className="space-y-2">
                    {repos.map((repo, i) => (
                      <div
                        key={i}
                        style={{ border: '1px solid var(--border)' }}
                        className="grid gap-2 p-3 md:grid-cols-7"
                      >
                        <Input value={repo.name} onChange={e => updateRepo(i, 'name', e.target.value)} placeholder="name" />
                        <select
                          value={repo.type}
                          onChange={e => updateRepo(i, 'type', e.target.value)}
                          className="px-3 py-1.5 text-sm bg-bg text-text border border-border rounded focus:outline-none focus:border-accent"
                        >
                          <option value="local">local</option>
                          <option value="github">github</option>
                          <option value="gitlab">gitlab</option>
                        </select>
                        <div className="md:col-span-2">
                          <Input
                            value={repo.type === 'local' ? repo.path : repo.url}
                            onChange={e => updateRepo(i, repo.type === 'local' ? 'path' : 'url', e.target.value)}
                            placeholder={repo.type === 'local' ? '/repos/project' : 'https://provider/owner/repo'}
                          />
                        </div>
                        <Input value={repo.branch} onChange={e => updateRepo(i, 'branch', e.target.value)} placeholder="main" />
                        <Input value={repo.language} onChange={e => updateRepo(i, 'language', e.target.value)} placeholder="auto" />
                        <Button variant="danger" size="sm" onClick={() => removeRepo(i)} className="justify-center">
                          <Trash2 className="w-3.5 h-3.5" />
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
              </Section>
            </>
          )}

          <div className="flex justify-end">
            <Button
              variant="primary"
              size="lg"
              onClick={handleSubmit}
              loading={saving}
              disabled={saving || !bootstrapToken || !adminUsername || !adminPassword}
            >
              {mode === 'full' ? 'Complete setup' : 'Create admin account'}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
