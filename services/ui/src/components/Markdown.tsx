import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Components } from 'react-markdown'

// Renders assistant messages as markdown. Styling is kept compact so it reads
// well inside a chat bubble (tighter margins than the global prose rules) and
// stays legible on small mobile screens.
const components: Components = {
  p: ({ children }) => <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>,
  h1: ({ children }) => <h1 className="text-base font-semibold mt-3 mb-1.5 first:mt-0">{children}</h1>,
  h2: ({ children }) => <h2 className="text-base font-semibold mt-3 mb-1.5 first:mt-0">{children}</h2>,
  h3: ({ children }) => <h3 className="text-sm font-semibold mt-3 mb-1.5 first:mt-0">{children}</h3>,
  h4: ({ children }) => <h4 className="text-sm font-semibold mt-3 mb-1.5 first:mt-0">{children}</h4>,
  ul: ({ children }) => <ul className="list-disc pl-5 mb-2 last:mb-0 space-y-0.5">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal pl-5 mb-2 last:mb-0 space-y-0.5">{children}</ol>,
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-accent underline break-words"
    >
      {children}
    </a>
  ),
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  blockquote: ({ children }) => (
    <blockquote
      className="border-l-2 pl-3 my-2 text-muted"
      style={{ borderColor: 'var(--border)' }}
    >
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-3" style={{ borderColor: 'var(--border)' }} />,
  code: ({ className, children }) => {
    const isBlock = /language-/.test(className ?? '')
    if (isBlock) {
      return <code className={className}>{children}</code>
    }
    return (
      <code
        className="px-1 py-0.5 rounded text-[0.85em] break-words"
        style={{ background: 'var(--code-bg)', border: '1px solid var(--border)' }}
      >
        {children}
      </code>
    )
  },
  pre: ({ children }) => (
    <pre
      className="my-2 p-3 rounded overflow-x-auto text-xs"
      style={{ background: 'var(--code-bg)', border: '1px solid var(--border)' }}
    >
      {children}
    </pre>
  ),
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto">
      <table className="text-xs border-collapse">{children}</table>
    </div>
  ),
}

export function Markdown({ content }: { content: string }) {
  return (
    <div className="text-sm break-words">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  )
}
