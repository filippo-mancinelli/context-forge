import { useEffect, useState } from 'react'
import { Wrench, Loader2, Copy, Check } from 'lucide-react'
import { api, type Tool } from '../lib/api'

const TOOL_GROUPS: Record<string, { prefix: string; color: string; bg: string }> = {
  memory: { prefix: 'memory_', color: 'text-purple-400', bg: 'bg-purple-500/10 ring-purple-500/30' },
  repo: { prefix: 'repo_', color: 'text-blue-400', bg: 'bg-blue-500/10 ring-blue-500/30' },
  job: { prefix: 'job_', color: 'text-amber-400', bg: 'bg-amber-500/10 ring-amber-500/30' },
}

function getGroup(name: string) {
  return Object.entries(TOOL_GROUPS).find(([, v]) => name.startsWith(v.prefix))
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
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 hover:border-gray-700 transition-colors">
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="flex items-center gap-2">
          {group && (
            <span className={`text-xs px-1.5 py-0.5 rounded ring-1 ${group[1].color} ${group[1].bg} font-medium`}>
              {group[0]}
            </span>
          )}
          <code className="text-sm font-mono text-white">{tool.name}()</code>
        </div>
        <button
          onClick={handleCopy}
          className="p-1 text-gray-600 hover:text-gray-400 transition-colors flex-shrink-0"
          title="Copy tool name"
        >
          {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
        </button>
      </div>
      <p className="text-xs text-gray-400 leading-relaxed">{tool.description || '—'}</p>
    </div>
  )
}

function MCPConfigSnippet() {
  const [copiedKey, setCopiedKey] = useState<string | null>(null)
  const [selectedKey, setSelectedKey] = useState('codex')
  const snippets = [
    {
      key: 'claude',
      title: 'Claude Code',
      summary: 'CLI add command for a local MCP HTTP endpoint.',
      value: 'claude mcp add --transport http context-forge http://localhost:4000/mcp',
    },
    {
      key: 'codex',
      title: 'Codex CLI',
      summary: 'Current Codex syntax for streamable HTTP MCP servers.',
      value: 'codex mcp add context-forge --url http://localhost:4000/mcp',
    },
    {
      key: 'opencode',
      title: 'OpenCode (opencode.json)',
      summary: 'Drop this into your OpenCode config file.',
      value: `{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "context-forge": {
      "type": "remote",
      "url": "http://localhost:4000/mcp",
      "enabled": true
    }
  }
}`,
    },
    {
      key: 'cursor',
      title: 'Cursor (.cursor/mcp.json)',
      summary: 'Workspace-level MCP config for Cursor.',
      value: `{
  "mcpServers": {
    "context-forge": {
      "url": "http://localhost:4000/mcp"
    }
  }
}`,
    },
  ]
  const selectedSnippet = snippets.find((snippet) => snippet.key === selectedKey) ?? snippets[0]

  return (
    <div className="mb-8 rounded-2xl border border-gray-800 bg-gradient-to-br from-gray-900 via-gray-900 to-gray-950 p-5">
      <div className="mb-4 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-cyan-400">Quick Connect</p>
          <h2 className="mt-1 text-lg font-semibold text-white">Connect your MCP client in one step</h2>
          <p className="mt-1 text-sm text-gray-400">
            Pick a client, copy the snippet, then point it to your local or remote `context-forge` MCP URL.
          </p>
        </div>
        <div className="w-full lg:w-72">
          <label className="mb-1 block text-xs font-medium uppercase tracking-wider text-gray-500">
            Client
          </label>
          <select
            value={selectedKey}
            onChange={(e) => setSelectedKey(e.target.value)}
            className="w-full rounded-xl border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-200 outline-none transition-colors focus:border-cyan-500"
          >
            {snippets.map((snippet) => (
              <option key={snippet.key} value={snippet.key}>
                {snippet.title}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="mb-3 rounded-xl border border-gray-800 bg-gray-950/70 p-3">
        <p className="text-sm font-medium text-white">{selectedSnippet.title}</p>
        <p className="mt-1 text-xs leading-relaxed text-gray-400">{selectedSnippet.summary}</p>
      </div>

      <div className="flex items-start gap-2">
        <code className="flex-1 overflow-x-auto whitespace-pre rounded-xl bg-gray-950 px-4 py-3 text-xs font-mono text-cyan-300">
          {selectedSnippet.value}
        </code>
        <button
          onClick={() => {
            navigator.clipboard.writeText(selectedSnippet.value)
            setCopiedKey(selectedSnippet.key)
            setTimeout(() => setCopiedKey(null), 1500)
          }}
          className="flex-shrink-0 rounded-xl bg-gray-800 p-2.5 text-gray-500 transition-colors hover:text-gray-300"
          title={`Copy ${selectedSnippet.title} config`}
        >
          {copiedKey === selectedSnippet.key ? (
            <Check className="h-3.5 w-3.5 text-emerald-400" />
          ) : (
            <Copy className="h-3.5 w-3.5" />
          )}
        </button>
      </div>

      <div className="mt-4 grid gap-3 text-xs text-gray-400 lg:grid-cols-3">
        <div className="rounded-xl border border-gray-800 bg-gray-950/60 p-3">
          <p className="font-medium text-gray-200">Local default</p>
          <p className="mt-1">Use `http://localhost:4000/mcp` when the stack runs on the same machine as the client.</p>
        </div>
        <div className="rounded-xl border border-gray-800 bg-gray-950/60 p-3">
          <p className="font-medium text-gray-200">Remote server</p>
          <p className="mt-1">Replace `localhost` with your server hostname or reverse proxy URL.</p>
        </div>
        <div className="rounded-xl border border-gray-800 bg-gray-950/60 p-3">
          <p className="font-medium text-gray-200">Need auth?</p>
          <p className="mt-1">If you later protect the MCP endpoint, keep the same client entry and add auth at the proxy layer.</p>
        </div>
      </div>
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

  const groups = Object.keys(TOOL_GROUPS)
  const grouped: Record<string, Tool[]> = { memory: [], repo: [], job: [], other: [] }
  tools.forEach(t => {
    const g = getGroup(t.name)
    if (g) grouped[g[0]].push(t)
    else grouped.other.push(t)
  })

  return (
    <div className="p-8">
      <div className="mb-8">
        <h1 className="text-xl font-semibold text-white flex items-center gap-2">
          <Wrench className="w-5 h-5 text-indigo-400" />
          MCP Tools
        </h1>
        <p className="text-sm text-gray-500 mt-1">{tools.length} tools available at :4000/mcp</p>
      </div>

      <MCPConfigSnippet />

      {error && (
        <div className="mb-6 p-4 bg-red-500/10 border border-red-500/30 rounded-xl text-sm text-red-400">{error}</div>
      )}

      {loading ? (
        <div className="flex items-center justify-center h-48 text-gray-600">
          <Loader2 className="w-5 h-5 animate-spin mr-2" />
          Loading…
        </div>
      ) : (
        <div className="space-y-8">
          {[...groups, 'other'].map(group => {
            const items = grouped[group]
            if (!items?.length) return null
            const groupMeta = TOOL_GROUPS[group]
            return (
              <div key={group}>
                <h2 className={`text-xs font-semibold uppercase tracking-wider mb-3 ${groupMeta?.color ?? 'text-gray-500'}`}>
                  {group}
                </h2>
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                  {items.map(t => <ToolCard key={t.name} tool={t} />)}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
