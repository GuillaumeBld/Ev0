'use client'

import { clsx } from 'clsx'
import { XgBadge } from './XgBadge'

interface XgSourceBadgeProps {
  source: string | null | undefined
}

export function XgSourceBadge({ source }: XgSourceBadgeProps) {
  if (source === 'market_implied') {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-emerald-500/[0.08] text-emerald-400 border border-emerald-500/20">
        LIVE
      </span>
    )
  }
  if (source === 'market_implied_flagged') {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-amber-500/[0.08] text-amber-400 border border-amber-500/20">
        LIVE ⚠
      </span>
    )
  }
  if (source === 'bzzoiro') {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-blue-500/[0.08] text-blue-400 border border-blue-500/20">
        BZZOIRO
      </span>
    )
  }
  if (source === 'model') {
    return <XgBadge source="model" />
  }
  if (!source) {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-gray-500/[0.08] text-gray-400 border border-gray-500/20">
        xG · indisponible
      </span>
    )
  }
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-gray-500/[0.08] text-gray-400 border border-gray-500/20">
      xG · {source}
    </span>
  )
}
