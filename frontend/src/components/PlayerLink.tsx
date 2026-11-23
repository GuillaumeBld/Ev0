'use client'

import Link from 'next/link'

/**
 * Nom de joueur cliquable vers sa fiche Atlas (`/dashboard/atlas?player=<id>`).
 * Sans identifiant Atlas, le nom reste un simple texte : jamais de lien inventé.
 */
export function PlayerLink({
  playerId,
  name,
  className,
}: {
  playerId: number | null | undefined
  name: string
  className?: string
}) {
  if (playerId == null) return <>{name}</>
  return (
    <Link
      href={`/dashboard/atlas?player=${playerId}`}
      onClick={(e) => e.stopPropagation()}
      className={className ?? 'hover:underline'}
    >
      {name}
    </Link>
  )
}
