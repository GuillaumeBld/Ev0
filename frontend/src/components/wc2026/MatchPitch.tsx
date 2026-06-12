'use client'

import { clsx } from 'clsx'
import { type WCTeamLineup, type WCMatchLineupPlayer } from '@/lib/api'

function parseFormation(formation: string | null): number[] {
  if (!formation) return [4, 3, 3]
  const parts = formation.split('-').map(Number).filter(n => !isNaN(n) && n > 0)
  return parts.length >= 2 ? parts : [4, 3, 3]
}

// Sort players G→D→M→F and distribute across formation lines
function assignToLines(
  players: WCMatchLineupPlayer[],
  lines: number[],
): WCMatchLineupPlayer[][] {
  const posOrder: Record<string, number> = { G: 0, D: 1, M: 2, F: 3 }
  const sorted = [...players].sort(
    (a, b) => (posOrder[a.position] ?? 2) - (posOrder[b.position] ?? 2),
  )
  const gks = sorted.filter(p => p.position === 'G')
  const outfield = sorted.filter(p => p.position !== 'G')

  const result: WCMatchLineupPlayer[][] = [gks] // index 0 = GK row
  let cursor = 0
  for (const count of lines) {
    result.push(outfield.slice(cursor, cursor + count))
    cursor += count
  }
  return result
}

function PlayerChip({
  player,
  isHome,
}: {
  player: WCMatchLineupPlayer
  isHome: boolean
}) {
  const lastName = (player.short_name ?? player.name).split(' ').pop() ?? player.name

  return (
    <div className="flex flex-col items-center" style={{ width: 56 }}>
      <div
        className={clsx(
          'w-11 h-11 rounded-full border-2 flex items-center justify-center',
          isHome
            ? 'border-blue-400 bg-blue-900/50'
            : 'border-orange-400 bg-orange-900/50',
        )}
      >
        <span className="font-bold text-white text-sm leading-none">
          {player.jersey_number ?? '?'}
        </span>
      </div>
      <span
        className={clsx(
          'mt-1 text-[10px] text-center leading-tight w-14 truncate',
          isHome ? 'text-blue-200' : 'text-orange-200',
        )}
      >
        {lastName}
      </span>
    </div>
  )
}

interface MatchPitchProps {
  homeLineup: WCTeamLineup
  awayLineup: WCTeamLineup
  homeTeam: string
  awayTeam: string
}

export function MatchPitch({ homeLineup, awayLineup, homeTeam, awayTeam }: MatchPitchProps) {
  // lines[0] = GK, lines[1..N] = defense→attack
  const homeLines = assignToLines(homeLineup.players, parseFormation(homeLineup.formation))
  const awayLines = assignToLines(awayLineup.players, parseFormation(awayLineup.formation))

  // Away: render top→bottom as GK, def, mid, fwd
  const awayRows = awayLines // [GK, def, mid, fwd]
  // Home: render top→bottom as fwd, mid, def, GK (mirrored)
  const homeRows = [...homeLines.slice(1).reverse(), homeLines[0]]

  return (
    <div className="space-y-4">
      {/* Team name headers */}
      <div className="flex justify-between items-center">
        <div className="flex items-center gap-2">
          <span className="w-3 h-3 rounded-full bg-blue-400 inline-block" />
          <span className="font-semibold text-blue-300 text-sm">{homeTeam}</span>
          {homeLineup.formation && (
            <span className="text-xs text-gray-500">{homeLineup.formation}</span>
          )}
        </div>
        <div className="flex items-center gap-2 flex-row-reverse">
          <span className="w-3 h-3 rounded-full bg-orange-400 inline-block" />
          <span className="font-semibold text-orange-300 text-sm">{awayTeam}</span>
          {awayLineup.formation && (
            <span className="text-xs text-gray-500">{awayLineup.formation}</span>
          )}
        </div>
      </div>

      {/* Pitch */}
      <div className="relative bg-[#1a3a1a] border border-green-800/50 rounded-xl overflow-hidden">
        {/* Goal top */}
        <div className="flex justify-center pt-2 pb-1">
          <div className="w-24 h-3 border-b-2 border-l-2 border-r-2 border-green-700/60 rounded-b" />
        </div>

        {/* Away half */}
        <div className="px-6 pt-1 pb-4 space-y-3">
          {awayRows.map((row, i) => (
            <div key={i} className="flex justify-center gap-2 flex-wrap">
              {row.map(p => (
                <PlayerChip key={p.id} player={p} isHome={false} />
              ))}
            </div>
          ))}
        </div>

        {/* Center line with circle */}
        <div className="relative h-10 flex items-center justify-center">
          <div className="absolute left-0 right-0 border-t border-green-600/40" />
          <div className="relative z-10 w-10 h-10 rounded-full border border-green-600/40 bg-[#1a3a1a]" />
          <div className="absolute w-2 h-2 rounded-full bg-green-700/60" />
        </div>

        {/* Home half */}
        <div className="px-6 pt-4 pb-1 space-y-3">
          {homeRows.map((row, i) => (
            <div key={i} className="flex justify-center gap-2 flex-wrap">
              {row.map(p => (
                <PlayerChip key={p.id} player={p} isHome={true} />
              ))}
            </div>
          ))}
        </div>

        {/* Goal bottom */}
        <div className="flex justify-center pb-2 pt-1">
          <div className="w-24 h-3 border-t-2 border-l-2 border-r-2 border-green-700/60 rounded-t" />
        </div>
      </div>

      {/* Bench — two columns */}
      <div className="grid grid-cols-2 gap-4">
        <div>
          <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-2">
            Remplaçants — {homeTeam}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {homeLineup.substitutes.map(p => (
              <PlayerChip key={p.id} player={p} isHome={true} />
            ))}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-2 text-right">
            Remplaçants — {awayTeam}
          </div>
          <div className="flex flex-wrap gap-1.5 justify-end">
            {awayLineup.substitutes.map(p => (
              <PlayerChip key={p.id} player={p} isHome={false} />
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
