import { useEffect, useState } from 'react'
import { Copy, Check } from 'lucide-react'
import { api, type Tool } from '../lib/api'
import { Button } from '../components/ui'

const TOOL_GROUPS: Record<string, { prefix: string; label: string }> = {
  memory: { prefix: 'memory_', label: 'Memory' },
  repo: { prefix: 'repo_', label: 'Repository' },
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
    <tr style={{ borderBottom: '1px solid var(--border)' }} className="last:border-b-0">
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

const MCP_SNIPPETS = [
  {
    key: 'claude',
    title: 'Claude Code',
    value: `~/.claude/mcp.json:
{
  "mcpServers": {
    "context-forge": {
      "transport": { "type": "http" },
      "baseUrl": "https://contextapi.edia.cloud/mcp",
      "headers": { "Authorization": "Bearer YOUR_TOKEN" }
    }
  }
}`,
  },
  {
    key: 'codex',
    title: 'Codex CLI',
    value: `codex mcp add context-forge \\
  --url https://contextapi.edia.cloud/mcp \\
  --header "Authorization: Bearer YOUR_TOKEN"`,
  },
  {
    key: 'opencode',
    title: 'OpenCode',
    value: `~/.opencode/config.json:
{
  "mcp": {
    "context-forge": {
      "type": "remote",
      "url": "https://contextapi.edia.cloud/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN"
      },
      "enabled": true
    }
  }
}`,
  },
  {
    key: 'cursor',
    title: 'Cursor',
    value: `.cursor/mcp.json:
{
  "mcpServers": {
    "context-forge": {
      "url": "https://contextapi.edia.cloud/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN"
      }
    }
  }
}`,
  },
]

function QuickConnect() {
  const [copiedKey, setCopiedKey] = useState<string | null>(null)
  const [selectedKey, setSelectedKey] = useState('codex')
  const [expanded, setExpanded] = useState(false)
  const selectedSnippet = MCP_SNIPPETS.find(s => s.key === selectedKey) ?? MCP_SNIPPETS[0]

  return (
    <div style={{ border: '1px solid var(--border)' }} className="mb-8">
      <button
        onClick={() => setExpanded(!expanded)}
        style={{ borderBottom: expanded ? '1px solid var(--border)' : 'none' }}
        className="w-full px-4 py-3 flex items-center justify-between text-left hover:bg-surface transition-colors"
      >
        <div>
          <span className="text-sm font-medium">Quick Connect</span>
          <span className="text-xs text-muted ml-3">Connect MCP clients to this server</span>
        </div>
        <span className="text-muted text-sm">{expanded ? '−' : '+'}</span>
      </button>

      {expanded && (
        <div className="px-4 py-4">
          <p className="text-xs text-muted mb-3">
            🔑 Generate a token in <strong>Settings → MCP keys</strong> and replace <code className="text-xs">YOUR_TOKEN</code> in the examples below
          </p>
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
          <div className="flex items-start gap-2">
            <pre className="flex-1 overflow-x-auto text-xs m-0">
              <code>{selectedSnippet.value}</code>
            </pre>
            <button
              onClick={() => {
                navigator.clipboard.writeText(selectedSnippet.value)
                setCopiedKey(selectedSnippet.key)
                setTimeout(() => setCopiedKey(null), 1500)
              }}
              className="flex-shrink-0 text-muted hover:text-text transition-colors p-1 mt-2"
            >
              {copiedKey === selectedSnippet.key ? (
                <Check className="w-3.5 h-3.5 text-success" />
              ) : (
                <Copy className="w-3.5 h-3.5" />
              )}
            </button>
          </div>
        </div>
      )}
    </div>
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

  const grouped: Record<string, Tool[]> = { memory: [], repo: [], job: [], other: [] }
  tools.forEach(t => {
    const g = getGroup(t.name)
    if (g) grouped[g[0]].push(t)
    else grouped.other.push(t)
  })

  return (
    <div className="p-8">
      <div className="page-content">
        <div className="mb-6">
          <h1>MCP Tools</h1>
          <p className="text-muted text-sm">Available tools exposed through the MCP endpoint.</p>
        </div>

        <QuickConnect />

        {error && (
          <div
            style={{ border: '1px solid var(--danger)', color: 'var(--danger)' }}
            className="text-sm p-3 mb-4 bg-[#fef2f2]"
          >
            {error}
          </div>
        )}

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
                  <div style={{ border: '1px solid var(--border)' }}>
                    <table className="w-full">
                      <tbody>
                        {items.map(t => <ToolRow key={t.name} tool={t} />)}
                      </tbody>
                    </table>
                  </div>
                </section>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
