import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  UploadCloud,
  FileText,
  FileSpreadsheet,
  FileImage,
  File as FileIcon,
  Trash2,
  RefreshCw,
  Download,
  Search as SearchIcon,
  AlertCircle,
} from 'lucide-react'
import { api, type KbDocument, type KbSearchResult } from '../lib/api'
import { Button, Input, Badge } from '../components/ui'

const STATUS_VARIANT: Record<KbDocument['status'], 'success' | 'accent' | 'warning' | 'danger'> = {
  ready: 'success',
  processing: 'accent',
  pending: 'warning',
  error: 'danger',
}

const STATUS_LABEL: Record<KbDocument['status'], string> = {
  ready: 'Ready',
  processing: 'Processing',
  pending: 'Queued',
  error: 'Error',
}

function DocIcon({ ext }: { ext?: string }) {
  const e = (ext || '').toLowerCase()
  if (['.xlsx', '.xls', '.xlsm', '.csv', '.tsv'].includes(e))
    return <FileSpreadsheet className="w-4 h-4 text-[#1a7a45]" />
  if (['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tif', '.tiff', '.webp'].includes(e))
    return <FileImage className="w-4 h-4 text-accent" />
  if (['.pdf', '.docx', '.doc', '.pptx', '.rtf', '.txt', '.md'].includes(e))
    return <FileText className="w-4 h-4 text-muted" />
  return <FileIcon className="w-4 h-4 text-muted" />
}

function formatBytes(bytes: number) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)))
  return `${(bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`
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

function DropZone({
  onFiles,
  uploading,
  accept,
}: {
  onFiles: (files: File[]) => void
  uploading: boolean
  accept: string
}) {
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const files = Array.from(e.dataTransfer.files)
    if (files.length) onFiles(files)
  }

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault()
        setDragging(true)
      }}
      onDragLeave={(e) => {
        e.preventDefault()
        setDragging(false)
      }}
      onDrop={handleDrop}
      onClick={() => inputRef.current?.click()}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click()
      }}
      className={[
        'flex flex-col items-center justify-center gap-2 p-8 text-center cursor-pointer rounded transition-colors',
        'border-2 border-dashed',
        dragging ? 'border-accent bg-[#eaf4fb]' : 'border-border bg-surface hover:border-accent',
      ].join(' ')}
      style={{ outline: 'none' }}
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={accept || undefined}
        className="hidden"
        onChange={(e) => {
          const files = Array.from(e.target.files || [])
          if (files.length) onFiles(files)
          e.target.value = ''
        }}
      />
      <UploadCloud className={`w-8 h-8 ${dragging ? 'text-accent' : 'text-muted'}`} />
      <div className="text-sm">
        <span className="font-medium text-text">
          {uploading ? 'Uploading…' : 'Drop files here'}
        </span>
        <span className="text-muted"> or click to browse</span>
      </div>
      <p className="text-xs text-muted">
        PDF, Word, Excel, PowerPoint, images (OCR), text & more — up to 100 MB each
      </p>
    </div>
  )
}

function SearchPanel() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<KbSearchResult[] | null>(null)
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
      const data = await api.kb.search(query.trim(), 15)
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
        <SearchIcon className="w-4 h-4" /> Search knowledge base
      </h2>
      <div className="flex gap-2">
        <Input
          className="flex-1"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void runSearch()}
          placeholder="Ask across your uploaded documents…"
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
              <div key={`${r.document_id}-${r.chunk_index}-${i}`} style={{ border: '1px solid var(--border)' }} className="p-3">
                <div className="flex items-center justify-between gap-2 mb-1">
                  <span className="text-xs font-medium text-text flex items-center gap-1.5 min-w-0">
                    <DocIcon ext={r.extension} />
                    <span className="truncate">{r.title || r.filename}</span>
                  </span>
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

function DocumentRow({
  doc,
  onDelete,
  onReprocess,
  onDownload,
}: {
  doc: KbDocument
  onDelete: (id: number) => void
  onReprocess: (id: number) => void
  onDownload: (doc: KbDocument) => void
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
        <div className="flex items-start gap-2">
          <div className="mt-0.5">
            <DocIcon ext={doc.extension} />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-medium text-text truncate" title={doc.filename}>
              {doc.title || doc.filename}
            </p>
            <p className="text-xs text-muted truncate">{doc.filename}</p>
            {doc.status === 'error' && doc.error_message && (
              <p className="text-xs text-danger flex items-start gap-1 mt-1">
                <AlertCircle className="w-3 h-3 mt-0.5 flex-shrink-0" />
                <span>{doc.error_message}</span>
              </p>
            )}
          </div>
        </div>
      </td>
      <td className="py-3 px-4 align-top w-24 whitespace-nowrap">
        <Badge variant={STATUS_VARIANT[doc.status]}>{STATUS_LABEL[doc.status]}</Badge>
      </td>
      <td className="py-3 px-4 align-top w-20 text-xs text-muted whitespace-nowrap">
        {doc.status === 'ready' ? `${doc.total_chunks} chunks` : '—'}
      </td>
      <td className="py-3 px-4 align-top w-20 text-xs text-muted whitespace-nowrap">
        {formatBytes(doc.size_bytes)}
      </td>
      <td className="py-3 px-4 align-top w-28 text-xs text-muted whitespace-nowrap">
        {formatDate(doc.uploaded_at)}
      </td>
      <td className="py-3 px-4 align-top w-24">
        <div className="flex items-center gap-2 opacity-100 md:opacity-0 md:group-hover:opacity-100 transition-opacity">
          <button
            onClick={wrap(() => onDownload(doc))}
            disabled={busy}
            className="text-muted hover:text-accent transition-colors disabled:opacity-50"
            title="Download original"
          >
            <Download className="w-3.5 h-3.5" />
          </button>
          {doc.status === 'error' && (
            <button
              onClick={wrap(() => onReprocess(doc.id))}
              disabled={busy}
              className="text-muted hover:text-accent transition-colors disabled:opacity-50"
              title="Retry processing"
            >
              <RefreshCw className="w-3.5 h-3.5" />
            </button>
          )}
          <button
            onClick={wrap(() => onDelete(doc.id))}
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

export default function Knowledge() {
  const [documents, setDocuments] = useState<KbDocument[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [pageError, setPageError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [accept, setAccept] = useState('')

  const load = useCallback(async () => {
    try {
      const data = await api.kb.list()
      setDocuments(data)
      setPageError(null)
    } catch (e) {
      setPageError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
    api.kb
      .formats()
      .then((f) => setAccept(f.extensions.join(',')))
      .catch(() => {})
  }, [load])

  // Poll while any document is still being processed.
  const hasPending = useMemo(
    () => documents.some((d) => d.status === 'pending' || d.status === 'processing'),
    [documents]
  )
  useEffect(() => {
    if (!hasPending) return
    const timer = setInterval(() => void load(), 2500)
    return () => clearInterval(timer)
  }, [hasPending, load])

  const handleFiles = async (files: File[]) => {
    setUploading(true)
    setPageError(null)
    setNotice(null)
    try {
      const res = await api.kb.upload(files)
      const parts: string[] = []
      if (res.created.length) parts.push(`${res.created.length} uploaded`)
      if (res.rejected.length) {
        parts.push(
          `${res.rejected.length} skipped (${res.rejected
            .map((r) => `${r.filename}: ${r.reason}`)
            .join('; ')})`
        )
      }
      setNotice(parts.join(' · ') || 'Upload complete')
      await load()
    } catch (e) {
      setPageError(String(e))
    } finally {
      setUploading(false)
    }
  }

  const handleDelete = async (id: number) => {
    try {
      await api.kb.delete(id)
      setDocuments((prev) => prev.filter((d) => d.id !== id))
    } catch (e) {
      setPageError(String(e))
    }
  }

  const handleReprocess = async (id: number) => {
    try {
      await api.kb.reprocess(id)
      await load()
    } catch (e) {
      setPageError(String(e))
    }
  }

  const handleDownload = async (doc: KbDocument) => {
    try {
      const blob = await api.kb.download(doc.id)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = doc.filename
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (e) {
      setPageError(String(e))
    }
  }

  const readyCount = documents.filter((d) => d.status === 'ready').length

  return (
    <div className="p-4 sm:p-8">
      <div className="page-content">
        <div className="mb-6">
          <h1>Knowledge Base</h1>
          <p className="text-muted text-sm">
            {documents.length === 0
              ? 'Upload documents to make them searchable by your agents'
              : `${documents.length} document${documents.length === 1 ? '' : 's'} · ${readyCount} ready`}
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
        {notice && (
          <div
            style={{ border: '1px solid var(--border)' }}
            className="text-sm p-3 mb-4 text-muted bg-surface"
          >
            {notice}
          </div>
        )}

        <div className="mb-6">
          <DropZone onFiles={handleFiles} uploading={uploading} accept={accept} />
        </div>

        <SearchPanel />

        {loading ? (
          <p className="text-muted text-sm">Loading…</p>
        ) : documents.length === 0 ? (
          <p className="text-muted text-sm">No documents yet. Upload your first file above.</p>
        ) : (
          <div style={{ border: '1px solid var(--border)' }}>
            <table className="w-full">
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)' }} className="text-left">
                  <th className="py-2 px-4 text-xs font-medium text-muted">Document</th>
                  <th className="py-2 px-4 text-xs font-medium text-muted">Status</th>
                  <th className="py-2 px-4 text-xs font-medium text-muted">Chunks</th>
                  <th className="py-2 px-4 text-xs font-medium text-muted">Size</th>
                  <th className="py-2 px-4 text-xs font-medium text-muted">Uploaded</th>
                  <th className="py-2 px-4" />
                </tr>
              </thead>
              <tbody>
                {documents.map((doc) => (
                  <DocumentRow
                    key={doc.id}
                    doc={doc}
                    onDelete={handleDelete}
                    onReprocess={handleReprocess}
                    onDownload={handleDownload}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
