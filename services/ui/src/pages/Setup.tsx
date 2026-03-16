import { useState } from 'react'
import { ShieldCheck, Loader2, Plus, Trash2 } from 'lucide-react'
import { api } from '../lib/api'

type SetupProps = { onCompleted: () => void }

type RepoDraft = {
  name: string
  type: 'local' | 'github' | 'gitlab'
  path: string
  url: string
  branch: string
  language: string
}

export default function Setup({ onCompleted }: SetupProps) {
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
    setRepos(prev => [
      ...prev,
      { name: '', type: 'local', path: '', url: '', branch: 'main', language: 'auto' },
    ])

  const handleSubmit = async () => {
    setSaving(true)
    setError(null)
    try {
      const forgeConfig = {
        repos: repos
          .filter(r => r.name.trim())
          .map(r => ({
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
          exclude: indexingExclude
            .split('\n')
            .map(s => s.trim())
            .filter(Boolean),
          max_file_size_kb: indexingMaxSize,
          chunk_size: indexingChunkSize,
          chunk_overlap: indexingChunkOverlap,
        },
      }
      const settingsOverrides = {
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
      }
      await api.setup.init({
        bootstrap_token: bootstrapToken.trim(),
        admin_username: adminUsername.trim(),
        admin_password: adminPassword,
        forge_config: forgeConfig,
        settings_overrides: settingsOverrides,
      })
      onCompleted()
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-8">
      <div className="max-w-4xl mx-auto bg-gray-900 border border-gray-800 rounded-2xl p-6 space-y-5">
        <div>
          <h1 className="text-xl font-semibold text-white inline-flex items-center gap-2">
            <ShieldCheck className="w-5 h-5 text-indigo-400" />
            First-time Setup
          </h1>
          <p className="text-sm text-gray-500 mt-1">Configure admin access, providers, tokens and repositories with guided fields.</p>
        </div>

        {error && <div className="p-3 text-sm text-red-300 bg-red-500/10 border border-red-500/30 rounded-lg">{error}</div>}

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <input
            value={bootstrapToken}
            onChange={e => setBootstrapToken(e.target.value)}
            placeholder="SETUP_BOOTSTRAP_TOKEN"
            className="px-3 py-2 text-sm bg-gray-950 border border-gray-700 rounded-lg"
          />
          <input
            value={adminUsername}
            onChange={e => setAdminUsername(e.target.value)}
            placeholder="Admin username"
            className="px-3 py-2 text-sm bg-gray-950 border border-gray-700 rounded-lg"
          />
          <input
            type="password"
            value={adminPassword}
            onChange={e => setAdminPassword(e.target.value)}
            placeholder="Admin password"
            className="px-3 py-2 text-sm bg-gray-950 border border-gray-700 rounded-lg"
          />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <input value={memoryUserId} onChange={e => setMemoryUserId(e.target.value)} placeholder="Memory user id" className="px-3 py-2 text-sm bg-gray-950 border border-gray-700 rounded-lg" />
          <input value={indexingSchedule} onChange={e => setIndexingSchedule(e.target.value)} placeholder="Indexing schedule (cron)" className="px-3 py-2 text-sm bg-gray-950 border border-gray-700 rounded-lg" />
          <label className="inline-flex items-center gap-2 text-sm text-gray-300">
            <input type="checkbox" checked={indexingAuto} onChange={e => setIndexingAuto(e.target.checked)} />
            Auto indexing
          </label>
          <input type="number" value={indexingMaxSize} onChange={e => setIndexingMaxSize(Number(e.target.value))} placeholder="Max file size KB" className="px-3 py-2 text-sm bg-gray-950 border border-gray-700 rounded-lg" />
          <input type="number" value={indexingChunkSize} onChange={e => setIndexingChunkSize(Number(e.target.value))} placeholder="Chunk size" className="px-3 py-2 text-sm bg-gray-950 border border-gray-700 rounded-lg" />
          <input type="number" value={indexingChunkOverlap} onChange={e => setIndexingChunkOverlap(Number(e.target.value))} placeholder="Chunk overlap" className="px-3 py-2 text-sm bg-gray-950 border border-gray-700 rounded-lg" />
        </div>

        <div>
          <p className="text-xs text-gray-500 mb-1">Exclude patterns (one per line)</p>
          <textarea value={indexingExclude} onChange={e => setIndexingExclude(e.target.value)} rows={5} className="w-full px-3 py-2 text-xs font-mono bg-gray-950 border border-gray-700 rounded-lg" />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <input value={llmProvider} onChange={e => setLlmProvider(e.target.value)} placeholder="LLM provider" className="px-3 py-2 text-sm bg-gray-950 border border-gray-700 rounded-lg" />
          <input value={llmModel} onChange={e => setLlmModel(e.target.value)} placeholder="LLM model" className="px-3 py-2 text-sm bg-gray-950 border border-gray-700 rounded-lg" />
          <input value={embeddingsProvider} onChange={e => setEmbeddingsProvider(e.target.value)} placeholder="Embeddings provider" className="px-3 py-2 text-sm bg-gray-950 border border-gray-700 rounded-lg" />
          <input value={embeddingsModel} onChange={e => setEmbeddingsModel(e.target.value)} placeholder="Embeddings model" className="px-3 py-2 text-sm bg-gray-950 border border-gray-700 rounded-lg" />
          <input type="number" value={embeddingsDims} onChange={e => setEmbeddingsDims(Number(e.target.value))} placeholder="Embeddings dims" className="px-3 py-2 text-sm bg-gray-950 border border-gray-700 rounded-lg" />
          <input value={embeddingsBaseUrl} onChange={e => setEmbeddingsBaseUrl(e.target.value)} placeholder="Embeddings base URL (optional)" className="px-3 py-2 text-sm bg-gray-950 border border-gray-700 rounded-lg" />
          <input value={openAiKey} onChange={e => setOpenAiKey(e.target.value)} placeholder="OPENAI_API_KEY" className="px-3 py-2 text-sm bg-gray-950 border border-gray-700 rounded-lg" />
          <input value={anthropicKey} onChange={e => setAnthropicKey(e.target.value)} placeholder="ANTHROPIC_API_KEY" className="px-3 py-2 text-sm bg-gray-950 border border-gray-700 rounded-lg" />
          <input value={deepseekKey} onChange={e => setDeepseekKey(e.target.value)} placeholder="DEEPSEEK_API_KEY" className="px-3 py-2 text-sm bg-gray-950 border border-gray-700 rounded-lg" />
          <input value={embeddingsApiKey} onChange={e => setEmbeddingsApiKey(e.target.value)} placeholder="EMBEDDINGS_API_KEY" className="px-3 py-2 text-sm bg-gray-950 border border-gray-700 rounded-lg" />
          <input value={githubToken} onChange={e => setGithubToken(e.target.value)} placeholder="GITHUB_TOKEN" className="px-3 py-2 text-sm bg-gray-950 border border-gray-700 rounded-lg" />
          <input value={gitlabToken} onChange={e => setGitlabToken(e.target.value)} placeholder="GITLAB_TOKEN" className="px-3 py-2 text-sm bg-gray-950 border border-gray-700 rounded-lg" />
        </div>

        <div>
          <div className="flex items-center justify-between mb-2">
            <p className="text-sm text-gray-300">Repositories</p>
            <button onClick={addRepo} type="button" className="inline-flex items-center gap-1 text-xs text-indigo-300 hover:text-indigo-200">
              <Plus className="w-3.5 h-3.5" />
              Add repo
            </button>
          </div>
          <div className="space-y-2">
            {repos.map((repo, index) => (
              <div key={index} className="grid grid-cols-1 md:grid-cols-7 gap-2 p-2 border border-gray-800 rounded-lg">
                <input value={repo.name} onChange={e => setRepos(prev => prev.map((r, i) => (i === index ? { ...r, name: e.target.value } : r)))} placeholder="name" className="px-2 py-1.5 text-xs bg-gray-950 border border-gray-700 rounded" />
                <select value={repo.type} onChange={e => setRepos(prev => prev.map((r, i) => (i === index ? { ...r, type: e.target.value as RepoDraft['type'] } : r)))} className="px-2 py-1.5 text-xs bg-gray-950 border border-gray-700 rounded">
                  <option value="local">local</option>
                  <option value="github">github</option>
                  <option value="gitlab">gitlab</option>
                </select>
                <input value={repo.type === 'local' ? repo.path : repo.url} onChange={e => setRepos(prev => prev.map((r, i) => (i === index ? (r.type === 'local' ? { ...r, path: e.target.value } : { ...r, url: e.target.value }) : r)))} placeholder={repo.type === 'local' ? 'path' : 'url'} className="px-2 py-1.5 text-xs bg-gray-950 border border-gray-700 rounded md:col-span-2" />
                <input value={repo.branch} onChange={e => setRepos(prev => prev.map((r, i) => (i === index ? { ...r, branch: e.target.value } : r)))} placeholder="branch" className="px-2 py-1.5 text-xs bg-gray-950 border border-gray-700 rounded" />
                <input value={repo.language} onChange={e => setRepos(prev => prev.map((r, i) => (i === index ? { ...r, language: e.target.value } : r)))} placeholder="language" className="px-2 py-1.5 text-xs bg-gray-950 border border-gray-700 rounded" />
                <button onClick={() => setRepos(prev => prev.filter((_, i) => i !== index))} type="button" className="inline-flex items-center justify-center text-red-300 hover:text-red-200">
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            ))}
            {repos.length === 0 && <p className="text-xs text-gray-500">No repositories added yet.</p>}
          </div>
        </div>

        <button
          onClick={handleSubmit}
          disabled={saving || !bootstrapToken || !adminUsername || !adminPassword}
          className="px-4 py-2 text-sm text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg disabled:opacity-50 inline-flex items-center gap-2"
        >
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
          Complete setup
        </button>
      </div>
    </div>
  )
}

