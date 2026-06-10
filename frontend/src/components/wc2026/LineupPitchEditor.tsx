// frontend/src/components/wc2026/LineupPitchEditor.tsx
'use client'

import { useState, useEffect } from 'react'
import { clsx } from 'clsx'
import { Save } from 'lucide-react'
import { JerseyCard } from './JerseyCard'
import { SquadPanel } from './SquadPanel'
import { type WCLineup, type WCLineupPlayer, type WCSquadPlayer, upsertWCLineup } from '@/lib/api'

const FORMATIONS: Record<string, number[]> = {
  '4-4-2':   [4, 4, 2],
  '4-4-2d':  [4, 1, 2, 1, 2],
  '4-3-3':   [4, 3, 3],
  '4-2-3-1': [4, 2, 3, 1],
  '4-3-2-1': [4, 3, 2, 1],
  '4-5-1':   [4, 5, 1],
  '4-1-4-1': [4, 1, 4, 1],
  '4-1-3-2': [4, 1, 3, 2],
  '4-2-2-2': [4, 2, 2, 2],
  '4-3-1-2': [4, 3, 1, 2],
  '3-5-2':   [3, 5, 2],
  '3-4-3':   [3, 4, 3],
  '3-4-2-1': [3, 4, 2, 1],
  '3-4-1-2': [3, 4, 1, 2],
  '3-3-4':   [3, 3, 4],
  '3-6-1':   [3, 6, 1],
  '5-3-2':   [5, 3, 2],
  '5-4-1':   [5, 4, 1],
  '5-2-3':   [5, 2, 3],
  '5-2-2-1': [5, 2, 2, 1],
  '5-1-2-2': [5, 1, 2, 2],
}

const FORMATION_GROUPS = [
  { label: '4 défenseurs', keys: ['4-4-2','4-4-2d','4-3-3','4-2-3-1','4-3-2-1','4-5-1','4-1-4-1','4-1-3-2','4-2-2-2','4-3-1-2'] },
  { label: '3 défenseurs', keys: ['3-5-2','3-4-3','3-4-2-1','3-4-1-2','3-3-4','3-6-1'] },
  { label: '5 défenseurs', keys: ['5-3-2','5-4-1','5-2-3','5-2-2-1','5-1-2-2'] },
]

const CONTEXTS = [
  { value: 'default',    label: 'Compo type' },
  { value: 'matchday_1', label: 'Journée 1' },
  { value: 'matchday_2', label: 'Journée 2' },
  { value: 'matchday_3', label: 'Journée 3' },
]

const ROLE_MINUTES: Record<string, number> = {
  starter: 85, sub_planned: 30, sub_tactical: 12, reserve: 0,
}

// Normalize accents for cross-source name matching
const normName = (n: string) =>
  n.normalize('NFKD').replace(/\p{Mn}/gu, '').toLowerCase().trim()

interface LineupPitchEditorProps {
  nation: string
  flagEmoji: string | null
  squad: WCSquadPlayer[]
  initialLineups: Record<string, WCLineup>
  onSaved?: () => void
}

export function LineupPitchEditor({
  nation, flagEmoji, squad, initialLineups, onSaved,
}: LineupPitchEditorProps) {
  const [context, setContext] = useState('default')
  const [formation, setFormation] = useState(
    initialLineups['default']?.formation ?? '4-3-3'
  )
  const [players, setPlayers] = useState<WCLineupPlayer[]>(
    initialLineups['default']?.players ?? []
  )
  const [selectedSlot, setSelectedSlot] = useState<{ line: number; slot: number } | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState<string | null>(null)

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setSelectedSlot(null)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  function loadContext(ctx: string) {
    setContext(ctx)
    const lineup = initialLineups[ctx]
    if (lineup) {
      setFormation(lineup.formation)
      setPlayers(lineup.players)
    } else {
      const def = initialLineups['default']
      setFormation(def?.formation ?? '4-3-3')
      setPlayers(def?.players ?? [])
    }
    setSelectedSlot(null)
  }

  function changeFormation(newFmt: string) {
    const newLines = FORMATIONS[newFmt] ?? [4, 3, 3]
    const starters = players.filter((p) => p.is_starter)
    const subs = players.filter((p) => !p.is_starter)

    const byLine = new Map<number, WCLineupPlayer[]>()
    for (const p of starters) {
      const arr = byLine.get(p.line_index) ?? []
      arr.push(p)
      byLine.set(p.line_index, arr)
    }

    const newStarters: WCLineupPlayer[] = []
    const gkLine = byLine.get(0) ?? []
    if (gkLine.length > 0) newStarters.push({ ...gkLine[0], line_index: 0, slot_index: 0 })

    newLines.forEach((count, idx) => {
      const lineIdx = idx + 1
      const existing = (byLine.get(lineIdx) ?? []).slice(0, count)
      existing.forEach((p, si) => newStarters.push({ ...p, line_index: lineIdx, slot_index: si }))
    })

    setFormation(newFmt)
    setPlayers([...newStarters, ...subs])
    setSelectedSlot(null)
  }

  function getStarter(line: number, slot: number): WCLineupPlayer | undefined {
    return players.find((p) => p.is_starter && p.line_index === line && p.slot_index === slot)
  }

  // Scan order: GK (line 0) first, then outfield lines bottom→top, right→left within each line
  function findNextEmptySlot(): { line: number; slot: number } | null {
    if (!getStarter(0, 0)) return { line: 0, slot: 0 }
    for (let lineIdx = 1; lineIdx <= lines.length; lineIdx++) {
      const count = lines[lineIdx - 1]
      for (let slotIdx = count - 1; slotIdx >= 0; slotIdx--) {
        if (!getStarter(lineIdx, slotIdx)) return { line: lineIdx, slot: slotIdx }
      }
    }
    return null
  }

  function placeInSlot(squadPlayer: WCSquadPlayer, line: number, slot: number) {
    setPlayers((prev) => [
      ...prev.filter((p) => !(p.is_starter && p.line_index === line && p.slot_index === slot)),
      {
        player_name: squadPlayer.player_name,
        position: squadPlayer.position,
        shirt_number: squadPlayer.shirt_number,
        line_index: line,
        slot_index: slot,
        is_starter: true,
        role: 'starter',
        expected_minutes: ROLE_MINUTES['starter'],
      },
    ])
  }

  // Click a player in the sidebar: place in selected slot, or auto-place bottom-right→top-left
  function handlePlayerSelect(squadPlayer: WCSquadPlayer) {
    if (players.some((p) => normName(p.player_name) === normName(squadPlayer.player_name))) return
    if (selectedSlot) {
      placeInSlot(squadPlayer, selectedSlot.line, selectedSlot.slot)
      setSelectedSlot(null)
    } else {
      const next = findNextEmptySlot()
      if (next) {
        placeInSlot(squadPlayer, next.line, next.slot)
      } else {
        addAsSub(squadPlayer)
      }
    }
  }

  function addAsSub(squadPlayer: WCSquadPlayer) {
    if (players.some((p) => normName(p.player_name) === normName(squadPlayer.player_name))) return
    setPlayers((prev) => [
      ...prev,
      {
        player_name: squadPlayer.player_name,
        position: squadPlayer.position,
        shirt_number: squadPlayer.shirt_number,
        line_index: -1,
        slot_index: prev.filter((p) => !p.is_starter).length,
        is_starter: false,
        role: 'reserve',
        expected_minutes: 0,
      },
    ])
  }

  // Click a slot: select it for the next manual player placement
  function handleSlotClick(line: number, slot: number) {
    const isSelected = selectedSlot?.line === line && selectedSlot?.slot === slot
    setSelectedSlot(isSelected ? null : { line, slot })
  }

  function removePlayer(playerName: string) {
    setPlayers((prev) => prev.filter((p) => p.player_name !== playerName))
  }

  function updateMinutes(playerName: string, minutes: number) {
    setPlayers((prev) =>
      prev.map((p) => p.player_name === playerName ? { ...p, expected_minutes: minutes } : p)
    )
  }

  async function handleSave() {
    setSaving(true)
    setSaveMsg(null)
    try {
      await upsertWCLineup(nation, context, { formation, players })
      setSaveMsg('Sauvegardé ✓')
      onSaved?.()
    } catch {
      setSaveMsg('Erreur de sauvegarde')
    } finally {
      setSaving(false)
    }
  }

  // Normalize names for dedup check so accented variants are recognised as placed
  const usedNamesNorm = new Set(players.map((p) => normName(p.player_name)))
  const lines = FORMATIONS[formation] ?? [4, 3, 3]
  const subs = players.filter((p) => !p.is_starter)

  return (
    <div className="flex gap-4 h-full">
      {/* Left: pitch + controls */}
      <div className="flex-1 flex flex-col gap-3 min-w-0">
        {/* Top bar */}
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex gap-1">
            {CONTEXTS.map((c) => (
              <button
                key={c.value}
                onClick={() => loadContext(c.value)}
                className={clsx(
                  'px-3 py-1 text-xs rounded-lg border transition-colors',
                  context === c.value
                    ? 'bg-orange-500/20 border-orange-500/50 text-orange-300'
                    : 'border-gray-600 text-gray-400 hover:text-white',
                )}
              >
                {c.label}
              </button>
            ))}
          </div>

          <select
            value={formation}
            onChange={(e) => changeFormation(e.target.value)}
            className="px-2 py-1 text-xs bg-gray-800 border border-gray-600 rounded text-white"
          >
            {FORMATION_GROUPS.map((group) => (
              <optgroup key={group.label} label={group.label}>
                {group.keys.map((key) => (
                  <option key={key} value={key}>{key}</option>
                ))}
              </optgroup>
            ))}
          </select>

          <div className="ml-auto flex items-center gap-2">
            {saveMsg && <span className="text-xs text-gray-400">{saveMsg}</span>}
            <button
              onClick={handleSave}
              disabled={saving}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-orange-500 hover:bg-orange-600 disabled:opacity-40 text-white text-xs font-medium rounded-lg transition-colors"
            >
              <Save className="w-3 h-3" />
              {saving ? 'Sauvegarde…' : 'Sauvegarder'}
            </button>
          </div>
        </div>

        {/* Pitch */}
        <div className="relative bg-green-900/30 border border-green-800/40 rounded-xl p-4 flex flex-col gap-4">
          {[...lines].reverse().map((count, revIdx) => {
            const lineIdx = lines.length - revIdx
            return (
              <div key={lineIdx} className="flex justify-center gap-3">
                {Array.from({ length: count }).map((_, slotIdx) => {
                  const starter = getStarter(lineIdx, slotIdx)
                  const isSelected = selectedSlot?.line === lineIdx && selectedSlot?.slot === slotIdx
                  return (
                    <div key={slotIdx}>
                      {starter ? (
                        <JerseyCard
                          playerName={starter.player_name}
                          shirtNumber={starter.shirt_number ?? null}
                          expectedMinutes={starter.expected_minutes}
                          isSelected={isSelected}
                          role={starter.role as 'starter'}
                          onClick={() => handleSlotClick(lineIdx, slotIdx)}
                          onMinutesChange={(m) => updateMinutes(starter.player_name, m)}
                          onRemove={() => removePlayer(starter.player_name)}
                        />
                      ) : (
                        <button
                          onClick={() => handleSlotClick(lineIdx, slotIdx)}
                          className={clsx(
                            'w-16 h-20 rounded-md border-2 border-dashed flex items-center justify-center text-xs transition-colors',
                            isSelected
                              ? 'border-orange-400 text-orange-400'
                              : 'border-gray-600 text-gray-600 hover:border-gray-400',
                          )}
                        >
                          +
                        </button>
                      )}
                    </div>
                  )
                })}
              </div>
            )
          })}

          {/* GK line */}
          <div className="flex justify-center">
            {(() => {
              const gk = getStarter(0, 0)
              const isSelected = selectedSlot?.line === 0 && selectedSlot?.slot === 0
              return gk ? (
                <JerseyCard
                  playerName={gk.player_name}
                  shirtNumber={gk.shirt_number ?? null}
                  expectedMinutes={gk.expected_minutes}
                  isSelected={isSelected}
                  role="starter"
                  onClick={() => handleSlotClick(0, 0)}
                  onMinutesChange={(m) => updateMinutes(gk.player_name, m)}
                  onRemove={() => removePlayer(gk.player_name)}
                />
              ) : (
                <button
                  onClick={() => handleSlotClick(0, 0)}
                  className={clsx(
                    'w-16 h-20 rounded-md border-2 border-dashed flex items-center justify-center text-xs transition-colors',
                    isSelected
                      ? 'border-orange-400 text-orange-400'
                      : 'border-gray-600 text-gray-600 hover:border-gray-400',
                  )}
                >
                  GK
                </button>
              )
            })()}
          </div>

          {/* Midfield center line */}
          <div className="absolute left-0 right-0 top-1/2 border-t border-green-700/30" />
        </div>

        {/* Subs bench */}
        {subs.length > 0 && (
          <div className="border border-gray-700 rounded-xl p-3">
            <p className="text-xs text-gray-500 mb-2 uppercase tracking-wider font-medium">Remplaçants</p>
            <div className="flex flex-wrap gap-2">
              {subs.map((p) => (
                <JerseyCard
                  key={p.player_name}
                  playerName={p.player_name}
                  shirtNumber={p.shirt_number ?? null}
                  expectedMinutes={p.expected_minutes}
                  isSelected={false}
                  role={p.role as 'sub_planned' | 'sub_tactical' | 'reserve'}
                  onClick={() => {}}
                  onMinutesChange={(m) => updateMinutes(p.player_name, m)}
                  onRemove={() => removePlayer(p.player_name)}
                  compact
                />
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Right: squad panel */}
      <div className="w-44 shrink-0 border-l border-gray-700 pl-3">
        <SquadPanel
          squad={squad}
          usedNamesNorm={usedNamesNorm}
          onPlayerClick={handlePlayerSelect}
          onAddAsSub={addAsSub}
        />
      </div>
    </div>
  )
}
