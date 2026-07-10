// Brand mark "Confluenza": three streams converging into an azure node.
// Strokes inherit currentColor so the mark follows the surrounding text color.
export function LogoMark({ size = 20 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 48 48"
      fill="none"
      aria-hidden="true"
      className="flex-shrink-0"
    >
      <path d="M4 10 C20 10 22 24 32 24" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" />
      <path d="M4 24 L32 24" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" />
      <path d="M4 38 C20 38 22 24 32 24" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" />
      <circle cx="37.5" cy="24" r="5.5" fill="#7CC9F2" />
    </svg>
  )
}

// Full lockup: mark + "ContextForge" wordmark in Space Grotesk (Context 500 / Forge 700).
export function Logo({ size = 20 }: { size?: number }) {
  return (
    <span className="inline-flex items-center gap-2 text-text">
      <LogoMark size={size} />
      <span
        style={{
          fontFamily: "'Space Grotesk Variable', 'Space Grotesk', sans-serif",
          fontSize: size * 0.72,
          letterSpacing: '-0.02em',
          lineHeight: 1,
        }}
      >
        <span style={{ fontWeight: 500 }}>Context</span>
        <span style={{ fontWeight: 700 }}>Forge</span>
      </span>
    </span>
  )
}
