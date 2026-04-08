'use client'

import { useState } from 'react'
import { Users, ChevronDown, Save, Play } from 'lucide-react'
import { clsx } from 'clsx'
import { type PlayerAllocationOut } from '@/lib/api'

// ── Types ─────────────────────────────────────────────────────────

type Mode = 'probable' | 'officielle' | 'derniere'

interface LineupPricingWidgetProps {
  fixtureId: number
  team: string
  teamPlayers: PlayerAllocationOut[]  // full-squad allocations (for player list)
  lineupPlayers: PlayerAllocationOut[] | null  // redistributed result from backend
  isHome: boolean
  onCalculate: (starters: string[]) => void
}

// ── Helpers ────────────────────────────────────────────────────────

const POS_ORDER: Record<string, number> = { FW: 0, MF: 1, DF: 2 }

function sortByPositionThenName(players: PlayerAllocationOut[]): PlayerAllocationOut[] {
  return [...players].sort((a, b) => {
    const pa = POS_ORDER[a.position ?? ''] ?? 3
    const pb = POS_ORDER[b.position ?? ''] ?? 3
    if (pa !== pb) return pa - pb
    return a.player_name.localeCompare(b.player_name)
  })
}

function fmtOdds(o: number): string {
  return o >= 100 ? '—' : o.toFixed(2)
}

function fmtPct(p: number): string {
  return `${(p * 100).toFixed(1)}%`
}

const POS_COLOR: Record<string, string> = {
  FW: 'text-orange-400',
  MF: 'text-blue-400',
  DF: 'text-gray-400',
}

// ── Component ─────────────────────────────────────────────────────

export function LineupPricingWidget({
  fixtureId,
  team,
  teamPlayers,
  lineupPlayers,
  isHome,
  onCalculate,
}: LineupPricingWidgetProps) {
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<Mode | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState<string | null>(null)

  // When "Dernière compo" is clicked, auto-fetch and compute
  async function handleDerniereCompo() {
    setMode('derniere')
    try {
      const r = await fetch(`/api/v1/lineups/fixture/${fixtureId}`)
      if (!r.ok) return
      const d = await r.json()
      const side = isHome ? d.home : d.away
      if (!side) return
      const starters: string[] = side.players
        .filter((p: { is_starter: boolean; player_name: string }) => p.is_starter)
        .map((p: { player_name: string }) => p.player_name)
      if (starters.length >= 5) {
        setSelected(new Set(starters))
        onCalculate(starters)
      }
    } catch {
      // non-fatal
    }
  }

  async function handleSave() {
    if (selected.size < 5) return
    setSaving(true)
    setSaveMsg(null)
    try {
      const players = Array.from(selected).map((name) => ({
        player_name: name,
        position: teamPlayers.find((p) => p.player_name === name)?.position ?? 'MF',
        is_starter: true,
      }))
      const r = await fetch('/api/v1/lineups', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fixture_id: fixtureId, team, players }),
      })
      setSaveMsg(r.ok ? 'Compo sauvegardée ✓' : 'Erreur lors de la sauvegarde')
    } catch {
      setSaveMsg('Erreur lors de la sauvegarde')
    } finally {
      setSaving(false)
    }
  }

  function togglePlayer(name: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  return (
    <div className="mt-2 rounded-xl border border-gray-700 bg-gray-900 overflow-hidden">
      {/* Toggle header */}
      <button
        onClick={() => setOpen((o) => !o)}
        className={clsx(
          'w-full flex items-center justify-between px-4 py-2.5 text-sm font-medium transition-colors',
          open ? 'bg-ev-surface2' : 'hover:bg-ev-surface2',
        )}
      >
        <span className="flex items-center gap-2 text-gray-300">
          <Users className="w-4 h-4" />
          Mode Compo
        </span>
        <ChevronDown className={clsx('w-4 h-4 text-gray-400 transition-transform', open && 'rotate-180')} />
      </button>

      {open && (
        <div className="px-4 py-3 space-y-3 border-t border-gray-700">
          {/* Mode selector */}
          <div className="flex gap-2 flex-wrap">
            {(['probable', 'officielle'] as const).map((m) => (
              <button
                key={m}
                onClick={() => { setMode(m); setSelected(new Set()) }}
                className={clsx(
                  'px-3 py-1.5 rounded-lg text-xs font-medium transition-colors border',
                  mode === m
                    ? (isHome ? 'bg-orange-500/20 border-orange-500/40 text-orange-300' : 'bg-blue-500/20 border-blue-500/40 text-blue-300')
                    : 'bg-ev-surface2 border-gray-600 text-gray-400 hover:text-ev-t1',
                )}
              >
                {m === 'probable' ? 'Compo probable' : 'Compo officielle'}
              </button>
            ))}
            <button
              onClick={handleDerniereCompo}
              className={clsx(
                'px-3 py-1.5 rounded-lg text-xs font-medium transition-colors border',
                mode === 'derniere'
                  ? 'bg-gray-500/30 border-gray-400/40 text-gray-200'
                  : 'bg-ev-surface2 border-gray-600 text-gray-400 hover:text-ev-t1',
              )}
            >
              Dernière compo
            </button>
          </div>

          {/* Player picker (for probable / officielle modes) */}
          {(mode === 'probable' || mode === 'officielle') && (
            <div>
              <p className="text-xs text-gray-500 mb-2">
                Sélectionne les titulaires ({selected.size} sélectionnés)
              </p>
              {(() => {
                const POS_GROUPS: { key: string; label: string; color: string; filter: (pos: string | null) => boolean }[] = [
                  { key: 'FW', label: 'Attaquants', color: 'text-orange-400', filter: (p) => p === 'FW' },
                  { key: 'MF', label: 'Milieux',    color: 'text-blue-400',   filter: (p) => p === 'MF' },
                  { key: 'DF', label: 'Défenseurs', color: 'text-gray-400',   filter: (p) => p === 'DF' },
                ]
                const sorted = sortByPositionThenName(teamPlayers)
                return (
                  <div className="max-h-72 overflow-y-auto pr-1 space-y-3">
                    {POS_GROUPS.map(({ key, label, color, filter }) => {
                      const group = sorted.filter((p) => filter(p.position))
                      if (group.length === 0) return null
                      return (
                        <div key={key}>
                          <div className={clsx('flex items-center gap-2 mb-1.5')}>
                            <span className={clsx('text-xs font-bold uppercase tracking-wider', color)}>{label}</span>
                            <span className="flex-1 h-px bg-gray-700" />
                            <span className="text-[10px] text-gray-500">{group.length}</span>
                          </div>
                          <div className="grid grid-cols-2 gap-0.5">
                            {group.map((p) => (
                              <label
                                key={p.player_id}
                                className={clsx(
                                  'flex items-center gap-2 px-2 py-1.5 rounded cursor-pointer text-xs transition-colors',
                                  selected.has(p.player_name)
                                    ? (isHome ? 'bg-orange-500/20 text-orange-200' : 'bg-blue-500/20 text-blue-200')
                                    : 'hover:bg-ev-surface2 text-gray-300',
                                )}
                              >
                                <input
                                  type="checkbox"
                                  checked={selected.has(p.player_name)}
                                  onChange={() => togglePlayer(p.player_name)}
                                  className="accent-orange-400 shrink-0"
                                />
                                <span className="truncate">{p.player_name}</span>
                              </label>
                            ))}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )
              })()}

              {/* Action buttons */}
              <div className="flex items-center gap-2 mt-3 flex-wrap">
                <button
                  onClick={() => onCalculate(Array.from(selected))}
                  disabled={selected.size < 5}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-orange-500 hover:bg-orange-600 disabled:opacity-40 text-white text-xs font-medium rounded-lg transition-colors"
                >
                  <Play className="w-3 h-3" />
                  Calculer ({selected.size})
                </button>
                <button
                  onClick={handleSave}
                  disabled={selected.size < 5 || saving}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-gray-600 hover:bg-ev-surface2 disabled:opacity-40 text-white text-xs font-medium rounded-lg transition-colors"
                >
                  <Save className="w-3 h-3" />
                  {saving ? 'Sauvegarde…' : 'Sauvegarder'}
                </button>
                {saveMsg && <span className="text-xs text-gray-400">{saveMsg}</span>}
              </div>
            </div>
          )}

          {/* Redistributed pricing result */}
          {lineupPlayers && lineupPlayers.length > 0 && (
            <div className="mt-2">
              <p className="text-xs font-semibold text-gray-300 mb-2 flex items-center gap-1">
                <span className={clsx('w-1.5 h-1.5 rounded-full', isHome ? 'bg-orange-400' : 'bg-blue-400')} />
                xG redistribué — {lineupPlayers.length} titulaires
              </p>
              <div className="overflow-x-auto rounded-lg border border-gray-700">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="bg-gray-800 text-gray-400 border-b border-gray-700">
                      <th className="text-left px-3 py-1.5 font-medium">Joueur</th>
                      <th className="px-2 py-1.5 font-medium">Pos</th>
                      <th className="px-3 py-1.5 font-medium text-orange-300 border-l border-gray-700">P(but)</th>
                      <th className="px-3 py-1.5 font-medium text-orange-300">Cote But</th>
                      <th className="px-3 py-1.5 font-medium text-blue-300 border-l border-gray-700">P(passe)</th>
                      <th className="px-3 py-1.5 font-medium text-blue-300">Cote Pass</th>
                    </tr>
                  </thead>
                  <tbody>
                    {lineupPlayers.map((p) => (
                      <tr key={p.player_id} className="border-b border-gray-700/50 hover:bg-ev-surface2">
                        <td className="px-3 py-1.5">
                          <span className={clsx('font-medium', p.is_pen_taker ? 'text-amber-200' : 'text-white')}>
                            {p.is_pen_taker && <span className="text-amber-400 text-[10px] mr-1">⬡P</span>}
                            {p.player_name}
                          </span>
                        </td>
                        <td className="px-2 py-1.5 text-center">
                          <span className={POS_COLOR[p.position ?? ''] ?? 'text-gray-400'}>
                            {p.position ?? '—'}
                          </span>
                        </td>
                        <td className="px-3 py-1.5 text-center border-l border-gray-700/50">
                          <span className={clsx(
                            'font-medium',
                            p.prob_goal >= 0.40 ? 'text-green-400' :
                            p.prob_goal >= 0.20 ? 'text-orange-300' :
                            'text-gray-300',
                          )}>
                            {fmtPct(p.prob_goal)}
                          </span>
                        </td>
                        <td className="px-3 py-1.5 text-center font-bold text-white">
                          {fmtOdds(p.fair_odds_goal)}
                        </td>
                        <td className="px-3 py-1.5 text-center border-l border-gray-700/50">
                          <span className={clsx(
                            'font-medium',
                            p.prob_assist >= 0.25 ? 'text-blue-300' :
                            p.prob_assist >= 0.12 ? 'text-blue-400' :
                            'text-gray-400',
                          )}>
                            {fmtPct(p.prob_assist)}
                          </span>
                        </td>
                        <td className="px-3 py-1.5 text-center font-bold text-gray-200">
                          {fmtOdds(p.fair_odds_assist)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
