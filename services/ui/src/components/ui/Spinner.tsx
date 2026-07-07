// Monochrome "pixel-scan" loader (see .cf-spinner in index.css): nine cells,
// lit one at a time around the ring with a stepped, digital cadence. Inherits
// currentColor, so wrap it in a text-* class to tint it.
export function Spinner({ size = 14, className = '' }: { size?: number; className?: string }) {
  return (
    <span
      role="status"
      aria-label="Loading"
      className={`cf-spinner ${className}`}
      style={{ width: size, height: size }}
    >
      {Array.from({ length: 9 }, (_, i) => (
        <span key={i} />
      ))}
    </span>
  )
}
