"use client"

import { useState, useEffect, useCallback } from "react"
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
  const [fixtures, setFixtures]     = useState<Fixture[]>([])
  const [selected, setSelected]     = useState<Fixture | null>(null)
  const [lineups, setLineups]       = useState<FixtureLineups | null>(null)
  const [editingTeam, setEditingTeam] = useState<"home" | "away" | null>(null)

  useEffect(() => {
    fetch("/api/v1/fixtures?status=scheduled&limit=30")
      .then(r => r.json())
      .then((data) => setFixtures(Array.isArray(data) ? data : data.fixtures ?? []))
  }, [])

  const loadLineups = useCallback(async (fixture: Fixture) => {
    setSelected(fixture)
    setEditingTeam(null)
    const r = await fetch(`/api/v1/lineups/fixture/${fixture.id}`)
    setLineups(await r.json())
  }, [])

  function handleSaved() {
    if (selected) loadLineups(selected)
    setEditingTeam(null)
  }

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">Compos probables</h1>
        <p className="text-sm text-gray-400 mt-1">
          Saisie manuelle des compositions avant les matchs.
        </p>
      </div>

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
