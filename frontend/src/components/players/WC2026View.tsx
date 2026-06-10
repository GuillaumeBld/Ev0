'use client'

import { useMemo } from 'react'
import { clsx } from 'clsx'
import { WCNation, WCSquad, WCPlayer } from '@/lib/api'
import { FlagImg } from '@/components/FlagImg'

interface Props {
  nations: WCNation[]
  selectedNation: string | null
  squad: WCSquad | null
  loading: boolean
  onSelectNation: (nation: string) => void
  onSwitchToTable?: () => void
}

function fmt(v: number | null, decimals = 1): string | null {
  return v == null ? null : v.toFixed(decimals)
}

function PlayerCard({ player }: { player: WCPlayer }) {
  const isGK = player.position === 'GK'
  const hasStats = player.matches_played != null
  const hasScouting = player.sc_rating != null

  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg p-3 flex flex-col gap-1">
      <div className="flex items-center gap-2">
        {player.player_image && (
          <img
            src={player.player_image}
            alt=""
            className="w-8 h-8 rounded-full object-cover shrink-0 bg-gray-700"
            onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
          />
        )}
        <div className="min-w-0">
          <div className="text-white text-sm font-medium leading-tight truncate">{player.player_name}</div>
          <div className="text-gray-400 text-xs truncate">{player.detailed_position ?? player.club ?? '—'}</div>
        </div>
      </div>

      {hasStats && isGK && (
        <>
          <div className="text-gray-300 text-xs mt-1">
            {player.saves != null && <span>🧤 {player.saves} parades</span>}
          </div>
          <div className="text-gray-500 text-xs">
            {player.matches_played} matchs
            {player.avg_rating != null && <span> · ★ {fmt(player.avg_rating)}</span>}
          </div>
        </>
      )}

      {hasStats && !isGK && (
        <>
          <div className="text-gray-300 text-xs mt-1 flex gap-2 flex-wrap">
            {player.goals != null && <span>⚽ {player.goals}</span>}
            {player.assists != null && <span>🅰 {player.assists}</span>}
            {player.xg != null && <span>xG {fmt(player.xg)}</span>}
            {player.xa != null && <span>xA {fmt(player.xa)}</span>}
          </div>
          <div className="text-gray-500 text-xs flex gap-1 flex-wrap">
            <span>{player.matches_played}m</span>
            {player.xg_per90 != null && <span>· xG/90 {fmt(player.xg_per90, 2)}</span>}
            {player.avg_rating != null && <span>· ★ {fmt(player.avg_rating)}</span>}
          </div>
        </>
      )}

      {hasScouting && !isGK && (
        <div className="text-gray-500 text-xs flex gap-2 flex-wrap border-t border-gray-700/50 pt-1 mt-0.5">
          {player.key_passes_p90 != null && <span>KP {fmt(player.key_passes_p90, 2)}</span>}
          {player.tackles_p90 != null && <span>Tck {fmt(player.tackles_p90, 2)}</span>}
          {player.shots_on_target_p90 != null && <span>SOT {fmt(player.shots_on_target_p90, 2)}</span>}
          {player.sc_rating != null && <span>⭐ {fmt(player.sc_rating)}</span>}
        </div>
      )}

      {hasStats && player.form_goals_5 != null && (
        <div className="text-gray-500 text-xs">
          Forme 5j: {player.form_goals_5}⚽
          {player.form_xg_5 != null && <span> · xG {fmt(player.form_xg_5)}</span>}
          {player.form_rating_5 != null && <span> · ★ {fmt(player.form_rating_5)}</span>}
        </div>
      )}
    </div>
  )
}

function PositionSection({ emoji, label, players }: { emoji: string; label: string; players: WCPlayer[] }) {
  if (players.length === 0) return null
  return (
    <div className="mb-5">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">
        {emoji} {label} ({players.length})
      </h3>
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-2">
        {players.map((p) => (
          <PlayerCard key={p.player_name} player={p} />
        ))}
      </div>
    </div>
  )
}

export function WC2026View({ nations, selectedNation, squad, loading, onSelectNation, onSwitchToTable }: Props) {
  const grouped = useMemo(() => {
    const map: Record<string, WCNation[]> = {}
    for (const n of nations) {
      if (!map[n.group_letter]) map[n.group_letter] = []
      map[n.group_letter].push(n)
    }
    return map
  }, [nations])

  const groups = useMemo(() => Object.keys(grouped).sort(), [grouped])

  return (
    <div>
      {onSwitchToTable && (
        <div className="flex justify-end mb-3">
          <button
            onClick={onSwitchToTable}
            className="px-3 py-2 rounded-lg text-sm font-medium border border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-500 hover:text-white transition-colors"
          >
            ▦ Tableau
          </button>
        </div>
      )}
      <div className="flex gap-0 border border-gray-700 rounded-xl overflow-hidden min-h-[500px]">
        {/* Sidebar */}
        <div className="w-44 shrink-0 border-r border-gray-700 overflow-y-auto bg-gray-900">
          {groups.map((letter) => (
            <div key={letter}>
              <div className="px-3 py-1.5 text-[10px] font-bold uppercase tracking-widest text-gray-500 bg-gray-800/60">
                Groupe {letter}
              </div>
              {grouped[letter].map((n) => (
                <button
                  key={n.nation}
                  onClick={() => onSelectNation(n.nation)}
                  className={clsx(
                    'w-full text-left px-3 py-2 text-sm flex items-center gap-2 transition-colors border-l-2',
                    selectedNation === n.nation
                      ? 'bg-brand-600/20 border-brand-500 text-white'
                      : 'border-transparent text-gray-400 hover:bg-gray-800 hover:text-white'
                  )}
                >
                  <FlagImg emoji={n.flag_emoji} size={16} />
                  <span className="truncate">{n.nation}</span>
                </button>
              ))}
            </div>
          ))}
        </div>

        {/* Main panel */}
        <div className="flex-1 p-5 overflow-y-auto bg-gray-900/50">
          {!selectedNation && (
            <div className="flex items-center justify-center h-full text-gray-500 text-sm">
              Sélectionne une nation
            </div>
          )}
          {selectedNation && loading && (
            <div className="flex items-center justify-center h-full text-gray-500 text-sm">
              Chargement…
            </div>
          )}
          {selectedNation && !loading && squad && (
            <>
              <div className="flex items-center gap-2 mb-5">
                <FlagImg emoji={squad.flag_emoji} size={32} />
                <div>
                  <h2 className="text-white font-bold text-lg leading-tight">{squad.nation}</h2>
                  <span className="text-gray-400 text-xs">
                    Groupe {squad.group_letter} · {squad.gk.length + squad.def_.length + squad.mid.length + squad.fwd.length} joueurs
                  </span>
                </div>
              </div>
              <PositionSection emoji="🧤" label="Gardiens" players={squad.gk} />
              <PositionSection emoji="🛡" label="Défenseurs" players={squad.def_} />
              <PositionSection emoji="⚙" label="Milieux" players={squad.mid} />
              <PositionSection emoji="⚽" label="Attaquants" players={squad.fwd} />
            </>
          )}
          {selectedNation && !loading && !squad && (
            <div className="flex items-center justify-center h-full text-gray-500 text-sm">
              Données non disponibles pour cette nation
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
