"use client"

import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"

const POSITIONS = ["GK", "DEF", "MID", "FWD"] as const

type EditPlayer = {
  player_name: string
  position: string
  is_starter: boolean
}

export function LineupEditor({
  fixtureId,
  team,
  existingLineupId,
  onSaved,
  onDeleted,
}: {
  fixtureId: number
  team: string
  existingLineupId: number | null
  onSaved: () => void
  onDeleted: () => void
}) {
  const [roster, setRoster]     = useState<string[]>([])
  const [selected, setSelected] = useState<EditPlayer[]>([])
  const [saving, setSaving]     = useState(false)

  useEffect(() => {
    fetch(`/api/v1/lineups/team-players/${encodeURIComponent(team)}`)
      .then(r => r.json())
      .then(setRoster)
  }, [team])

  function togglePlayer(name: string) {
    setSelected(prev => {
      if (prev.find(p => p.player_name === name)) {
        return prev.filter(p => p.player_name !== name)
      }
      return [...prev, { player_name: name, position: "MID", is_starter: true }]
    })
  }

  function setPosition(name: string, pos: string) {
    setSelected(prev =>
      prev.map(p => p.player_name === name ? { ...p, position: pos } : p)
    )
  }

  async function handleSave() {
    setSaving(true)
    await fetch("/api/v1/lineups", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fixture_id: fixtureId, team, players: selected }),
    })
    setSaving(false)
    onSaved()
  }

  async function handleDelete() {
    if (!existingLineupId) return
    await fetch(`/api/v1/lineups/${existingLineupId}`, { method: "DELETE" })
    onDeleted()
  }

  return (
    <div className="space-y-4">
      {/* Sélecteur de joueurs */}
      <div className="flex flex-wrap gap-2">
        {roster.map(name => {
          const sel = selected.find(p => p.player_name === name)
          return (
            <button
              key={name}
              onClick={() => togglePlayer(name)}
              className={`inline-flex items-center gap-1 px-2 py-1 rounded text-sm border transition-colors ${
                sel
                  ? "bg-brand-600 text-white border-brand-600"
                  : "bg-gray-700 border-gray-600 text-gray-300 hover:border-brand-500"
              }`}
            >
              {name}
              {sel && (
                <select
                  className="ml-1 text-xs bg-transparent border-none outline-none cursor-pointer text-white"
                  value={sel.position}
                  onChange={e => { e.stopPropagation(); setPosition(name, e.target.value) }}
                  onClick={e => e.stopPropagation()}
                >
                  {POSITIONS.map(pos => <option key={pos} value={pos} className="bg-gray-800">{pos}</option>)}
                </select>
              )}
            </button>
          )
        })}
      </div>

      <p className="text-xs text-gray-400">
        {selected.length} joueur(s) sélectionné(s)
      </p>

      <div className="flex gap-2">
        <Button
          onClick={handleSave}
          disabled={saving || selected.length === 0}
          size="sm"
        >
          {saving ? "Sauvegarde…" : "Enregistrer"}
        </Button>
        {existingLineupId && (
          <Button variant="outline" size="sm" onClick={handleDelete}>
            Effacer (retour dernière compo)
          </Button>
        )}
      </div>
    </div>
  )
}
