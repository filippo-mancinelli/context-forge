import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertCircle, Check, Clipboard, Plus, Save, Trash2, X } from 'lucide-react'
import { api, type Repo, type MCPApiKey, type OrgRole } from '../lib/api'
import { Banner, Button, Card, Input, Textarea, Select, Tabs, TabsList, TabsTrigger, TabsContent, Badge, Dialog, DialogFooter, Table, Thead, Tbody, Tr, Th, Td, useConfirm, useToast } from '../components/ui'
import { useAppStore } from '../store'

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

function GlobalSettingsNotice({ text }: { text?: React.ReactNode } = {}) {
  return (
    <Card className="text-xs text-muted p-3 mb-4 bg-surface">
      {text ?? (
        <>
          These are <span className="font-medium text-text">global</span> settings shared by every
          organization (they are tied to the shared vector store). Only organization admins can change
          them.
        </>
      )}
    </Card>
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
          className="pr-14"
        />
        <button
          type="button"
          onClick={() => setVisible(v => !v)}
          className="absolute right-2.5 top-1/2 -translate-y-1/2 text-xs text-muted hover:text-text bg-bg pl-1"
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
        <Banner variant="warning">
          <p className="font-medium mb-1">Embedding changes require re-indexing</p>
          <p className="text-xs">
            Changing the embeddings provider or model will invalidate existing vectors. Re-index all repositories after saving.
            {embeddingRisk.dimsChanged && ' Changing dimensions also requires resetting vector-backed data.'}
          </p>
        </Banner>
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
  editable = true,
}: {
  config: SettingsData['forge_config']
  onChange: (path: string, value: unknown) => void
  editable?: boolean
}) {
  const indexing = config.indexing || {}
  const memory = config.memory || { user_id: 'default' }

  return (
    <fieldset disabled={!editable} className="contents">
    {!editable && (
      <GlobalSettingsNotice text="These settings apply to this organization. Only organization admins can change them." />
    )}
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
    </fieldset>
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

function NewKeyBanner({ apiKey, endpoint, onDismiss }: { apiKey: string; endpoint: string; onDismiss: () => void }) {
  return (
    <Banner variant="success" className="p-4 mb-4">
      <div className="flex items-start justify-between mb-2">
        <p className="font-medium">
          <Check className="inline w-3.5 h-3.5 mr-1" />
          Key generated — copy it now, it won't be shown again.
        </p>
        <button type="button" onClick={onDismiss} className="p-1 hover:bg-black/5 rounded" title="Dismiss">
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
      <div className="flex items-center gap-1 bg-surface border border-[var(--success-border)] p-2 rounded">
        <code className="text-xs font-mono flex-1 overflow-x-auto select-all">{apiKey}</code>
        <CopyButton text={apiKey} />
      </div>
      {endpoint && (
        <div className="flex items-center gap-1 bg-surface border border-[var(--success-border)] p-2 rounded mt-2">
          <span className="text-xs text-muted mr-1">Endpoint:</span>
          <code className="text-xs font-mono flex-1 overflow-x-auto select-all">{endpoint}</code>
          <CopyButton text={endpoint} />
        </div>
      )}
    </Banner>
  )
}

const KEY_PERMISSION_OPTIONS = [
  { value: '*', label: 'All tools (*)' },
  { value: 'context-read', label: 'context-read — search & read context' },
  { value: 'context-write', label: 'context-write — annotate & store memory' },
  { value: 'db-query', label: 'db-query — read-only SQL on connected databases' },
  { value: 'repo-write', label: 'repo-write — index & manage repositories' },
  { value: 'jobs', label: 'jobs — submit & manage background jobs' },
]

function togglePermission(current: string[], value: string): string[] {
  if (value === '*') return current.includes('*') ? [] : ['*']
  const without = current.filter((p) => p !== '*' && p !== value)
  return current.includes(value) ? without : [...without, value]
}

function McpKeysTab() {
  const [keys, setKeys] = useState<MCPApiKey[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [creating, setCreating] = useState(false)
  const confirm = useConfirm()
  const toast = useToast()
  const [newKey, setNewKey] = useState<{ key: string; name: string; endpoint: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [keyName, setKeyName] = useState('')
  const [keyPermissions, setKeyPermissions] = useState<string[]>(['context-read', 'context-write'])
  const [expiresDays, setExpiresDays] = useState('')
  const [rateLimit, setRateLimit] = useState('')
  const projects = useAppStore((s) => s.projects)
  const activeProjectId = useAppStore((s) => s.activeProjectId)
  const organizations = useAppStore((s) => s.organizations)
  const activeOrgId = useAppStore((s) => s.activeOrgId)
  const orgSlug = organizations.find((o) => o.id === activeOrgId)?.slug ?? ''
  const [keyProjectId, setKeyProjectId] = useState<number | null>(activeProjectId)

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
    if (rateLimit && (!/^\d+$/.test(rateLimit) || Number(rateLimit) < 1 || Number(rateLimit) > 100000)) {
      toast.error('Rate limit must be a positive integer between 1 and 100000, or left empty for unlimited.')
      return
    }
    setCreating(true)
    setError(null)
    try {
      const chosen = projects.find((p) => p.id === keyProjectId)
      const response = await api.mcpKeys.create({
        name: keyName,
        permissions: keyPermissions,
        expires_days: expiresDays ? parseInt(expiresDays, 10) : undefined,
        project_id: keyProjectId ?? undefined,
        rate_limit_per_minute: rateLimit ? parseInt(rateLimit, 10) : undefined,
      })
      const endpoint = chosen && orgSlug ? `/mcp/${orgSlug}/${chosen.slug}` : ''
      setNewKey({ key: response.key, name: response.name, endpoint })
      toast.success(`API key "${response.name}" generated`)
      await loadKeys()
      setShowCreate(false)
      setKeyName('')
      setExpiresDays('')
      setRateLimit('')
      setKeyPermissions(['context-read', 'context-write'])
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
        <Button variant="primary" size="sm" onClick={() => { setKeyProjectId(activeProjectId); setShowCreate(true) }} className="self-start">
          <Plus className="w-3.5 h-3.5" />
          Generate key
        </Button>
      </div>

      {error && (
        <Banner variant="danger" className="mb-4">
          <AlertCircle className="inline w-3.5 h-3.5 mr-1" />{error}
        </Banner>
      )}

      {newKey && (
        <NewKeyBanner apiKey={newKey.key} endpoint={newKey.endpoint} onDismiss={() => setNewKey(null)} />
      )}

      {loading ? (
        <p className="text-muted text-sm">Loading...</p>
      ) : keys.length === 0 ? (
        <p className="text-muted text-sm">No API keys configured yet.</p>
      ) : (
        <Card className="overflow-x-auto">
          <Table>
            <Thead>
              <Tr>
                <Th>Name</Th>
                <Th>Project</Th>
                <Th>Permissions</Th>
                <Th>Rate limit</Th>
                <Th>Created</Th>
                <Th>Last used</Th>
                <Th>Expires</Th>
                <Th></Th>
              </Tr>
            </Thead>
            <Tbody>
              {keys.map(key => (
                <Tr key={key.id} className="hover:bg-surface">
                  <Td className="font-medium">
                    {key.name}
                    {isExpired(key.expires_at) && <Badge variant="danger" className="ml-2">Expired</Badge>}
                  </Td>
                  <Td className="text-xs">
                    {key.project_slug ? (
                      <div className="flex items-center gap-1">
                        <span className="text-muted">{projects.find((p) => p.slug === key.project_slug)?.name ?? key.project_slug}</span>
                        {orgSlug && (
                          <>
                            <code className="font-mono text-muted">/mcp/{orgSlug}/{key.project_slug}</code>
                            <CopyButton text={`/mcp/${orgSlug}/${key.project_slug}`} />
                          </>
                        )}
                      </div>
                    ) : (
                      '—'
                    )}
                  </Td>
                  <Td>
                    <code className="text-xs font-mono text-muted">{key.permissions ?? key.scope}</code>
                  </Td>
                  <Td className="text-xs text-muted">
                    {key.rate_limit_per_minute ? `${key.rate_limit_per_minute}/min` : 'Unlimited'}
                  </Td>
                  <Td className="text-xs text-muted">{fmtDate(key.created_at)}</Td>
                  <Td className="text-xs text-muted">{fmtDate(key.last_used_at)}</Td>
                  <Td className="text-xs text-muted">
                    {key.expires_at ? <span className={isExpired(key.expires_at) ? 'text-danger' : ''}>{fmtDate(key.expires_at)}</span> : 'Never'}
                  </Td>
                  <Td>
                    <Button size="sm" variant="ghost" onClick={() => handleRevoke(key.id)} title="Revoke" aria-label={`Revoke ${key.name}`}>
                      <Trash2 className="w-3 h-3" />
                    </Button>
                  </Td>
                </Tr>
              ))}
            </Tbody>
          </Table>
        </Card>
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
            label="Project"
            value={String(keyProjectId ?? '')}
            onValueChange={(v) => setKeyProjectId(Number(v))}
            options={projects.map((p) => ({ value: String(p.id), label: p.name }))}
          />
          <div>
            <span className="text-sm font-medium block mb-1.5">Permissions</span>
            <div className="space-y-1.5">
              {KEY_PERMISSION_OPTIONS.map((opt) => (
                <label key={opt.value} className="flex items-center gap-2 text-sm cursor-pointer">
                  <input
                    type="checkbox"
                    checked={keyPermissions.includes(opt.value)}
                    onChange={() => setKeyPermissions((p) => togglePermission(p, opt.value))}
                  />
                  <span>{opt.label}</span>
                </label>
              ))}
            </div>
          </div>
          <Input
            label="Expires in days (optional)"
            type="number"
            value={expiresDays}
            onChange={e => setExpiresDays(e.target.value)}
            placeholder="Leave empty for no expiration"
          />
          <Input
            label="Rate limit (calls/min)"
            type="number"
            min={1}
            value={rateLimit}
            onChange={e => setRateLimit(e.target.value)}
            placeholder="Leave empty for unlimited"
            hint="Applies to this key only, per server process."
          />
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setShowCreate(false)}>Cancel</Button>
          <Button variant="primary" onClick={handleCreate} disabled={!keyName || keyPermissions.length === 0 || creating} loading={creating}>
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
  const [requiresReindex, setRequiresReindex] = useState(false)
  const [reembedding, setReembedding] = useState(false)
  const confirm = useConfirm()
  const toast = useToast()

  const organizations = useAppStore((s) => s.organizations)
  const activeOrgId = useAppStore((s) => s.activeOrgId)
  const myRole: OrgRole = organizations.find((o) => o.id === activeOrgId)?.role ?? 'viewer'
  const runtimeEditable = myRole === 'admin' || myRole === 'owner'

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
      setRequiresReindex(result.requires_reindex)
    } catch (e) {
      toast.error(String(e))
    } finally {
      setSaving(false)
    }
  }

  const startReembed = async () => {
    setReembedding(true)
    try {
      const res = await api.settings.reembed()
      toast.success(`Re-embed started (job ${res.job_id})`)
    } catch (e) {
      toast.error(String(e))
    } finally {
      setReembedding(false)
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
          <Banner variant="danger" className="mb-4">
            <AlertCircle className="inline w-3.5 h-3.5 mr-1" />{error}
          </Banner>
        )}
        {warnings.length > 0 && (
          <Banner variant="warning" className="mb-4">
            {warnings.map(w => <p key={w}>{w}</p>)}
          </Banner>
        )}
        {requiresReindex && (
          <Banner variant="warning" className="mb-4 flex items-center justify-between gap-3">
            <span>Embeddings configuration changed. Re-embed this organization's content.</span>
            <Button size="sm" variant="primary" onClick={startReembed} loading={reembedding}>
              Re-embed now
            </Button>
          </Banner>
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
            <RuntimeTab config={data.forge_config} onChange={updateForgeConfig} editable={runtimeEditable} />
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
