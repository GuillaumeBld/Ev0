"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { LineupDisplay, LineupData } from "@/components/lineups/LineupDisplay"
import { LineupEditor } from "@/components/lineups/LineupEditor"

type Fixture = {
  id: number
  home_team: string
  away_team: string
  kickoff_utc: string
  league: string
}

type FixtureLineups = {
  fixture_id: number
  home_team: string
  away_team: string
  home: LineupData | null
  away: LineupData | null
}

export default function LineupsAdminPage() {
  const [fixtures, setFixtures]       = useState<Fixture[]>([])
  const [selected, setSelected]       = useState<Fixture | null>(null)
  const [lineups, setLineups]         = useState<FixtureLineups | null>(null)
  const [editingTeam, setEditingTeam] = useState<"home" | "away" | null>(null)
  const [error, setError]             = useState<string | null>(null)
  // Ref so handleSaved always sees the latest selected fixture
  const selectedRef = useRef<Fixture | null>(null)

  useEffect(() => {
    fetch("/api/v1/fixtures?status=scheduled&limit=30")
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((data) => setFixtures(data.fixtures ?? []))
      .catch(e => setError(`Impossible de charger les matchs : ${e.message}`))
  }, [])

  const loadLineups = useCallback(async (fixture: Fixture) => {
    setSelected(fixture)
    selectedRef.current = fixture
    setEditingTeam(null)
    setError(null)
    try {
      const r = await fetch(`/api/v1/lineups/fixture/${fixture.id}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setLineups(await r.json())
    } catch (e: unknown) {
      setError(`Impossible de charger les compos : ${e instanceof Error ? e.message : e}`)
    }
  }, [])

  const handleSaved = useCallback(() => {
    const current = selectedRef.current
    if (current) loadLineups(current)
    setEditingTeam(null)
  }, [loadLineups])

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">Compos probables</h1>
        <p className="text-sm text-gray-400 mt-1">
          Saisie manuelle des compositions avant les matchs.
        </p>
      </div>

      {error && (
        <p className="text-sm text-red-400">{error}</p>
      )}

      {/* Sélecteur de match */}
      <div className="flex flex-wrap gap-2">
        {fixtures.map(fx => (
          <Button
            key={fx.id}
            variant={selected?.id === fx.id ? "default" : "outline"}
            size="sm"
            onClick={() => loadLineups(fx)}
          >
            {fx.home_team} vs {fx.away_team}
            <span className="ml-1 text-xs opacity-70">
              {new Date(fx.kickoff_utc).toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" })}
            </span>
          </Button>
        ))}
      </div>

      {/* Cartes équipes */}
      {lineups && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {(["home", "away"] as const).map(side => {
            const team   = side === "home" ? lineups.home_team : lineups.away_team
            const lineup = side === "home" ? lineups.home : lineups.away
            return (
              <Card key={side}>
                <CardHeader className="pb-2">
                  <CardTitle className="text-base">{team}</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  {lineup ? (
                    <LineupDisplay lineup={lineup} />
                  ) : (
                    <p className="text-sm text-gray-400">Aucune compo connue</p>
                  )}

                  {editingTeam === side ? (
                    <LineupEditor
                      fixtureId={lineups.fixture_id}
                      team={team}
                      existingLineupId={lineup?.lineup_id ?? null}
                      onSaved={handleSaved}
                      onDeleted={handleSaved}
                    />
                  ) : (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setEditingTeam(side)}
                    >
                      Modifier
                    </Button>
                  )}
                </CardContent>
              </Card>
            )
          })}
        </div>
      )}
    </div>
  )
}
