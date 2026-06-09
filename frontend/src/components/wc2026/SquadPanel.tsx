// frontend/src/components/wc2026/SquadPanel.tsx
'use client'

import { clsx } from 'clsx'
import { type WCSquadPlayer } from '@/lib/api'

interface SquadPanelProps {
  squad: WCSquadPlayer[]
  usedNames: Set<string>
  onPlayerClick: (player: WCSquadPlayer) => void
}

const POS_ORDER = ['GK', 'DEF', 'MID', 'FWD'] as const
const POS_COLOR: Record<string, string> = {
  GK:  'text-yellow-400',
  DEF: 'text-blue-400',
  MID: 'text-green-400',
  FWD: 'text-orange-400',
}

export function SquadPanel({ squad, usedNames, onPlayerClick }: SquadPanelProps) {
  const available = squad.filter((p) => !usedNames.has(p.player_name))

  const byPos: Record<string, WCSquadPlayer[]> = { GK: [], DEF: [], MID: [], FWD: [] }
  for (const p of available) {
    const key = p.position in byPos ? p.position : 'MID'
    byPos[key].push(p)
  }

  return (
    <div className="flex flex-col h-full overflow-y-auto pr-1">
      <p className="text-xs text-gray-500 mb-2 font-medium uppercase tracking-wider">
        Effectif disponible
      </p>
      {POS_ORDER.map((pos) => {
        const players = byPos[pos]
        if (players.length === 0) return null
        return (
          <div key={pos} className="mb-3">
            <div className={clsx('text-[10px] font-bold uppercase mb-1', POS_COLOR[pos])}>
              {pos}
            </div>
            {players.map((p) => (
              <button
                key={p.player_name}
                onClick={() => onPlayerClick(p)}
                className="w-full text-left px-2 py-1 text-xs rounded hover:bg-gray-700 text-gray-300 hover:text-white transition-colors truncate"
              >
                {p.shirt_number != null && (
                  <span className="text-gray-500 mr-1 text-[10px]">#{p.shirt_number}</span>
                )}
                {p.player_name}
              </button>
            ))}
          </div>
        )
      })}
      {available.length === 0 && (
        <p className="text-xs text-gray-600 italic">Tous les joueurs sont placés</p>
      )}
    </div>
  )
}
