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
  const [error, setError]       = useState<string | null>(null)

  useEffect(() => {
    fetch(`/api/v1/lineups/team-players/${encodeURIComponent(team)}`)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then(setRoster)
      .catch(e => setError(`Impossible de charger l'effectif : ${e.message}`))
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
    setError(null)
    try {
      const r = await fetch("/api/v1/lineups", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fixture_id: fixtureId, team, players: selected }),
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      onSaved()
    } catch (e: unknown) {
      setError(`Erreur lors de la sauvegarde : ${e instanceof Error ? e.message : e}`)
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete() {
    if (!existingLineupId) return
    setError(null)
    try {
      const r = await fetch(`/api/v1/lineups/${existingLineupId}`, { method: "DELETE" })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      onDeleted()
    } catch (e: unknown) {
      setError(`Erreur lors de la suppression : ${e instanceof Error ? e.message : e}`)
    }
  }

  return (
    <div className="space-y-4">
      {error && <p className="text-xs text-red-400">{error}</p>}

      {/* Sélecteur de joueurs — bouton + sélecteur position côte à côte */}
      <div className="flex flex-wrap gap-2">
        {roster.map(name => {
          const sel = selected.find(p => p.player_name === name)
          return (
            <div key={name} className="inline-flex items-center gap-1">
              <button
                onClick={() => togglePlayer(name)}
                className={`px-2 py-1 rounded text-sm border transition-colors ${
                  sel
                    ? "bg-brand-600 text-white border-brand-600"
                    : "bg-gray-700 border-gray-600 text-gray-300 hover:border-brand-500"
                }`}
              >
                {name}
              </button>
              {sel && (
                <select
                  className="text-xs bg-gray-800 border border-gray-600 rounded px-1 py-1 text-gray-300 cursor-pointer"
                  value={sel.position}
                  onChange={e => setPosition(name, e.target.value)}
                >
                  {POSITIONS.map(pos => (
                    <option key={pos} value={pos} className="bg-gray-800">{pos}</option>
                  ))}
                </select>
              )}
            </div>
          )
        })}
      </div>

      <p className="text-xs text-gray-400">
        {selected.length} joueur(s) sélectionné(s) — tous marqués titulaires
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
