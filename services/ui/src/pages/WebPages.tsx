import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Globe,
  Trash2,
  RefreshCw,
  Search as SearchIcon,
  AlertCircle,
  ExternalLink,
  Plus,
  ChevronDown,
  ChevronRight,
  Settings2,
} from 'lucide-react'
import { api, type WebPage, type WebSite, type WebSearchResult } from '../lib/api'
import { Button, Input, Badge, useConfirm, useToast } from '../components/ui'

const STATUS_VARIANT: Record<WebPage['status'], 'success' | 'accent' | 'warning' | 'danger'> = {
  ready: 'success',
  processing: 'accent',
  pending: 'warning',
  error: 'danger',
}

const STATUS_LABEL: Record<WebPage['status'], string> = {
  ready: 'Ready',
  processing: 'Fetching',
  pending: 'Queued',
  error: 'Error',
}

const SITE_STATUS_VARIANT: Record<WebSite['status'], 'success' | 'accent' | 'warning' | 'danger'> = {
  ready: 'success',
  crawling: 'accent',
  pending: 'warning',
  error: 'danger',
}

const SITE_STATUS_LABEL: Record<WebSite['status'], string> = {
  ready: 'Ready',
  crawling: 'Crawling',
  pending: 'Queued',
  error: 'Error',
}

function formatDate(iso?: string) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function hostOf(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

function parsePatterns(value: string): string[] {
  return value
    .split(/[\n,]+/)
    .map((p) => p.trim())
    .filter(Boolean)
}

function AddUrls({ onAdded }: { onAdded: () => void }) {
  const toast = useToast()
  const [value, setValue] = useState('')
  const [adding, setAdding] = useState(false)
  const [crawl, setCrawl] = useState(false)
  const [maxPages, setMaxPages] = useState('200')
  const [exclude, setExclude] = useState('')

  const submit = async () => {
    // Accept multiple URLs separated by newlines, commas, or spaces.
    const urls = value
      .split(/[\n,\s]+/)
      .map((u) => u.trim())
      .filter(Boolean)
    if (urls.length === 0) return
    setAdding(true)
    try {
      if (crawl) {
        const patterns = parsePatterns(exclude)
        const max = parseInt(maxPages, 10) || undefined
        let added = 0
        const failed: string[] = []
        for (const url of urls) {
          try {
            await api.web.sites.add(url, max, patterns)
            added += 1
          } catch (e) {
            failed.push(`${url}: ${String(e)}`)
          }
        }
        if (added) toast.success(added === 1 ? 'Crawl started' : `Crawl started for ${added} sites`)
        if (failed.length) toast.error(`${failed.length} failed (${failed.join('; ')})`)
      } else {
        const res = await api.web.add(urls)
        if (res.created.length) {
          toast.success(res.created.length === 1 ? 'Page added' : `${res.created.length} pages added`)
        }
        if (res.rejected.length) {
          toast.error(
            `${res.rejected.length} skipped (${res.rejected
              .map((r) => `${r.url}: ${r.reason}`)
              .join('; ')})`
          )
        }
      }
      setValue('')
      onAdded()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setAdding(false)
    }
  }

  return (
    <section style={{ border: '1px solid var(--border)' }} className="mb-6 p-4">
      <h2 className="text-base font-semibold mb-3 flex items-center gap-2">
        <Plus className="w-4 h-4" /> Add web pages
      </h2>
      <div className="flex flex-col sm:flex-row gap-2">
        <Input
          className="flex-1"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void submit()
          }}
          placeholder={
            crawl
              ? 'https://docs.example.com — root URL to crawl'
              : 'https://example.com/docs — paste one or more URLs'
          }
        />
        <Button variant="primary" onClick={() => void submit()} loading={adding} disabled={adding || !value.trim()}>
          {crawl ? 'Crawl' : 'Add'}
        </Button>
      </div>

      <label className="flex items-center gap-2 mt-3 text-sm cursor-pointer select-none">
        <input
          type="checkbox"
          checked={crawl}
          onChange={(e) => setCrawl(e.target.checked)}
          className="accent-[var(--accent)]"
        />
        Crawl all sub-pages (index the whole site under this URL)
      </label>

      {crawl && (
        <div className="mt-3 flex flex-col sm:flex-row gap-3">
          <div className="sm:w-36">
            <label className="text-xs text-muted block mb-1">Max pages</label>
            <Input
              type="number"
              min={1}
              max={1000}
              value={maxPages}
              onChange={(e) => setMaxPages(e.target.value)}
            />
          </div>
          <div className="flex-1">
            <label className="text-xs text-muted block mb-1">
              Exclude URLs (one per line — substring or glob with *)
            </label>
            <textarea
              value={exclude}
              onChange={(e) => setExclude(e.target.value)}
              rows={2}
              placeholder={'/blog/\n*/changelog*'}
              style={{ border: '1px solid var(--border)' }}
              className="w-full p-2 text-sm font-mono bg-transparent outline-none focus:border-[var(--accent)]"
            />
          </div>
        </div>
      )}

      <p className="text-xs text-muted mt-2">
        {crawl
          ? 'The crawler follows links on the same host under the root URL (plus the sitemap) and indexes every page it finds, skipping excluded URLs.'
          : 'Each URL is fetched, its readable text extracted, chunked and embedded so your agents can search it. Paste several at once (separated by spaces or new lines).'}
      </p>
    </section>
  )
}

function SearchPanel() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<WebSearchResult[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const runSearch = async () => {
    if (!query.trim()) {
      setResults(null)
      return
    }
    setSearching(true)
    setError(null)
    try {
      const data = await api.web.search(query.trim(), 15)
      setResults(data.results)
    } catch (e) {
      setError(String(e))
    } finally {
      setSearching(false)
    }
  }

  return (
    <section style={{ border: '1px solid var(--border)' }} className="mb-6 p-4">
      <h2 className="text-base font-semibold mb-3 flex items-center gap-2">
        <SearchIcon className="w-4 h-4" /> Search web pages
      </h2>
      <div className="flex gap-2">
        <Input
          className="flex-1"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void runSearch()}
          placeholder="Ask across your scraped pages…"
        />
        {results !== null && (
          <Button
            variant="ghost"
            onClick={() => {
              setQuery('')
              setResults(null)
            }}
          >
            Clear
          </Button>
        )}
        <Button variant="secondary" onClick={() => void runSearch()} loading={searching} disabled={searching}>
          Search
        </Button>
      </div>

      {error && <p style={{ color: 'var(--danger)' }} className="text-sm mt-3">{error}</p>}

      {results !== null && (
        <div className="mt-4 space-y-2">
          {results.length === 0 ? (
            <p className="text-muted text-sm">No matching passages found.</p>
          ) : (
            results.map((r, i) => (
              <div key={`${r.page_id}-${r.chunk_index}-${i}`} style={{ border: '1px solid var(--border)' }} className="p-3">
                <div className="flex items-center justify-between gap-2 mb-1">
                  <a
                    href={r.url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-xs font-medium text-text hover:text-accent flex items-center gap-1.5 min-w-0"
                  >
                    <Globe className="w-3.5 h-3.5 flex-shrink-0" style={{ color: '#c026d3' }} />
                    <span className="truncate">{r.title || r.url}</span>
                  </a>
                  <span className="font-mono text-xs text-accent flex-shrink-0">{r.score.toFixed(3)}</span>
                </div>
                <p className="text-sm text-text whitespace-pre-wrap line-clamp-4">{r.content}</p>
              </div>
            ))
          )}
        </div>
      )}
    </section>
  )
}

function PageRow({
  page,
  onDelete,
  onRefetch,
}: {
  page: WebPage
  onDelete: (id: number) => void
  onRefetch: (id: number) => void
}) {
  const [busy, setBusy] = useState(false)
  const wrap = (fn: () => Promise<void> | void) => async () => {
    setBusy(true)
    try {
      await fn()
    } finally {
      setBusy(false)
    }
  }

  return (
    <tr style={{ borderBottom: '1px solid var(--border)' }} className="last:border-b-0 group">
      <td className="py-3 px-4 align-top">
        <div className="flex items-start gap-2 min-w-0">
          <Globe className="w-4 h-4 mt-0.5 flex-shrink-0" style={{ color: '#c026d3' }} />
          <div className="min-w-0">
            <p className="text-sm font-medium text-text truncate" title={page.title || page.url}>
              {page.title || hostOf(page.url)}
            </p>
            <a
              href={page.url}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-muted hover:text-accent truncate inline-flex items-center gap-1 max-w-full"
            >
              <span className="truncate">{page.url}</span>
              <ExternalLink className="w-3 h-3 flex-shrink-0" />
            </a>
            {page.status === 'error' && page.error_message && (
              <p className="text-xs text-danger flex items-start gap-1 mt-1">
                <AlertCircle className="w-3 h-3 mt-0.5 flex-shrink-0" />
                <span>{page.error_message}</span>
              </p>
            )}
          </div>
        </div>
      </td>
      <td className="py-3 px-4 align-top w-24 whitespace-nowrap">
        <Badge variant={STATUS_VARIANT[page.status]}>{STATUS_LABEL[page.status]}</Badge>
      </td>
      <td className="py-3 px-4 align-top w-20 text-xs text-muted whitespace-nowrap">
        {page.status === 'ready' ? `${page.total_chunks} chunks` : '—'}
      </td>
      <td className="py-3 px-4 align-top w-28 text-xs text-muted whitespace-nowrap">
        {formatDate(page.fetched_at || page.created_at)}
      </td>
      <td className="py-3 px-4 align-top w-20">
        <div className="flex items-center gap-2 opacity-100 md:opacity-0 md:group-hover:opacity-100 transition-opacity">
          <button
            onClick={wrap(() => onRefetch(page.id))}
            disabled={busy || page.status === 'processing'}
            className="text-muted hover:text-accent transition-colors disabled:opacity-50"
            title="Re-fetch"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={wrap(() => onDelete(page.id))}
            disabled={busy}
            className="text-muted hover:text-danger transition-colors disabled:opacity-50"
            title="Delete"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </td>
    </tr>
  )
}

function PageCard({
  page,
  onDelete,
  onRefetch,
}: {
  page: WebPage
  onDelete: (id: number) => void
  onRefetch: (id: number) => void
}) {
  const [busy, setBusy] = useState(false)
  const wrap = (fn: () => Promise<void> | void) => async () => {
    setBusy(true)
    try {
      await fn()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ border: '1px solid var(--border)' }} className="p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-start gap-2 min-w-0">
          <Globe className="w-4 h-4 mt-0.5 flex-shrink-0" style={{ color: '#c026d3' }} />
          <div className="min-w-0">
            <p className="text-sm font-medium text-text break-words">{page.title || hostOf(page.url)}</p>
            <a href={page.url} target="_blank" rel="noreferrer" className="text-xs text-muted hover:text-accent break-all">
              {page.url}
            </a>
          </div>
        </div>
        <Badge variant={STATUS_VARIANT[page.status]}>{STATUS_LABEL[page.status]}</Badge>
      </div>
      {page.status === 'error' && page.error_message && (
        <p className="text-xs text-danger flex items-start gap-1 mt-2">
          <AlertCircle className="w-3 h-3 mt-0.5 flex-shrink-0" />
          <span className="break-words">{page.error_message}</span>
        </p>
      )}
      <div className="flex flex-wrap gap-x-4 gap-y-1 mt-3 text-xs text-muted">
        <span>{page.status === 'ready' ? `${page.total_chunks} chunks` : '—'}</span>
        <span>{formatDate(page.fetched_at || page.created_at)}</span>
      </div>
      <div style={{ borderTop: '1px solid var(--border)' }} className="flex items-center gap-4 mt-3 pt-3">
        <button
          onClick={wrap(() => onRefetch(page.id))}
          disabled={busy || page.status === 'processing'}
          className="text-muted hover:text-accent transition-colors disabled:opacity-50 inline-flex items-center gap-1 text-xs"
        >
          <RefreshCw className="w-3.5 h-3.5" /> Re-fetch
        </button>
        <button
          onClick={wrap(() => onDelete(page.id))}
          disabled={busy}
          className="text-muted hover:text-danger transition-colors disabled:opacity-50 inline-flex items-center gap-1 text-xs"
        >
          <Trash2 className="w-3.5 h-3.5" /> Delete
        </button>
      </div>
    </div>
  )
}

function SiteCard({
  site,
  onChanged,
  onDeletePage,
  onRefetchPage,
}: {
  site: WebSite
  onChanged: () => void
  onDeletePage: (id: number) => Promise<void>
  onRefetchPage: (id: number) => Promise<void>
}) {
  const toast = useToast()
  const confirm = useConfirm()
  const [busy, setBusy] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [editing, setEditing] = useState(false)
  const [pages, setPages] = useState<WebPage[] | null>(null)
  const [patterns, setPatterns] = useState(site.exclude_patterns.join('\n'))
  const [maxPages, setMaxPages] = useState(String(site.max_pages))
  const [saving, setSaving] = useState(false)

  const crawling = site.status === 'crawling' || site.status === 'pending'

  const loadPages = useCallback(async () => {
    try {
      setPages(await api.web.sites.pages(site.id))
    } catch (e) {
      toast.error(String(e))
    }
  }, [site.id, toast])

  useEffect(() => {
    if (expanded) void loadPages()
  }, [expanded, loadPages])

  // Keep the expanded page list fresh while a crawl is running.
  useEffect(() => {
    if (!expanded || !crawling) return
    const timer = setInterval(() => void loadPages(), 3000)
    return () => clearInterval(timer)
  }, [expanded, crawling, loadPages])

  const recrawl = async () => {
    setBusy(true)
    try {
      await api.web.sites.recrawl(site.id)
      toast.success('Recrawl started')
      onChanged()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    const ok = await confirm({
      title: 'Delete site',
      message: `Delete ${site.root_url} and all its ${site.total_pages} indexed pages?`,
      confirmLabel: 'Delete',
      danger: true,
      onConfirm: () => api.web.sites.delete(site.id),
    })
    if (!ok) return
    toast.success('Site removed')
    onChanged()
  }

  const saveSettings = async (thenRecrawl: boolean) => {
    setSaving(true)
    try {
      await api.web.sites.update(site.id, {
        max_pages: parseInt(maxPages, 10) || site.max_pages,
        exclude_patterns: parsePatterns(patterns),
      })
      if (thenRecrawl) await api.web.sites.recrawl(site.id)
      setEditing(false)
      toast.success(thenRecrawl ? 'Settings saved, recrawl started' : 'Settings saved')
      onChanged()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setSaving(false)
    }
  }

  const progress = crawling
    ? `${site.pages_found || site.total_pages} pages found…`
    : `${site.ready_pages}/${site.total_pages} pages indexed · ${site.total_chunks} chunks`

  return (
    <div style={{ border: '1px solid var(--border)' }} className="mb-3">
      <div className="p-3 flex items-start justify-between gap-2 flex-wrap">
        <button
          onClick={() => setExpanded((v) => !v)}
          className="flex items-start gap-2 min-w-0 text-left flex-1"
        >
          {expanded ? (
            <ChevronDown className="w-4 h-4 mt-0.5 flex-shrink-0 text-muted" />
          ) : (
            <ChevronRight className="w-4 h-4 mt-0.5 flex-shrink-0 text-muted" />
          )}
          <Globe className="w-4 h-4 mt-0.5 flex-shrink-0" style={{ color: '#c026d3' }} />
          <div className="min-w-0">
            <p className="text-sm font-medium text-text break-all">{site.root_url}</p>
            <p className="text-xs text-muted mt-0.5">
              {progress}
              {site.crawled_at ? ` · crawled ${formatDate(site.crawled_at)}` : ''}
              {site.exclude_patterns.length > 0
                ? ` · ${site.exclude_patterns.length} exclusion${site.exclude_patterns.length === 1 ? '' : 's'}`
                : ''}
            </p>
            {site.status === 'error' && site.error_message && (
              <p className="text-xs text-danger flex items-start gap-1 mt-1">
                <AlertCircle className="w-3 h-3 mt-0.5 flex-shrink-0" />
                <span className="break-words">{site.error_message}</span>
              </p>
            )}
          </div>
        </button>
        <div className="flex items-center gap-3 flex-shrink-0">
          <Badge variant={SITE_STATUS_VARIANT[site.status]}>{SITE_STATUS_LABEL[site.status]}</Badge>
          <button
            onClick={() => setEditing((v) => !v)}
            disabled={busy}
            className="text-muted hover:text-accent transition-colors disabled:opacity-50"
            title="Crawl settings & exclusions"
          >
            <Settings2 className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => void recrawl()}
            disabled={busy || crawling}
            className="text-muted hover:text-accent transition-colors disabled:opacity-50"
            title="Re-crawl"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => void remove()}
            disabled={busy}
            className="text-muted hover:text-danger transition-colors disabled:opacity-50"
            title="Delete site"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {editing && (
        <div style={{ borderTop: '1px solid var(--border)' }} className="p-3">
          <div className="flex flex-col sm:flex-row gap-3">
            <div className="sm:w-36">
              <label className="text-xs text-muted block mb-1">Max pages</label>
              <Input
                type="number"
                min={1}
                max={1000}
                value={maxPages}
                onChange={(e) => setMaxPages(e.target.value)}
              />
            </div>
            <div className="flex-1">
              <label className="text-xs text-muted block mb-1">
                Exclude URLs (one per line — substring or glob with *)
              </label>
              <textarea
                value={patterns}
                onChange={(e) => setPatterns(e.target.value)}
                rows={3}
                placeholder={'/blog/\n*/changelog*'}
                style={{ border: '1px solid var(--border)' }}
                className="w-full p-2 text-sm font-mono bg-transparent outline-none focus:border-[var(--accent)]"
              />
            </div>
          </div>
          <div className="flex items-center gap-2 mt-2">
            <Button variant="primary" onClick={() => void saveSettings(true)} loading={saving} disabled={saving || crawling}>
              Save & re-crawl
            </Button>
            <Button variant="secondary" onClick={() => void saveSettings(false)} disabled={saving}>
              Save
            </Button>
            <Button variant="ghost" onClick={() => setEditing(false)} disabled={saving}>
              Cancel
            </Button>
          </div>
          <p className="text-xs text-muted mt-2">
            Exclusions apply on the next crawl; already-indexed pages matching them are removed then.
          </p>
        </div>
      )}

      {expanded && (
        <div style={{ borderTop: '1px solid var(--border)' }}>
          {pages === null ? (
            <p className="text-muted text-sm p-3">Loading pages…</p>
          ) : pages.length === 0 ? (
            <p className="text-muted text-sm p-3">No pages discovered yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[560px]">
                <tbody>
                  {pages.map((page) => (
                    <PageRow
                      key={page.id}
                      page={page}
                      onDelete={(id) => {
                        void onDeletePage(id).then(() => loadPages())
                      }}
                      onRefetch={(id) => {
                        void onRefetchPage(id).then(() => loadPages())
                      }}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function WebPages() {
  const toast = useToast()
  const [pages, setPages] = useState<WebPage[]>([])
  const [sites, setSites] = useState<WebSite[]>([])
  const [loading, setLoading] = useState(true)
  const [pageError, setPageError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const [pageData, siteData] = await Promise.all([api.web.list(), api.web.sites.list()])
      setPages(pageData)
      setSites(siteData)
      setPageError(null)
    } catch (e) {
      setPageError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // Poll while anything is still being fetched or crawled.
  const hasPending = useMemo(
    () =>
      pages.some((p) => p.status === 'pending' || p.status === 'processing') ||
      sites.some((s) => s.status === 'pending' || s.status === 'crawling'),
    [pages, sites]
  )
  useEffect(() => {
    if (!hasPending) return
    const timer = setInterval(() => void load(), 2500)
    return () => clearInterval(timer)
  }, [hasPending, load])

  const handleDelete = async (id: number) => {
    try {
      await api.web.delete(id)
      setPages((prev) => prev.filter((p) => p.id !== id))
      toast.success('Page removed')
    } catch (e) {
      toast.error(String(e))
    }
  }

  const handleRefetch = async (id: number) => {
    try {
      await api.web.refetch(id)
      toast.success('Page refetched')
      await load()
    } catch (e) {
      toast.error(String(e))
    }
  }

  const standalonePages = useMemo(() => pages.filter((p) => p.site_id == null), [pages])
  const readyCount = pages.filter((p) => p.status === 'ready').length

  return (
    <div className="p-4 sm:p-8">
      <div className="page-wide">
        <div className="mb-6">
          <h1>Web Pages</h1>
          <p className="text-muted text-sm">
            {pages.length === 0 && sites.length === 0
              ? 'Add website URLs to scrape and make them searchable by your agents'
              : [
                  sites.length > 0 ? `${sites.length} site${sites.length === 1 ? '' : 's'}` : null,
                  `${pages.length} page${pages.length === 1 ? '' : 's'}`,
                  `${readyCount} ready`,
                ]
                  .filter(Boolean)
                  .join(' · ')}
          </p>
        </div>

        {pageError && (
          <div
            style={{ border: '1px solid var(--danger)', color: 'var(--danger)' }}
            className="text-sm p-3 mb-4 bg-[#fef2f2]"
          >
            {pageError}
          </div>
        )}
        <AddUrls onAdded={() => void load()} />

        <SearchPanel />

        {loading ? (
          <p className="text-muted text-sm">Loading…</p>
        ) : (
          <>
            {sites.length > 0 && (
              <section className="mb-6">
                <h2 className="text-base font-semibold mb-3">Crawled sites</h2>
                {sites.map((site) => (
                  <SiteCard
                    key={site.id}
                    site={site}
                    onChanged={() => void load()}
                    onDeletePage={handleDelete}
                    onRefetchPage={handleRefetch}
                  />
                ))}
              </section>
            )}

            {pages.length === 0 && sites.length === 0 ? (
              <p className="text-muted text-sm">No pages yet. Add your first URL above.</p>
            ) : standalonePages.length > 0 ? (
              <section>
                {sites.length > 0 && <h2 className="text-base font-semibold mb-3">Single pages</h2>}

                {/* Mobile: stacked cards */}
                <div className="space-y-3 md:hidden">
                  {standalonePages.map((page) => (
                    <PageCard key={page.id} page={page} onDelete={handleDelete} onRefetch={handleRefetch} />
                  ))}
                </div>

                {/* Desktop: table */}
                <div style={{ border: '1px solid var(--border)' }} className="hidden md:block overflow-x-auto">
                  <table className="w-full min-w-[640px]">
                    <thead>
                      <tr style={{ borderBottom: '1px solid var(--border)' }} className="text-left">
                        <th className="py-2 px-4 text-xs font-medium text-muted">Page</th>
                        <th className="py-2 px-4 text-xs font-medium text-muted">Status</th>
                        <th className="py-2 px-4 text-xs font-medium text-muted">Chunks</th>
                        <th className="py-2 px-4 text-xs font-medium text-muted">Fetched</th>
                        <th className="py-2 px-4" />
                      </tr>
                    </thead>
                    <tbody>
                      {standalonePages.map((page) => (
                        <PageRow key={page.id} page={page} onDelete={handleDelete} onRefetch={handleRefetch} />
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            ) : null}
          </>
        )}
      </div>
    </div>
  )
}
