'use client'

import { useState, useEffect, useCallback } from 'react'
import { Calculator, RefreshCw, ChevronDown } from 'lucide-react'
import { clsx } from 'clsx'
import { getFixtures, priceMatch, type FixtureOut, type MatchPriceResponse, type PlayerAllocationOut } from '@/lib/api'

// ── Helpers ────────────────────────────────────────────────────────

function fmtOdds(o: number): string {
  return o >= 100 ? '—' : o.toFixed(2)
}

function fmtPct(p: number): string {
  return `${(p * 100).toFixed(1)}%`
}

function fmtMins(m: number): string {
  return `${Math.round(m)}'`
}

const POS_COLOR: Record<string, string> = {
  FW: 'text-orange-400',
  MF: 'text-blue-400',
  DF: 'text-gray-400',
}

// ── Team table ─────────────────────────────────────────────────────

interface TeamTableProps {
  teamName: string
  matchXg: number
  players: PlayerAllocationOut[]
  xgOverride: string
  onXgOverride: (v: string) => void
  penTakerOverride: number | null
  onPenTakerClick: (playerId: number) => void
  isHome: boolean
}

function TeamTable({
  teamName,
  matchXg,
  players,
  xgOverride,
  onXgOverride,
  penTakerOverride,
  onPenTakerClick,
  isHome,
}: TeamTableProps) {
  return (
    <div className="bg-gray-800 rounded-xl overflow-hidden">
      {/* Header */}
      <div className={clsx(
        'px-4 py-3 flex items-center justify-between',
        isHome ? 'bg-orange-500/10 border-b border-orange-500/20' : 'bg-blue-500/10 border-b border-blue-500/20',
      )}>
        <div>
          <span className="font-semibold text-white text-sm">{teamName}</span>
          <span className={clsx(
            'ml-2 text-xs font-medium px-2 py-0.5 rounded',
            isHome ? 'bg-orange-500/20 text-orange-300' : 'bg-blue-500/20 text-blue-300',
          )}>
            {isHome ? 'DOM.' : 'EXT.'}
          </span>
        </div>
        {/* xG display + override */}
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-400">Match xG</span>
          <span className={clsx(
            'text-sm font-bold',
            isHome ? 'text-orange-300' : 'text-blue-300',
          )}>
            {matchXg.toFixed(2)}
          </span>
          <input
            type="number"
            step="0.1"
            min="0"
            max="5"
            placeholder="override"
            value={xgOverride}
            onChange={(e) => onXgOverride(e.target.value)}
            className="w-20 text-xs bg-gray-700 border border-gray-600 rounded px-2 py-1 text-white placeholder-gray-500 focus:outline-none focus:border-orange-500"
          />
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-gray-700 text-gray-400">
              <th className="text-left px-3 py-2 font-medium">Joueur</th>
              <th className="px-2 py-2 font-medium">Pos</th>
              <th className="px-2 py-2 font-medium">Min</th>
              <th className="px-3 py-2 font-medium text-orange-300 border-l border-gray-700">P(but)</th>
              <th className="px-3 py-2 font-medium text-orange-300">Cote But</th>
              <th className="px-3 py-2 font-medium text-blue-300 border-l border-gray-700">P(passe)</th>
              <th className="px-3 py-2 font-medium text-blue-300">Cote Pass</th>
            </tr>
          </thead>
          <tbody>
            {players.map((p) => {
              const isPenTaker = penTakerOverride
                ? p.player_id === penTakerOverride
                : p.is_pen_taker
              return (
                <tr
                  key={p.player_id}
                  onClick={() => onPenTakerClick(p.player_id)}
                  className={clsx(
                    'border-b border-gray-700/50 cursor-pointer transition-colors',
                    isPenTaker
                      ? 'bg-amber-500/10 hover:bg-amber-500/15'
                      : 'hover:bg-gray-700/40',
                  )}
                  title="Cliquer pour désigner comme tireur de penalty"
                >
                  {/* Player name + pen taker indicator */}
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-1.5">
                      {isPenTaker && (
                        <span
                          title="Tireur de penalty"
                          className="text-amber-400 text-[10px] leading-none font-bold"
                        >
                          ⬡P
                        </span>
                      )}
                      <span className={clsx(
                        'font-medium',
                        isPenTaker ? 'text-amber-200' : 'text-white',
                      )}>
                        {p.player_name}
                      </span>
                    </div>
                  </td>

                  {/* Position */}
                  <td className="px-2 py-2 text-center">
                    <span className={clsx(
                      'font-medium',
                      POS_COLOR[p.position ?? ''] ?? 'text-gray-400',
                    )}>
                      {p.position ?? '—'}
                    </span>
                  </td>

                  {/* Minutes */}
                  <td className="px-2 py-2 text-center text-gray-400">
                    {fmtMins(p.expected_minutes)}
                  </td>

                  {/* P(but) */}
                  <td className="px-3 py-2 text-center border-l border-gray-700/50">
                    <span className={clsx(
                      'font-medium',
                      p.prob_goal >= 0.40 ? 'text-green-400' :
                      p.prob_goal >= 0.20 ? 'text-orange-300' :
                      'text-gray-300',
                    )}>
                      {fmtPct(p.prob_goal)}
                    </span>
                  </td>

                  {/* Cote But Ev0 */}
                  <td className="px-3 py-2 text-center">
                    <span className={clsx(
                      'font-bold',
                      isPenTaker ? 'text-amber-300' : 'text-white',
                    )}>
                      {fmtOdds(p.fair_odds_goal)}
                    </span>
                  </td>

                  {/* P(passe) */}
                  <td className="px-3 py-2 text-center border-l border-gray-700/50">
                    <span className={clsx(
                      'font-medium',
                      p.prob_assist >= 0.25 ? 'text-blue-300' :
                      p.prob_assist >= 0.12 ? 'text-blue-400' :
                      'text-gray-400',
                    )}>
                      {fmtPct(p.prob_assist)}
                    </span>
                  </td>

                  {/* Cote Pass Ev0 */}
                  <td className="px-3 py-2 text-center">
                    <span className="font-bold text-gray-200">
                      {fmtOdds(p.fair_odds_assist)}
                    </span>
                  </td>
                </tr>
              )
            })}
            {players.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-6 text-center text-gray-500 italic">
                  Aucun joueur trouvé pour cette équipe
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────

export default function CalculatorPage() {
  const [fixtures, setFixtures] = useState<FixtureOut[]>([])
  const [selectedFixtureId, setSelectedFixtureId] = useState<number | null>(null)
  const [pricing, setPricing] = useState<MatchPriceResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [loadingFixtures, setLoadingFixtures] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Overrides
  const [homeXgOverride, setHomeXgOverride] = useState('')
  const [awayXgOverride, setAwayXgOverride] = useState('')
  const [homePenTaker, setHomePenTaker] = useState<number | null>(null)
  const [awayPenTaker, setAwayPenTaker] = useState<number | null>(null)

  // Load upcoming fixtures
  useEffect(() => {
    setLoadingFixtures(true)
    getFixtures({ status: 'scheduled' })
      .then((res) => setFixtures(res.fixtures))
      .catch(() => setFixtures([]))
      .finally(() => setLoadingFixtures(false))
  }, [])

  const fetchPricing = useCallback(async (fixtureId: number) => {
    setLoading(true)
    setError(null)
    try {
      const result = await priceMatch({
        fixture_id: fixtureId,
        home_xg_override: homeXgOverride ? Number(homeXgOverride) : null,
        away_xg_override: awayXgOverride ? Number(awayXgOverride) : null,
        home_pen_taker_override: homePenTaker,
        away_pen_taker_override: awayPenTaker,
      })
      setPricing(result)
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Erreur lors du chargement du pricing')
    } finally {
      setLoading(false)
    }
  }, [homeXgOverride, awayXgOverride, homePenTaker, awayPenTaker])

  // Auto-fetch when fixture selected or overrides change
  useEffect(() => {
    if (selectedFixtureId !== null) {
      fetchPricing(selectedFixtureId)
    }
  }, [selectedFixtureId, homeXgOverride, awayXgOverride, homePenTaker, awayPenTaker, fetchPricing])

  function handleFixtureSelect(e: React.ChangeEvent<HTMLSelectElement>) {
    const id = Number(e.target.value)
    setSelectedFixtureId(id || null)
    setPricing(null)
    setHomeXgOverride('')
    setAwayXgOverride('')
    setHomePenTaker(null)
    setAwayPenTaker(null)
  }

  function handleHomePenClick(playerId: number) {
    setHomePenTaker(prev => prev === playerId ? null : playerId)
  }

  function handleAwayPenClick(playerId: number) {
    setAwayPenTaker(prev => prev === playerId ? null : playerId)
  }

  const selectedFixture = fixtures.find(f => f.id === selectedFixtureId)

  return (
    <div className="p-4 md:p-6 max-w-7xl">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-white flex items-center gap-2">
          <Calculator className="w-6 h-6" />
          Calculateur Ev0
        </h1>
        <p className="text-gray-400 mt-1 text-sm">
          Modèle Top-Down — Team xG → allocation joueurs → Poisson
        </p>
      </div>

      {/* Match selector */}
      <div className="mb-6">
        <label className="block text-sm text-gray-400 mb-2">Sélectionner un match</label>
        <div className="relative">
          <select
            value={selectedFixtureId ?? ''}
            onChange={handleFixtureSelect}
            disabled={loadingFixtures}
            className="w-full max-w-xl bg-gray-800 border border-gray-600 rounded-lg px-4 py-3 text-white appearance-none cursor-pointer focus:outline-none focus:border-orange-500 disabled:opacity-50"
          >
            <option value="">
              {loadingFixtures ? 'Chargement…' : '— Choisir un match —'}
            </option>
            {fixtures.map(f => (
              <option key={f.id} value={f.id}>
                {f.home_team} vs {f.away_team}
                {' · '}
                {new Date(f.kickoff_utc).toLocaleDateString('fr-FR', {
                  day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
                })}
                {' · '}{f.league.replace('_', ' ').toUpperCase()}
              </option>
            ))}
          </select>
          <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
        </div>
      </div>

      {/* Loading state */}
      {loading && (
        <div className="flex items-center gap-2 text-gray-400 mb-6">
          <RefreshCw className="w-4 h-4 animate-spin" />
          <span className="text-sm">Calcul en cours…</span>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="mb-6 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-3 text-red-400 text-sm">
          {error}
        </div>
      )}

      {/* Legend */}
      {pricing && !loading && (
        <div className="mb-4 flex items-center gap-4 text-xs text-gray-500">
          <span className="flex items-center gap-1">
            <span className="text-amber-400 font-bold">⬡P</span>
            Tireur de penalty (auto-détecté · cliquer pour changer)
          </span>
          <span className="w-px h-3 bg-gray-700" />
          <span className="flex items-center gap-1">
            <span className="text-orange-300">FW</span> ·
            <span className="text-blue-400">MF</span> ·
            <span className="text-gray-400">DF</span>
          </span>
        </div>
      )}

      {/* Tables */}
      {pricing && !loading && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          <TeamTable
            teamName={pricing.home_team}
            matchXg={pricing.home_match_xg}
            players={pricing.home_players}
            xgOverride={homeXgOverride}
            onXgOverride={setHomeXgOverride}
            penTakerOverride={homePenTaker}
            onPenTakerClick={handleHomePenClick}
            isHome={true}
          />
          <TeamTable
            teamName={pricing.away_team}
            matchXg={pricing.away_match_xg}
            players={pricing.away_players}
            xgOverride={awayXgOverride}
            onXgOverride={setAwayXgOverride}
            penTakerOverride={awayPenTaker}
            onPenTakerClick={handleAwayPenClick}
            isHome={false}
          />
        </div>
      )}

      {/* Empty state */}
      {!selectedFixtureId && !loading && (
        <div className="text-center py-20 text-gray-600">
          <Calculator className="w-12 h-12 mx-auto mb-3 opacity-30" />
          <p className="text-sm">Sélectionne un match pour calculer les cotes Ev0</p>
        </div>
      )}
    </div>
  )
}
