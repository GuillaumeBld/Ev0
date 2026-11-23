'use client'

import { useEffect, useMemo, useState } from 'react'
import type { TeamBadgeOut } from '@/lib/api'
import { ClubCrest } from '@/components/atlas/ClubCrest'

/**
 * Écusson d'une équipe, source unique = badge Atlas renvoyé par l'API.
 * Ordre : crest_url -> api_football_id (via ClubCrest) -> drapeau flagcdn pour
 * une sélection (country_iso sans écusson) -> initiales. Jamais d'image cassée.
 */
export function TeamBadge({
  badge,
  size = 28,
  name,
}: {
  badge: TeamBadgeOut | null | undefined
  size?: number
  /** Nom affiché de l'équipe, utilisé seulement pour les initiales quand le badge est absent. */
  name?: string
}) {
  const label = badge?.name_fr ?? name ?? ''
  const hasClubCrest = !!(badge?.crest_url || badge?.api_football_id)
  const iso = badge?.country_iso ? badge.country_iso.toLowerCase() : null
  const isNational = !hasClubCrest && !!iso

  const [flagFailed, setFlagFailed] = useState(false)
  useEffect(() => setFlagFailed(false), [iso])

  const flagSrc = useMemo(() => (iso ? `https://flagcdn.com/w40/${iso}.png` : null), [iso])

  if (isNational && flagSrc && !flagFailed) {
    return (
      <img
        src={flagSrc}
        alt=""
        width={size}
        height={size}
        style={{ objectFit: 'contain', flex: 'none' }}
        onError={() => setFlagFailed(true)}
      />
    )
  }
  return (
    <ClubCrest
      crestUrl={badge?.crest_url}
      apiFootballId={badge?.api_football_id}
      name={label}
      size={size}
    />
  )
}
