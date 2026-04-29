"use client"

import { Badge } from "@/components/ui/badge"

export type LineupPlayer = {
  player_name: string
  position: string      // GK | DEF | MID | FWD
  is_starter: boolean
  is_striker: boolean
}

export type LineupData = {
  lineup_type: "official" | "probable_statshub" | "probable_manual" | "last_known"
  lineup_id: number | null
  team: string
  players: LineupPlayer[]
}

const BADGE_CONFIG = {
  official:           { label: "Officielle",  className: "bg-green-600 text-white hover:bg-green-600" },
  probable_statshub:  { label: "StatsHub",    className: "bg-blue-500 text-white hover:bg-blue-500" },
  probable_manual:    { label: "Probable",    className: "bg-orange-500 text-white hover:bg-orange-500" },
  last_known:         { label: "Dernière compo", className: "bg-gray-500 text-white hover:bg-ev-surface2" },
}

const POSITION_ORDER = ["GK", "DEF", "MID", "FWD"] as const

/** Place les BU au centre de leur ligne, les non-BU de chaque côté. */
function centerBU(players: LineupPlayer[]): LineupPlayer[] {
  const bus   = players.filter(p => p.is_striker)
  const wings = players.filter(p => !p.is_striker)
  const split = Math.ceil(wings.length / 2)
  return [...wings.slice(0, split), ...bus, ...wings.slice(split)]
}

/**
 * Déduit la formation en comptant DEF-MID-FWD.
 * Attend en entrée uniquement les titulaires (is_starter=true).
 */
function getFormation(starters: LineupPlayer[]): string {
  const counts: Record<string, number> = {}
  for (const p of starters) {
    if (p.position !== "GK") counts[p.position] = (counts[p.position] ?? 0) + 1
  }
  const parts = (["DEF", "MID", "FWD"] as const)
    .map(pos => counts[pos] ?? 0)
    .filter(n => n > 0)
  return parts.join("-")
}

export function LineupDisplay({ lineup }: { lineup: LineupData }) {
  const badge = BADGE_CONFIG[lineup.lineup_type] ?? BADGE_CONFIG["last_known"]
  const starters = lineup.players.filter(p => p.is_starter)
  const formation = getFormation(starters)

  const byPos: Record<string, LineupPlayer[]> = {}
  for (const pos of POSITION_ORDER) byPos[pos] = starters.filter(p => p.position === pos)

  // Détection auto BU : si ≤ 2 FWD et aucun marqué manuellement → tous BU par défaut
  const fwds = byPos["FWD"] ?? []
  if (fwds.length <= 2 && fwds.every(p => !p.is_striker)) {
    byPos["FWD"] = fwds.map(p => ({ ...p, is_striker: true }))
  }

  return (
    <div className="space-y-1 text-sm">
      <div className="flex items-center gap-2 mb-2">
        <Badge className={badge.className}>{badge.label}</Badge>
        {formation && (
          <span className="text-xs text-muted-foreground font-mono">{formation}</span>
        )}
      </div>

      {POSITION_ORDER.map(pos => {
        const line = byPos[pos] ?? []
        if (line.length === 0) return null
        const sorted = pos === "FWD" ? centerBU(line) : line
        return (
          <div key={pos} className="flex gap-x-3 justify-center py-0.5 flex-wrap">
            {sorted.map(p => (
              <span
                key={p.player_name}
                className={
                  p.is_striker
                    ? "font-bold underline decoration-orange-400"
                    : "text-foreground"
                }
              >
                {p.player_name}
              </span>
            ))}
          </div>
        )
      })}
    </div>
  )
}
