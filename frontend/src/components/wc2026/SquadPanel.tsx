// frontend/src/components/wc2026/SquadPanel.tsx
'use client'

import { clsx } from 'clsx'
import { type WCSquadPlayer } from '@/lib/api'

interface SquadPanelProps {
  squad: WCSquadPlayer[]
  usedNamesNorm: Set<string>
  selectedPlayerName: string | null
  onPlayerClick: (player: WCSquadPlayer) => void
  onAddAsSub: (player: WCSquadPlayer) => void
}

const POS_ORDER = ['GK', 'DEF', 'MID', 'FWD'] as const
const POS_COLOR: Record<string, string> = {
  GK:  'text-yellow-400',
  DEF: 'text-blue-400',
  MID: 'text-green-400',
  FWD: 'text-orange-400',
}

const normName = (n: string) =>
  n.normalize('NFKD').replace(/\p{Mn}/gu, '').toLowerCase().trim()

export function SquadPanel({
  squad, usedNamesNorm, selectedPlayerName, onPlayerClick, onAddAsSub,
}: SquadPanelProps) {
  const available = squad.filter((p) => !usedNamesNorm.has(normName(p.player_name)))

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
            {players.map((p) => {
              const isSelected = p.player_name === selectedPlayerName
              return (
                <div
                  key={p.player_name}
                  className={clsx(
                    'group flex items-center rounded transition-colors',
                    isSelected ? 'bg-orange-500/20' : 'hover:bg-gray-700/50',
                  )}
                >
                  <button
                    onClick={() => onPlayerClick(p)}
                    className={clsx(
                      'flex-1 text-left px-2 py-1 text-xs truncate transition-colors',
                      isSelected ? 'text-orange-300 font-medium' : 'text-gray-300 group-hover:text-white',
                    )}
                  >
                    {p.shirt_number != null && (
                      <span className="text-gray-500 mr-1 text-[10px]">#{p.shirt_number}</span>
                    )}
                    {p.player_name}
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); onAddAsSub(p) }}
                    className="opacity-0 group-hover:opacity-100 px-1.5 py-1 text-gray-500 hover:text-orange-400 transition-opacity shrink-0"
                    title="Ajouter en remplaçant"
                  >
                    <svg className="w-3 h-3" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
                      <path d="M6 1v8M3 6l3 3 3-3" strokeLinecap="round" strokeLinejoin="round"/>
                    </svg>
                  </button>
                </div>
              )
            })}
          </div>
        )
      })}
      {available.length === 0 && (
        <p className="text-xs text-gray-600 italic">Tous les joueurs sont placés</p>
      )}
    </div>
  )
}
