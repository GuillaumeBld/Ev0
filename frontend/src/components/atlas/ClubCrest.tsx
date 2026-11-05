'use client'

import { useEffect, useMemo, useState } from 'react'

/**
 * Club/team crest with the same resolution order used everywhere else in
 * the app (see `PlayerTeamCrest` in `app/dashboard/players/page.tsx`):
 * `crest_url` (footylogos, already verified 200 server-side) → `api_football_id`
 * (media.api-sports.io) → initials placeholder. Never a guessed URL.
 */
export function ClubCrest({
  crestUrl,
  apiFootballId,
  name,
  size = 36,
}: {
  crestUrl: string | null | undefined
  apiFootballId: number | null | undefined
  name: string
  size?: number
}) {
  const sources = useMemo(() => {
    const list: string[] = []
    if (crestUrl) list.push(crestUrl)
    if (apiFootballId) list.push(`https://media.api-sports.io/football/teams/${apiFootballId}.png`)
    return list
  }, [crestUrl, apiFootballId])

  const [idx, setIdx] = useState(0)
  useEffect(() => setIdx(0), [crestUrl, apiFootballId])

  const src = sources[idx]
  if (!src) {
    const initials = name
      .split(/[\s-]+/)
      .filter(Boolean)
      .map((w) => w[0])
      .join('')
      .toUpperCase()
      .slice(0, 2)
    return (
      <div
        style={{
          width: size,
          height: size,
          borderRadius: '50%',
          background: 'var(--bd2)',
          color: 'var(--t2)',
          display: 'grid',
          placeItems: 'center',
          fontSize: Math.max(9, size * 0.32),
          fontWeight: 800,
          flex: 'none',
        }}
      >
        {initials || '?'}
      </div>
    )
  }
  return (
    <img
      src={src}
      alt=""
      width={size}
      height={size}
      style={{ objectFit: 'contain', flex: 'none' }}
      onError={() => setIdx((i) => i + 1)}
    />
  )
}
