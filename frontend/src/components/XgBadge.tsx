'use client'

interface XgBadgeProps {
  source: 'bzzoiro' | 'model' | undefined | null
  className?: string
}

export function XgBadge({ source, className }: XgBadgeProps) {
  if (!source) return null
  const isApi = source === 'bzzoiro'
  return (
    <span
      className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-semibold ${className ?? ''}`}
      style={{
        backgroundColor: isApi ? '#3B82F6' : '#F97316',
        color: 'white',
      }}
    >
      {isApi ? 'API' : 'MODEL'}
    </span>
  )
}
