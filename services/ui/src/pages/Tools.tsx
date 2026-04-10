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
  const [expanded, setExpanded] = useState(false)
  const snippets = [
    {
      key: 'claude',
      title: 'Claude Code',
      value: 'claude mcp add --transport http context-forge http://localhost:4000/mcp',
    },
    {
      key: 'codex',
      title: 'Codex CLI',
      value: 'codex mcp add context-forge --url http://localhost:4000/mcp',
    },
    {
      key: 'opencode',
      title: 'OpenCode',
      value: `{"mcp":{"context-forge":{"type":"remote","url":"http://localhost:4000/mcp","enabled":true}}}`,
    },
    {
      key: 'cursor',
      title: 'Cursor',
      value: `{"mcpServers":{"context-forge":{"url":"http://localhost:4000/mcp"}}}`,
    },
  ]
  const selectedSnippet = snippets.find((snippet) => snippet.key === selectedKey) ?? snippets[0]

  return (
    <div className="mb-6 rounded-xl border border-gray-800 bg-gray-900/50">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full px-4 py-3 flex items-center justify-between text-left hover:bg-gray-800/50 transition-colors"
      >
        <div>
          <p className="text-sm font-medium text-white">Quick Connect</p>
          <p className="text-xs text-gray-500 mt-0.5">Connect MCP clients</p>
        </div>
        <span className="text-gray-500">{expanded ? '−' : '+'}</span>
      </button>

      {expanded && (
        <div className="px-4 pb-4 border-t border-gray-800 pt-4">
          <div className="flex gap-2 mb-3">
            {snippets.map((snippet) => (
              <button
                key={snippet.key}
                onClick={() => setSelectedKey(snippet.key)}
                className={`px-3 py-1.5 text-xs rounded-lg transition-colors ${
                  selectedKey === snippet.key
                    ? 'bg-cyan-600 text-white'
                    : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
                }`}
              >
                {snippet.title}
              </button>
            ))}
          </div>

          <div className="flex items-start gap-2">
            <code className="flex-1 overflow-x-auto whitespace-pre rounded-lg bg-gray-950 px-3 py-2 text-xs font-mono text-cyan-300">
              {selectedSnippet.value}
            </code>
            <button
              onClick={() => {
                navigator.clipboard.writeText(selectedSnippet.value)
                setCopiedKey(selectedSnippet.key)
                setTimeout(() => setCopiedKey(null), 1500)
              }}
              className="flex-shrink-0 rounded-lg bg-gray-800 p-2 text-gray-500 transition-colors hover:text-gray-300"
            >
              {copiedKey === selectedSnippet.key ? (
                <Check className="h-3.5 w-3.5 text-emerald-400" />
              ) : (
                <Copy className="h-3.5 w-3.5" />
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

  const groups = Object.keys(TOOL_GROUPS)
  const grouped: Record<string, Tool[]> = { memory: [], repo: [], job: [], other: [] }
  tools.forEach(t => {
    const g = getGroup(t.name)
    if (g) grouped[g[0]].push(t)
    else grouped.other.push(t)
  })

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-lg font-semibold text-white flex items-center gap-2">
          <Wrench className="w-4 h-4 text-indigo-400" />
          MCP Tools
        </h1>
      </div>

      <MCPConfigSnippet />

      {error && (
        <div className="mb-4 p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-400">{error}</div>
      )}

      {loading ? (
        <div className="flex items-center justify-center h-32 text-gray-600">
          <Loader2 className="w-4 h-4 animate-spin mr-2" />
          Loading…
        </div>
      ) : (
        <div className="space-y-4">
          {[...groups, 'other'].map(group => {
            const items = grouped[group]
            if (!items?.length) return null
            const groupMeta = TOOL_GROUPS[group]
            return (
              <div key={group}>
                <h2 className={`text-xs font-semibold uppercase tracking-wider mb-2 ${groupMeta?.color ?? 'text-gray-500'}`}>
                  {group}
                </h2>
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-2">
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
