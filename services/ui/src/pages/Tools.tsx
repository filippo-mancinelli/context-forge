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
  const [copied, setCopied] = useState(false)
  const snippet = `claude mcp add --transport http context-forge http://localhost:4000/mcp`

  return (
    <div className="mb-8 p-4 bg-gray-900 border border-gray-800 rounded-xl">
      <p className="text-xs text-gray-500 mb-2 font-medium uppercase tracking-wider">Quick Connect</p>
      <div className="flex items-center gap-2">
        <code className="flex-1 text-xs font-mono text-indigo-300 bg-gray-800 px-3 py-2 rounded-lg overflow-x-auto">
          {snippet}
        </code>
        <button
          onClick={() => { navigator.clipboard.writeText(snippet); setCopied(true); setTimeout(() => setCopied(false), 1500) }}
          className="flex-shrink-0 p-2 text-gray-500 hover:text-gray-300 bg-gray-800 rounded-lg transition-colors"
        >
          {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
        </button>
      </div>
      <p className="text-xs text-gray-600 mt-2">Or add to Cursor's <code className="font-mono">.cursor/mcp.json</code> with <code className="font-mono">"url": "http://localhost:4000/mcp"</code></p>
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
