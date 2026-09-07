import { useEffect, useState } from 'react'
import { Copy, Check } from 'lucide-react'
import { api, type Tool } from '../lib/api'
import { Banner, Button, Card } from '../components/ui'
import { useAppStore } from '../store'

const TOOL_GROUPS: Record<string, { prefix: string; label: string }> = {
  memory: { prefix: 'memory_', label: 'Memory' },
  repo: { prefix: 'repo_', label: 'Repository' },
  code: { prefix: 'code_', label: 'Code intelligence' },
  kb: { prefix: 'kb_', label: 'Knowledge base' },
  web: { prefix: 'web_', label: 'Web pages' },
  db: { prefix: 'db_', label: 'Databases' },
  api: { prefix: 'api_', label: 'API contracts' },
  ci: { prefix: 'ci_', label: 'CI' },
  job: { prefix: 'job_', label: 'Jobs' },
}

function getGroup(name: string) {
  return Object.entries(TOOL_GROUPS).find(([, v]) => name.startsWith(v.prefix))
}

function ToolRow({ tool }: { tool: Tool }) {
  const [copied, setCopied] = useState(false)
  const group = getGroup(tool.name)

  const handleCopy = () => {
    navigator.clipboard.writeText(tool.name)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <tr className="border-b border-border last:border-b-0">
      <td className="py-3 px-4 align-top w-48">
        <div className="flex items-center gap-2">
          <code className="text-sm font-mono text-text">{tool.name}()</code>
        </div>
        {group && (
          <span className="text-xs text-muted">{group[1].label}</span>
        )}
      </td>
      <td className="py-3 px-4 align-top text-sm text-muted">{tool.description || '—'}</td>
      <td className="py-3 px-4 align-top w-10">
        <button
          onClick={handleCopy}
          className="text-muted hover:text-text transition-colors"
          title="Copy tool name"
        >
          {copied ? <Check className="w-3.5 h-3.5 text-success" /> : <Copy className="w-3.5 h-3.5" />}
        </button>
      </td>
    </tr>
  )
}

function ToolCard({ tool }: { tool: Tool }) {
  const [copied, setCopied] = useState(false)
  const group = getGroup(tool.name)

  const handleCopy = () => {
    navigator.clipboard.writeText(tool.name)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <Card className="p-3">
      <div className="flex items-start justify-between gap-2">
        <code className="text-sm font-mono text-text break-all">{tool.name}()</code>
        <button
          onClick={handleCopy}
          className="text-muted hover:text-text transition-colors flex-shrink-0 mt-0.5"
          title="Copy tool name"
        >
          {copied ? <Check className="w-3.5 h-3.5 text-success" /> : <Copy className="w-3.5 h-3.5" />}
        </button>
      </div>
      {group && <span className="text-xs text-muted">{group[1].label}</span>}
      {tool.description && (
        <p className="text-sm text-muted mt-1 break-words">{tool.description}</p>
      )}
    </Card>
  )
}

type McpSnippet = {
  key: string
  title: string
  /** Path of the client's config file */
  path: string
  /** Content to put in the config file (JSON / TOML) */
  config: string
  /** Shell command(s) that achieve the same result from the terminal */
  cli: string
  cliNote?: string
  /** OAuth variant of the config file (no API-key header) */
  configOAuth: string
  /** OAuth variant of the CLI command */
  cliOAuth: string
}

const PI_CONFIG = `{
  "mcpServers": {
    "context-forge": {
      "url": "{MCP_URL}",
      "headers": {
        "X-API-Key": "{YOUR_API_KEY}"
      }
    }
  }
}`

const CURSOR_CONFIG = `{
  "mcpServers": {
    "context-forge": {
      "url": "{MCP_URL}",
      "headers": {
        "X-API-Key": "{YOUR_API_KEY}"
      }
    }
  }
}`

const WINDSURF_CONFIG = `{
  "mcpServers": {
    "context-forge": {
      "serverUrl": "{MCP_URL}",
      "headers": {
        "X-API-Key": "{YOUR_API_KEY}"
      }
    }
  }
}`

const OPENCODE_CONFIG = `{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "context-forge": {
      "type": "remote",
      "url": "{MCP_URL}",
      "headers": {
        "X-API-Key": "{YOUR_API_KEY}"
      },
      "enabled": true
    }
  }
}`

const CODEX_CONFIG = `[mcp_servers.context-forge]
url = "{MCP_URL}"
http_headers = { "X-API-Key" = "{YOUR_API_KEY}" }`

// OAuth variants (auto-discovery): url only, no header — the client discovers the IdP.
const PI_OAUTH = `{
  "mcpServers": {
    "context-forge": {
      "url": "{MCP_URL}"
    }
  }
}`

const CURSOR_OAUTH = PI_OAUTH

const CLAUDE_OAUTH = `{
  "mcpServers": {
    "context-forge": {
      "type": "http",
      "url": "{MCP_URL}"
    }
  }
}`

const VSCODE_OAUTH = `{
  "servers": {
    "context-forge": {
      "type": "http",
      "url": "{MCP_URL}"
    }
  }
}`

const WINDSURF_OAUTH = `{
  "mcpServers": {
    "context-forge": {
      "serverUrl": "{MCP_URL}"
    }
  }
}`

const OPENCODE_OAUTH = `{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "context-forge": {
      "type": "remote",
      "url": "{MCP_URL}",
      "enabled": true
    }
  }
}`

const CODEX_OAUTH = `[mcp_servers.context-forge]
url = "{MCP_URL}"`

const OVERWRITE_NOTE = 'The command creates the file from scratch — if it already exists, merge the entry into it manually.'

const MCP_SNIPPETS: McpSnippet[] = [
  {
    key: 'pi',
    title: 'Pi',
    path: '~/.pi/agent/mcp.json',
    config: PI_CONFIG,
    cli: `mkdir -p ~/.pi/agent && cat > ~/.pi/agent/mcp.json <<'EOF'
${PI_CONFIG}
EOF`,
    cliNote: OVERWRITE_NOTE,
    configOAuth: PI_OAUTH,
    cliOAuth: `mkdir -p ~/.pi/agent && cat > ~/.pi/agent/mcp.json <<'EOF'
${PI_OAUTH}
EOF`,
  },
  {
    key: 'claude',
    title: 'Claude Code',
    path: '.mcp.json (project root, shared with the team)',
    config: `{
  "mcpServers": {
    "context-forge": {
      "type": "http",
      "url": "{MCP_URL}",
      "headers": {
        "X-API-Key": "{YOUR_API_KEY}"
      }
    }
  }
}`,
    cli: `claude mcp add --transport http context-forge {MCP_URL} \\
  --header "X-API-Key: {YOUR_API_KEY}"`,
    configOAuth: CLAUDE_OAUTH,
    cliOAuth: `claude mcp add --transport http context-forge {MCP_URL}`,
  },
  {
    key: 'codex',
    title: 'Codex CLI',
    path: '~/.codex/config.toml',
    config: CODEX_CONFIG,
    cli: `cat >> ~/.codex/config.toml <<'EOF'

${CODEX_CONFIG}
EOF`,
    cliNote: 'codex mcp add cannot set custom headers; the X-API-Key header must live in config.toml (the command above appends it).',
    configOAuth: CODEX_OAUTH,
    cliOAuth: `cat >> ~/.codex/config.toml <<'EOF'

${CODEX_OAUTH}
EOF`,
  },
  {
    key: 'cursor',
    title: 'Cursor',
    path: '.cursor/mcp.json (project) or ~/.cursor/mcp.json (global)',
    config: CURSOR_CONFIG,
    cli: `mkdir -p .cursor && cat > .cursor/mcp.json <<'EOF'
${CURSOR_CONFIG}
EOF`,
    cliNote: OVERWRITE_NOTE,
    configOAuth: CURSOR_OAUTH,
    cliOAuth: `mkdir -p .cursor && cat > .cursor/mcp.json <<'EOF'
${CURSOR_OAUTH}
EOF`,
  },
  {
    key: 'vscode',
    title: 'VS Code',
    path: '.vscode/mcp.json',
    config: `{
  "servers": {
    "context-forge": {
      "type": "http",
      "url": "{MCP_URL}",
      "headers": {
        "X-API-Key": "{YOUR_API_KEY}"
      }
    }
  }
}`,
    cli: `code --add-mcp '{"name":"context-forge","type":"http","url":"{MCP_URL}","headers":{"X-API-Key":"{YOUR_API_KEY}"}}'`,
    configOAuth: VSCODE_OAUTH,
    cliOAuth: `code --add-mcp '{"name":"context-forge","type":"http","url":"{MCP_URL}"}'`,
  },
  {
    key: 'windsurf',
    title: 'Windsurf',
    path: '~/.codeium/windsurf/mcp_config.json',
    config: WINDSURF_CONFIG,
    cli: `mkdir -p ~/.codeium/windsurf && cat > ~/.codeium/windsurf/mcp_config.json <<'EOF'
${WINDSURF_CONFIG}
EOF`,
    cliNote: OVERWRITE_NOTE,
    configOAuth: WINDSURF_OAUTH,
    cliOAuth: `mkdir -p ~/.codeium/windsurf && cat > ~/.codeium/windsurf/mcp_config.json <<'EOF'
${WINDSURF_OAUTH}
EOF`,
  },
  {
    key: 'opencode',
    title: 'OpenCode',
    path: 'opencode.json (project) or ~/.config/opencode/opencode.json (global)',
    config: OPENCODE_CONFIG,
    cli: `cat > opencode.json <<'EOF'
${OPENCODE_CONFIG}
EOF`,
    cliNote: OVERWRITE_NOTE,
    configOAuth: OPENCODE_OAUTH,
    cliOAuth: `cat > opencode.json <<'EOF'
${OPENCODE_OAUTH}
EOF`,
  },
]

function QuickConnect() {
  const [copiedKey, setCopiedKey] = useState<string | null>(null)
  const [copiedUrl, setCopiedUrl] = useState(false)
  const [selectedKey, setSelectedKey] = useState('pi')
  const [expanded, setExpanded] = useState(false)
  const [authMode, setAuthMode] = useState<'oauth' | 'apikey'>('oauth')
  // Externally reachable MCP base, resolved at runtime from the server so we
  // don't guess the internal MCP port on proxied deployments.
  const [serverMcpBase, setServerMcpBase] = useState<string | null>(null)

  useEffect(() => {
    api.config.get()
      .then(cfg => setServerMcpBase(cfg.public_mcp_url || ''))
      .catch(() => setServerMcpBase(''))
  }, [])

  // The organization exposes a single MCP endpoint at /mcp/{org}. It grants
  // visibility over every project the caller can access: discover them with the
  // list_projects tool and pick one with use_project, no per-project URL needed.
  const organizations = useAppStore((s) => s.organizations)
  const activeOrgId = useAppStore((s) => s.activeOrgId)
  const activeOrg = organizations.find((o) => o.id === activeOrgId)
  const orgSlug = activeOrg?.slug ?? '{org}'

  // Resolve the MCP base URL, in priority order:
  //   1. VITE_MCP_URL — explicit build-time override.
  //   2. public_mcp_url from the server — the real externally reachable base
  //      (behind the reverse proxy), correct on proxied deployments.
  //   3. Derived: reuse the browser origin but swap in the default MCP port
  //      (4000). Only right for local dev; flagged with a warning.
  const explicitMcpUrl = import.meta.env.VITE_MCP_URL
  const apiUrl = import.meta.env.VITE_API_URL
  const mcpPath = `/mcp/${orgSlug}`
  const configuredBase = explicitMcpUrl || serverMcpBase || ''
  let mcpUrl: string
  let urlIsDerived = false
  if (configuredBase) {
    mcpUrl = configuredBase.replace(/\/+$/, '') + mcpPath
  } else {
    urlIsDerived = true
    const base = apiUrl || window.location.origin
    try {
      const u = new URL(base)
      u.port = '4000'
      mcpUrl = u.origin + mcpPath
    } catch {
      mcpUrl = base + mcpPath
    }
  }

  const raw = MCP_SNIPPETS.find(s => s.key === selectedKey) ?? MCP_SNIPPETS[0]
  const fill = (s: string) => s.split('{MCP_URL}').join(mcpUrl)
  const rawConfig = authMode === 'oauth' ? raw.configOAuth : raw.config
  const rawCli = authMode === 'oauth' ? raw.cliOAuth : raw.cli
  const selectedSnippet = {
    ...raw,
    config: fill(rawConfig),
    cli: fill(rawCli),
  }

  const copyUrl = () => {
    navigator.clipboard.writeText(mcpUrl)
    setCopiedUrl(true)
    setTimeout(() => setCopiedUrl(false), 1500)
  }

  return (
    <Card className="mb-8">
      <button
        onClick={() => setExpanded(!expanded)}
        className={`w-full px-4 py-3 flex items-center justify-between text-left hover:bg-surface transition-colors ${expanded ? 'border-b border-border' : ''}`}
      >
        <div>
          <span className="text-sm font-medium">Quick Connect</span>
          <span className="text-xs text-muted ml-3">Connect MCP clients to this server</span>
        </div>
        <span className="text-muted text-sm">{expanded ? '−' : '+'}</span>
      </button>

      {expanded && (
        <div className="px-4 py-4">
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs text-muted">MCP endpoint:</span>
            <code className="text-xs font-mono bg-surface px-2 py-0.5 rounded">{mcpUrl}</code>
            <button
              onClick={copyUrl}
              className="text-muted hover:text-text transition-colors"
              title="Copy MCP URL"
            >
              {copiedUrl ? <Check className="w-3.5 h-3.5 text-success" /> : <Copy className="w-3.5 h-3.5" />}
            </button>
          </div>
          {urlIsDerived && (
            <Banner variant="warning" className="mb-3">
              ⚠️ MCP URL derived assuming the default MCP port (4000). If the MCP server is
              reachable at a different address, set <code>VITE_MCP_URL</code> at build time.
            </Banner>
          )}
          <p className="text-xs text-muted mb-3">
            🗂️ This is the organization-level endpoint: it exposes every project you can access. Call the <code className="text-xs">list_projects</code> tool to see them, then <code className="text-xs">use_project</code> to pick the active one for the session — no per-project URL needed.
          </p>
          <div className="flex gap-2 mb-3">
            <Button
              size="sm"
              variant={authMode === 'oauth' ? 'primary' : 'secondary'}
              onClick={() => setAuthMode('oauth')}
            >
              OAuth (recommended)
            </Button>
            <Button
              size="sm"
              variant={authMode === 'apikey' ? 'primary' : 'secondary'}
              onClick={() => setAuthMode('apikey')}
            >
              API key
            </Button>
          </div>
          {authMode === 'oauth' ? (
            <p className="text-xs text-muted mb-3">
              🔓 <strong>OAuth (recommended)</strong>: no API key to configure. The client discovers
              the identity provider automatically from the endpoint's metadata and logs in to
              Keycloak on its own — just the URL below.
            </p>
          ) : (
            <p className="text-xs text-muted mb-3">
              🔑 Generate an API key in <strong>Settings → MCP keys</strong> and replace{' '}
              <code className="text-xs">{'{YOUR_API_KEY}'}</code> below. An org-wide key sees every
              project; a project-scoped key is limited to its own projects, still on this same endpoint.
            </p>
          )}
          <div className="flex gap-2 mb-3 flex-wrap">
            {MCP_SNIPPETS.map(snippet => (
              <Button
                key={snippet.key}
                size="sm"
                variant={selectedKey === snippet.key ? 'primary' : 'secondary'}
                onClick={() => setSelectedKey(snippet.key)}
              >
                {snippet.title}
              </Button>
            ))}
          </div>
          <p className="text-xs text-muted mb-2">📁 Config file — save to <code className="text-xs">{raw.path}</code></p>
          <div className="flex items-start gap-2 mb-4">
            <pre className="flex-1 overflow-x-auto text-xs m-0 bg-surface p-3 rounded">
              <code>{selectedSnippet.config}</code>
            </pre>
            <button
              onClick={() => {
                navigator.clipboard.writeText(selectedSnippet.config)
                setCopiedKey(`${selectedSnippet.key}:config`)
                setTimeout(() => setCopiedKey(null), 1500)
              }}
              className="flex-shrink-0 text-muted hover:text-text transition-colors p-1 mt-2"
            >
              {copiedKey === `${selectedSnippet.key}:config` ? (
                <Check className="w-3.5 h-3.5 text-success" />
              ) : (
                <Copy className="w-3.5 h-3.5" />
              )}
            </button>
          </div>

          <p className="text-xs text-muted mb-2">💻 Or from the command line</p>
          <div className="flex items-start gap-2">
            <pre className="flex-1 overflow-x-auto text-xs m-0 bg-surface p-3 rounded">
              <code>{selectedSnippet.cli}</code>
            </pre>
            <button
              onClick={() => {
                navigator.clipboard.writeText(selectedSnippet.cli)
                setCopiedKey(`${selectedSnippet.key}:cli`)
                setTimeout(() => setCopiedKey(null), 1500)
              }}
              className="flex-shrink-0 text-muted hover:text-text transition-colors p-1 mt-2"
            >
              {copiedKey === `${selectedSnippet.key}:cli` ? (
                <Check className="w-3.5 h-3.5 text-success" />
              ) : (
                <Copy className="w-3.5 h-3.5" />
              )}
            </button>
          </div>
          {authMode === 'apikey' && selectedSnippet.cliNote && (
            <p className="text-xs text-muted mt-2">ℹ️ {selectedSnippet.cliNote}</p>
          )}
        </div>
      )}
    </Card>
  )
}

export default function Tools() {
  const [tools, setTools] = useState<Tool[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.tools.list()
      .then(data => { setTools(data.tools); setLoading(false) })
      .catch(e => { setError(String(e)); setLoading(false) })
  }, [])

  const grouped: Record<string, Tool[]> = Object.fromEntries(
    [...Object.keys(TOOL_GROUPS), 'other'].map(k => [k, [] as Tool[]]),
  )
  tools.forEach(t => {
    const g = getGroup(t.name)
    if (g) grouped[g[0]].push(t)
    else grouped.other.push(t)
  })

  return (
    <div className="p-4 sm:p-8">
      <div className="page-wide">
        <div className="mb-6">
          <h1>MCP Tools</h1>
          <p className="text-muted text-sm">Available tools exposed through the MCP endpoint.</p>
        </div>

        <QuickConnect />

        {error && <Banner variant="danger" className="mb-4">{error}</Banner>}

        {loading ? (
          <p className="text-muted text-sm">Loading...</p>
        ) : (
          <div className="space-y-8">
            {[...Object.keys(TOOL_GROUPS), 'other'].map(group => {
              const items = grouped[group]
              if (!items?.length) return null
              const meta = TOOL_GROUPS[group]
              return (
                <section key={group}>
                  <h2 className="text-base font-semibold mb-3">
                    {meta?.label ?? 'Other'} tools
                  </h2>
                  {/* Mobile: stacked cards */}
                  <div className="space-y-3 md:hidden">
                    {items.map(t => <ToolCard key={t.name} tool={t} />)}
                  </div>
                  {/* Desktop: table */}
                  <Card className="hidden md:block overflow-x-auto">
                    <table className="w-full min-w-[480px]">
                      <tbody>
                        {items.map(t => <ToolRow key={t.name} tool={t} />)}
                      </tbody>
                    </table>
                  </Card>
                </section>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
