import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertCircle, Check, Clipboard, Plus, Save, Trash2, X } from 'lucide-react'
import { api, type Repo, type MCPApiKey } from '../lib/api'
import { Button, Input, Textarea, Select, Tabs, TabsList, TabsTrigger, TabsContent, Badge, Dialog, DialogFooter, useConfirm, useToast } from '../components/ui'

type Tab = 'access' | 'models' | 'runtime' | 'mcp_keys' | 'channels'

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
    telegram_bot_token?: string
    telegram_webhook_secret?: string
    telegram_allowed_chat_ids?: string
    telegram_org_id?: number
  }
  // Global model/provider settings are shared and only editable by org admins.
  settings_overrides_editable?: boolean
}

function GlobalSettingsNotice() {
  return (
    <div
      style={{ border: '1px solid var(--border)' }}
      className="text-xs text-muted p-3 mb-4 bg-surface"
    >
      These are <span className="font-medium text-text">global</span> settings shared by every
      organization (they are tied to the shared vector store). Only organization admins can change
      them.
    </div>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <label className="text-xs font-medium text-muted uppercase tracking-wide block">{label}</label>
      {children}
      {hint && <p className="text-xs text-muted">{hint}</p>}
    </div>
  )
}

function SecretField({
  label,
  value,
  onChange,
  placeholder,
  hint,
}: {
  label: string
  value?: string
  onChange: (v: string) => void
  placeholder: string
  hint?: string
}) {
  const [visible, setVisible] = useState(false)
  return (
    <Field label={label} hint={hint}>
      <div className="relative">
        <Input
          type={visible ? 'text' : 'password'}
          value={value || ''}
          onChange={e => onChange(e.target.value)}
          placeholder={placeholder}
        />
        <button
          type="button"
          onClick={() => setVisible(v => !v)}
          className="absolute right-2.5 top-1/2 -translate-y-1/2 text-xs text-muted hover:text-text"
        >
          {visible ? 'Hide' : 'Show'}
        </button>
      </div>
    </Field>
  )
}

function AccessTab({
  settings,
  onChange,
  editable = true,
}: {
  settings: SettingsData['settings_overrides']
  onChange: (key: keyof SettingsData['settings_overrides'], value: string) => void
  editable?: boolean
}) {
  return (
    <fieldset disabled={!editable} className="contents">
    {!editable && <GlobalSettingsNotice />}
    <div className="grid gap-6 lg:grid-cols-2">
      <section>
        <h3 className="text-sm font-semibold mb-3">LLM provider keys</h3>
        <div className="space-y-3">
          <SecretField label="OpenAI API key" value={settings.openai_api_key} onChange={v => onChange('openai_api_key', v)} placeholder="sk-..." />
          <SecretField label="Anthropic API key" value={settings.anthropic_api_key} onChange={v => onChange('anthropic_api_key', v)} placeholder="sk-ant-..." />
          <SecretField label="DeepSeek API key" value={settings.deepseek_api_key} onChange={v => onChange('deepseek_api_key', v)} placeholder="..." />
        </div>
      </section>
      <section>
        <h3 className="text-sm font-semibold mb-3">Git provider tokens</h3>
        <div className="space-y-3">
          <SecretField label="GitHub token" value={settings.github_token} onChange={v => onChange('github_token', v)} placeholder="ghp_..." hint="Needed for private repos or high-volume API use." />
          <SecretField label="GitLab token" value={settings.gitlab_token} onChange={v => onChange('gitlab_token', v)} placeholder="glpat-..." />
        </div>
      </section>
    </div>
    </fieldset>
  )
}

function ChannelsTab({
  settings,
  onChange,
  editable = true,
  onSaveSettings,
}: {
  settings: SettingsData['settings_overrides']
  onChange: (key: keyof SettingsData['settings_overrides'], value: string | number) => void
  editable?: boolean
  onSaveSettings: () => Promise<void>
}) {
  const toast = useToast()
  const [webhookUrl, setWebhookUrl] = useState('')
  const [registering, setRegistering] = useState(false)

  // Auto-fill the webhook URL from Telegram on mount.
  useEffect(() => {
    api.telegram.getWebhookInfo().then(info => {
      if (info.url) setWebhookUrl(info.url)
    }).catch(() => { /* bot token not configured yet, ignore */ })
  }, [])

  const handleRegister = async () => {
    if (!webhookUrl.trim()) {
      toast.error('Enter the public webhook URL first')
      return
    }
    setRegistering(true)
    try {
      // Persist bot token/secret before asking Telegram to call back that URL.
      await onSaveSettings()
      await api.telegram.registerWebhook(webhookUrl.trim())
      toast.success('Telegram webhook registered')
    } catch (e) {
      toast.error(String(e))
    } finally {
      setRegistering(false)
    }
  }

  return (
    <fieldset disabled={!editable} className="contents">
    {!editable && <GlobalSettingsNotice />}
    <div className="grid gap-6 lg:grid-cols-2">
      <section>
        <h3 className="text-sm font-semibold mb-3">Telegram quick-capture</h3>
        <div className="space-y-3">
          <SecretField
            label="Bot token"
            value={settings.telegram_bot_token}
            onChange={v => onChange('telegram_bot_token', v)}
            placeholder="123456789:AA..."
            hint="From @BotFather on Telegram."
          />
          <SecretField
            label="Webhook secret"
            value={settings.telegram_webhook_secret}
            onChange={v => onChange('telegram_webhook_secret', v)}
            placeholder="any random string"
            hint="Verifies incoming requests really come from Telegram."
          />
          <Field label="Allowed chat IDs" hint="Comma-separated. Only these chats can feed the capture pipeline.">
            <Input
              value={settings.telegram_allowed_chat_ids || ''}
              onChange={e => onChange('telegram_allowed_chat_ids', e.target.value)}
              placeholder="123456789, 987654321"
            />
          </Field>
          <Field label="Org ID" hint="The organization that captured messages are saved under.">
            <Input
              type="number"
              value={settings.telegram_org_id ?? ''}
              onChange={e => onChange('telegram_org_id', e.target.value === '' ? 0 : Number(e.target.value))}
              placeholder="1"
            />
          </Field>
        </div>
      </section>
      <section>
        <h3 className="text-sm font-semibold mb-3">Register webhook</h3>
        <div className="space-y-3">
          <Field label="Public URL" hint="e.g. https://your-domain.com/api/telegram/webhook — saves the fields above first.">
            <Input
              value={webhookUrl}
              onChange={e => setWebhookUrl(e.target.value)}
              placeholder="https://your-domain.com/api/telegram/webhook"
            />
          </Field>
          <Button variant="secondary" onClick={handleRegister} loading={registering} disabled={registering}>
            Register webhook
          </Button>
        </div>
      </section>
    </div>
    </fieldset>
  )
}

function ModelsTab({
  settings,
  onChange,
  embeddingRisk,
  editable = true,
}: {
  settings: SettingsData['settings_overrides']
  onChange: (key: keyof SettingsData['settings_overrides'], value: string | number) => void
  embeddingRisk: { changed: boolean; dimsChanged: boolean }
  editable?: boolean
}) {
  return (
    <fieldset disabled={!editable} className="contents">
    <div className="space-y-6">
      {!editable && <GlobalSettingsNotice />}
      {embeddingRisk.changed && (
        <div style={{ border: '1px solid var(--warning)', color: 'var(--warning)' }} className="text-sm p-3 bg-[#fef9e7]">
          <p className="font-medium mb-1">Embedding changes require re-indexing</p>
          <p className="text-xs">
            Changing the embeddings provider or model will invalidate existing vectors. Re-index all repositories after saving.
            {embeddingRisk.dimsChanged && ' Changing dimensions also requires resetting vector-backed data.'}
          </p>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <section>
          <h3 className="text-sm font-semibold mb-3">Memory LLM</h3>
          <div className="space-y-3">
            <Select
              label="LLM provider"
              value={settings.llm_provider || 'openai'}
              onValueChange={v => onChange('llm_provider', v)}
              options={[
                { value: 'openai', label: 'OpenAI' },
                { value: 'anthropic', label: 'Anthropic' },
                { value: 'deepseek', label: 'DeepSeek' },
              ]}
            />
            <Field label="LLM model">
              <Input value={settings.llm_model || ''} onChange={e => onChange('llm_model', e.target.value)} placeholder="gpt-4o-mini" />
            </Field>
          </div>
        </section>

        <section>
          <h3 className="text-sm font-semibold mb-3">Embeddings</h3>
          <div className="space-y-3">
            <Select
              label="Provider"
              value={settings.embeddings_provider || 'openai'}
              onValueChange={v => onChange('embeddings_provider', v)}
              options={[
                { value: 'openai', label: 'OpenAI' },
                { value: 'jina', label: 'Jina' },
                { value: 'openai-compatible', label: 'OpenAI compatible' },
                { value: 'local', label: 'Local' },
              ]}
            />
            <Field label="Model">
              <Input value={settings.embeddings_model || ''} onChange={e => onChange('embeddings_model', e.target.value)} placeholder="text-embedding-3-small" />
            </Field>
            <Field label="Dimensions">
              <Input
                type="number"
                value={String(settings.embeddings_dims || 1536)}
                onChange={e => onChange('embeddings_dims', parseInt(e.target.value, 10) || 1536)}
              />
            </Field>
          </div>
        </section>

        <section>
          <h3 className="text-sm font-semibold mb-3">Embedder connection</h3>
          <div className="space-y-3">
            <SecretField
              label="Dedicated embeddings key"
              value={settings.embeddings_api_key}
              onChange={v => onChange('embeddings_api_key', v)}
              placeholder="Leave empty to reuse provider key"
            />
            <Field label="OpenAI-compatible base URL">
              <Input value={settings.embeddings_base_url || ''} onChange={e => onChange('embeddings_base_url', e.target.value)} placeholder="https://api.openai.com/v1" />
            </Field>
          </div>
        </section>
      </div>
    </div>
    </fieldset>
  )
}

function RuntimeTab({
  config,
  onChange,
}: {
  config: SettingsData['forge_config']
  onChange: (path: string, value: unknown) => void
}) {
  const indexing = config.indexing || {}
  const memory = config.memory || { user_id: 'default' }

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <section>
        <h3 className="text-sm font-semibold mb-3">Indexing</h3>
        <div className="space-y-3">
          <Field label="Auto-index">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={!!indexing.auto}
                onChange={e => onChange('indexing.auto', e.target.checked)}
                className="w-3.5 h-3.5"
              />
              <span className="text-sm">Enable automatic re-indexing</span>
            </label>
          </Field>
          <Field label="Cron schedule" hint="e.g. 0 */6 * * * (every 6 hours)">
            <Input value={indexing.schedule || ''} onChange={e => onChange('indexing.schedule', e.target.value)} placeholder="0 */6 * * *" />
          </Field>
          <Field label="Max file size (KB)">
            <Input type="number" value={String(indexing.max_file_size_kb || 500)} onChange={e => onChange('indexing.max_file_size_kb', parseInt(e.target.value, 10) || 500)} />
          </Field>
          <Field label="Chunk size (tokens)">
            <Input type="number" value={String(indexing.chunk_size || 400)} onChange={e => onChange('indexing.chunk_size', parseInt(e.target.value, 10) || 400)} />
          </Field>
          <Field label="Chunk overlap (tokens)">
            <Input type="number" value={String(indexing.chunk_overlap || 50)} onChange={e => onChange('indexing.chunk_overlap', parseInt(e.target.value, 10) || 50)} />
          </Field>
        </div>
      </section>

      <section>
        <h3 className="text-sm font-semibold mb-3">Memory</h3>
        <div className="space-y-3">
          <Field label="User ID" hint="All memories are stored under this user ID.">
            <Input value={memory.user_id || 'default'} onChange={e => onChange('memory.user_id', e.target.value)} placeholder="default" />
          </Field>
        </div>

        <h3 className="text-sm font-semibold mb-3 mt-6">Exclude patterns</h3>
        <Field label="Patterns (one per line)" hint="Glob patterns for files to skip during indexing.">
          <Textarea
            value={(indexing.exclude || []).join('\n')}
            onChange={e => onChange('indexing.exclude', e.target.value.split('\n').map(s => s.trim()).filter(Boolean))}
            rows={8}
            className="font-mono text-xs"
            placeholder="**/node_modules/**&#10;**/__pycache__/**"
          />
        </Field>
      </section>
    </div>
  )
}

function CopyButton({ text, className }: { text: string; className?: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <button
      type="button"
      onClick={handleCopy}
      className={`inline-flex items-center justify-center p-1.5 rounded hover:bg-black/5 transition-colors ${className ?? ''}`}
      title={copied ? 'Copied!' : 'Copy to clipboard'}
    >
      {copied
        ? <Check className="w-3.5 h-3.5 text-success" />
        : <Clipboard className="w-3.5 h-3.5 text-muted hover:text-text" />}
    </button>
  )
}

function NewKeyBanner({ apiKey, onDismiss }: { apiKey: string; onDismiss: () => void }) {
  return (
    <div style={{ border: '1px solid var(--success)', color: 'var(--success)' }} className="text-sm p-4 mb-4 bg-[#eafaf1]">
      <div className="flex items-start justify-between mb-2">
        <p className="font-medium">
          <Check className="inline w-3.5 h-3.5 mr-1" />
          Key generated — copy it now, it won't be shown again.
        </p>
        <button type="button" onClick={onDismiss} className="p-1 hover:bg-black/5 rounded" title="Dismiss">
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
      <div className="flex items-center gap-1 bg-white border border-[#a9dfbf] p-2 rounded">
        <code className="text-xs font-mono flex-1 overflow-x-auto select-all">{apiKey}</code>
        <CopyButton text={apiKey} />
      </div>
    </div>
  )
}

function McpKeysTab() {
  const [keys, setKeys] = useState<MCPApiKey[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [creating, setCreating] = useState(false)
  const confirm = useConfirm()
  const toast = useToast()
  const [newKey, setNewKey] = useState<{ key: string; name: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [keyName, setKeyName] = useState('')
  const [keyScope, setKeyScope] = useState('read,write')
  const [expiresDays, setExpiresDays] = useState('')

  const loadKeys = useCallback(async () => {
    try {
      const response = await api.mcpKeys.list()
      setKeys(response.keys)
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadKeys() }, [loadKeys])

  const handleCreate = async () => {
    setCreating(true)
    setError(null)
    try {
      const response = await api.mcpKeys.create({
        name: keyName,
        scope: keyScope,
        expires_days: expiresDays ? parseInt(expiresDays, 10) : undefined,
      })
      setNewKey({ key: response.key, name: response.name })
      toast.success(`API key "${response.name}" generated`)
      await loadKeys()
      setShowCreate(false)
      setKeyName('')
      setExpiresDays('')
    } catch (e) {
      toast.error(String(e))
    } finally {
      setCreating(false)
    }
  }

  const handleRevoke = async (id: number) => {
    const ok = await confirm({
      title: 'Revoke API key',
      message: 'Revoke this API key? It will stop working immediately.',
      confirmLabel: 'Revoke',
      danger: true,
      onConfirm: () => api.mcpKeys.revoke(id),
    })
    if (!ok) return
    toast.success('API key revoked')
    await loadKeys()
  }

  const isExpired = (expiresAt?: string) => expiresAt ? new Date(expiresAt) < new Date() : false
  const fmtDate = (d?: string) => d ? new Date(d).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'

  return (
    <div>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between mb-4">
        <p className="text-sm text-muted">API keys allow CLI agents to authenticate with this instance.</p>
        <Button variant="primary" size="sm" onClick={() => setShowCreate(true)} className="self-start">
          <Plus className="w-3.5 h-3.5" />
          Generate key
        </Button>
      </div>

      {error && (
        <div style={{ border: '1px solid var(--danger)', color: 'var(--danger)' }} className="text-sm p-3 mb-4 bg-[#fef2f2]">
          <AlertCircle className="inline w-3.5 h-3.5 mr-1" />{error}
        </div>
      )}

      {newKey && (
        <NewKeyBanner apiKey={newKey.key} onDismiss={() => setNewKey(null)} />
      )}

      {loading ? (
        <p className="text-muted text-sm">Loading...</p>
      ) : keys.length === 0 ? (
        <p className="text-muted text-sm">No API keys configured yet.</p>
      ) : (
        <div style={{ border: '1px solid var(--border)' }} className="overflow-x-auto">
          <table className="w-full min-w-[600px]">
            <thead>
              <tr style={{ borderBottom: '2px solid var(--border)' }}>
                <th className="text-left text-xs font-semibold uppercase tracking-wide text-muted px-4 py-2">Name</th>
                <th className="text-left text-xs font-semibold uppercase tracking-wide text-muted px-3 py-2">Scope</th>
                <th className="text-left text-xs font-semibold uppercase tracking-wide text-muted px-3 py-2">Created</th>
                <th className="text-left text-xs font-semibold uppercase tracking-wide text-muted px-3 py-2">Last used</th>
                <th className="text-left text-xs font-semibold uppercase tracking-wide text-muted px-3 py-2">Expires</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {keys.map(key => (
                <tr key={key.id} style={{ borderBottom: '1px solid var(--border)' }} className="last:border-b-0 hover:bg-surface">
                  <td className="px-4 py-3 text-sm font-medium">
                    {key.name}
                    {isExpired(key.expires_at) && <Badge variant="danger" className="ml-2">Expired</Badge>}
                  </td>
                  <td className="px-3 py-3">
                    <code className="text-xs font-mono text-muted">{key.scope}</code>
                  </td>
                  <td className="px-3 py-3 text-xs text-muted">{fmtDate(key.created_at)}</td>
                  <td className="px-3 py-3 text-xs text-muted">{fmtDate(key.last_used_at)}</td>
                  <td className="px-3 py-3 text-xs text-muted">
                    {key.expires_at ? <span className={isExpired(key.expires_at) ? 'text-danger' : ''}>{fmtDate(key.expires_at)}</span> : 'Never'}
                  </td>
                  <td className="px-3 py-3">
                    <Button size="sm" variant="danger" onClick={() => handleRevoke(key.id)}>
                      <Trash2 className="w-3 h-3" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Dialog
        open={showCreate}
        onOpenChange={o => { if (!o) setShowCreate(false) }}
        title="Generate MCP API Key"
      >
        <div className="space-y-3">
          <Input
            label="Key name"
            value={keyName}
            onChange={e => setKeyName(e.target.value)}
            placeholder="My CLI agent"
          />
          <Select
            label="Scope"
            value={keyScope}
            onValueChange={setKeyScope}
            options={[
              { value: 'read', label: 'Read only' },
              { value: 'write', label: 'Write only' },
              { value: 'read,write', label: 'Read + Write (recommended)' },
              { value: 'admin', label: 'Full admin access' },
            ]}
          />
          <Input
            label="Expires in days (optional)"
            type="number"
            value={expiresDays}
            onChange={e => setExpiresDays(e.target.value)}
            placeholder="Leave empty for no expiration"
          />
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setShowCreate(false)}>Cancel</Button>
          <Button variant="primary" onClick={handleCreate} disabled={!keyName || creating} loading={creating}>
            Generate
          </Button>
        </DialogFooter>
      </Dialog>
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
  const [activeTab, setActiveTab] = useState<Tab>('access')
  const [data, setData] = useState<SettingsData | null>(null)
  const [baselineEmbedding, setBaselineEmbedding] = useState<ReturnType<typeof getEmbeddingSignature> | null>(null)
  const [baselineOverrides, setBaselineOverrides] = useState<SettingsData['settings_overrides'] | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [warnings, setWarnings] = useState<string[]>([])
  const confirm = useConfirm()
  const toast = useToast()

  const load = useCallback(async () => {
    try {
      const next = (await api.settings.get()) as SettingsData
      setData(next)
      setBaselineEmbedding(getEmbeddingSignature(next.settings_overrides))
      setBaselineOverrides(next.settings_overrides)
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const embeddingRisk = useMemo(() => {
    if (!data || !baselineEmbedding) return { changed: false, dimsChanged: false }
    const current = getEmbeddingSignature(data.settings_overrides)
    const dimsChanged = current.dims !== baselineEmbedding.dims
    const changed = dimsChanged || current.provider !== baselineEmbedding.provider || current.model !== baselineEmbedding.model
    return { changed, dimsChanged }
  }, [baselineEmbedding, data])

  const updateOverride = (key: keyof SettingsData['settings_overrides'], value: unknown) => {
    if (!data) return
    setData({ ...data, settings_overrides: { ...data.settings_overrides, [key]: value as string | number | undefined } })
  }

  const updateForgeConfig = (path: string, value: unknown) => {
    if (!data) return
    const segments = path.split('.')
    const nextConfig = { ...data.forge_config } as Record<string, unknown>
    let current: Record<string, unknown> = nextConfig
    for (let i = 0; i < segments.length - 1; i++) {
      current[segments[i]] = { ...(current[segments[i]] as Record<string, unknown>) }
      current = current[segments[i]] as Record<string, unknown>
    }
    current[segments[segments.length - 1]] = value
    setData({ ...data, forge_config: nextConfig as SettingsData['forge_config'] })
  }

  const overridesEditable = data?.settings_overrides_editable !== false

  const handleSave = async () => {
    if (!data) return
    if (embeddingRisk.dimsChanged) {
      const ok = await confirm({
        title: 'Change embedding dimensions',
        message: 'Changing embedding dimensions requires resetting vector data and re-indexing. Save anyway?',
        confirmLabel: 'Save anyway',
        danger: true,
      })
      if (!ok) return
    }
    setSaving(true)
    setError(null)
    setWarnings([])
    try {
      // Non-admins cannot change global model/provider settings; submit the
      // unchanged baseline so saving repos/indexing never trips a 403.
      const overridesToSend =
        overridesEditable || !baselineOverrides ? data.settings_overrides : baselineOverrides
      const result = await api.settings.update({ forge_config: data.forge_config, settings_overrides: overridesToSend })
      setBaselineEmbedding(getEmbeddingSignature(data.settings_overrides))
      toast.success('Settings saved')
      setWarnings(result.warnings)
    } catch (e) {
      toast.error(String(e))
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="p-8 text-muted text-sm">Loading settings...</div>
  if (!data) return <div className="p-8 text-danger text-sm">Unable to load settings.</div>

  return (
    <div className="p-4 sm:p-8">
      <div className="page-wide">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between mb-6">
          <div>
            <h1>Settings</h1>
            <p className="text-muted text-sm">Runtime configuration — changes take effect immediately.</p>
          </div>
          <Button variant="primary" onClick={handleSave} loading={saving} disabled={saving} className="sm:mt-1 self-start">
            <Save className="w-3.5 h-3.5" />
            Save
          </Button>
        </div>

        {error && (
          <div style={{ border: '1px solid var(--danger)', color: 'var(--danger)' }} className="text-sm p-3 mb-4 bg-[#fef2f2]">
            <AlertCircle className="inline w-3.5 h-3.5 mr-1" />{error}
          </div>
        )}
        {warnings.length > 0 && (
          <div style={{ border: '1px solid var(--warning)', color: 'var(--warning)' }} className="text-sm p-3 mb-4 bg-[#fef9e7]">
            {warnings.map(w => <p key={w}>{w}</p>)}
          </div>
        )}

        <Tabs value={activeTab} onValueChange={v => setActiveTab(v as Tab)}>
          <TabsList>
            <TabsTrigger value="access">API keys</TabsTrigger>
            <TabsTrigger value="models">Models</TabsTrigger>
            <TabsTrigger value="runtime">Runtime</TabsTrigger>
            <TabsTrigger value="mcp_keys">MCP keys</TabsTrigger>
            <TabsTrigger value="channels">Channels</TabsTrigger>
          </TabsList>

          <TabsContent value="access">
            <AccessTab settings={data.settings_overrides} onChange={updateOverride} editable={overridesEditable} />
          </TabsContent>
          <TabsContent value="models">
            <ModelsTab settings={data.settings_overrides} onChange={updateOverride} embeddingRisk={embeddingRisk} editable={overridesEditable} />
          </TabsContent>
          <TabsContent value="runtime">
            <RuntimeTab config={data.forge_config} onChange={updateForgeConfig} />
          </TabsContent>
          <TabsContent value="mcp_keys">
            <McpKeysTab />
          </TabsContent>
          <TabsContent value="channels">
            <ChannelsTab settings={data.settings_overrides} onChange={updateOverride} editable={overridesEditable} onSaveSettings={handleSave} />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}
