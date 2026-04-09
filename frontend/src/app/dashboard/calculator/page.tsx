'use client'

import { useState, useEffect, useCallback, useRef, Suspense } from 'react'
import { useSearchParams } from 'next/navigation'
import { Calculator, RefreshCw, ChevronDown } from 'lucide-react'
import { clsx } from 'clsx'
import { getFixtures, priceMatch, type FixtureOut, type MatchPriceResponse, type PlayerAllocationOut } from '@/lib/api'
import { LineupPricingWidget } from '@/components/calculator/LineupPricingWidget'
import { XgBadge } from '@/components/XgBadge'

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
  xgSource?: string | null
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
  xgSource,
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
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-semibold text-white text-sm">{teamName}</span>
          <span className={clsx(
            'text-xs font-medium px-2 py-0.5 rounded',
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
          {(xgSource === 'bzzoiro' || xgSource === 'model') && (
            <XgBadge source={xgSource} />
          )}
          <input
            type="number"
            step="0.05"
            min="0"
            max="5"
            placeholder="override"
            value={xgOverride}
            onChange={(e) => onXgOverride(e.target.value)}
            className="w-20 text-xs bg-gray-700 border border-gray-600 rounded px-2 py-1 text-white placeholder-ev-t3 focus:outline-none focus:border-orange-500"
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
                      : 'hover:bg-ev-surface2',
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

function CalculatorInner() {
  const searchParams = useSearchParams()
  const matchParam = searchParams.get('match')

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

  // Lineup starters for compo redistribution (sent to priceMatch)
  const homeStartersRef = useRef<string[] | null>(null)
  const awayStartersRef = useRef<string[] | null>(null)

  // xG refs so fetchPricing doesn't need them as deps (avoids re-fetch on each keystroke)
  const homeXgRef = useRef(homeXgOverride)
  const awayXgRef = useRef(awayXgOverride)
  useEffect(() => { homeXgRef.current = homeXgOverride }, [homeXgOverride])
  useEffect(() => { awayXgRef.current = awayXgOverride }, [awayXgOverride])

  // Load upcoming fixtures, auto-select if ?match= param present
  useEffect(() => {
    setLoadingFixtures(true)
    getFixtures({ status: 'scheduled', limit: 200, upcoming_only: true })
      .then((res) => {
        setFixtures(res.fixtures)
        if (matchParam) {
          const id = Number(matchParam)
          const found = res.fixtures.find(f => f.id === id)
          if (found) setSelectedFixtureId(id)
        }
      })
      .catch(() => setFixtures([]))
      .finally(() => setLoadingFixtures(false))
  }, [matchParam])

  const fetchPricing = useCallback(async (fixtureId: number) => {
    setLoading(true)
    setError(null)
    try {
      const result = await priceMatch({
        fixture_id: fixtureId,
        home_xg_override: homeXgRef.current ? Number(homeXgRef.current) : null,
        away_xg_override: awayXgRef.current ? Number(awayXgRef.current) : null,
        home_pen_taker_override: homePenTaker,
        away_pen_taker_override: awayPenTaker,
        home_starters: homeStartersRef.current,
        away_starters: awayStartersRef.current,
      })
      setPricing(result)
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Erreur lors du chargement du pricing')
    } finally {
      setLoading(false)
    }
  }, [homePenTaker, awayPenTaker])

  // Auto-fetch only on fixture change or pen taker change — NOT on xG (use button)
  useEffect(() => {
    if (selectedFixtureId !== null) {
      fetchPricing(selectedFixtureId)
    }
  }, [selectedFixtureId, homePenTaker, awayPenTaker, fetchPricing])

  function handleFixtureSelect(e: React.ChangeEvent<HTMLSelectElement>) {
    const id = Number(e.target.value)
    setSelectedFixtureId(id || null)
    setPricing(null)
    setHomeXgOverride('')
    setAwayXgOverride('')
    setHomePenTaker(null)
    setAwayPenTaker(null)
    homeStartersRef.current = null
    awayStartersRef.current = null
  }

  function handleHomePenClick(playerId: number) {
    setHomePenTaker(prev => prev === playerId ? null : playerId)
  }

  function handleAwayPenClick(playerId: number) {
    setAwayPenTaker(prev => prev === playerId ? null : playerId)
  }

  function handleCalculateWithLineup(side: 'home' | 'away', starters: string[]) {
    if (!selectedFixtureId) return
    if (side === 'home') homeStartersRef.current = starters.length >= 5 ? starters : null
    else awayStartersRef.current = starters.length >= 5 ? starters : null
    fetchPricing(selectedFixtureId)
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

      {/* Recalculate button (shown when a match is selected) */}
      {selectedFixtureId && (
        <div className="mb-4">
          <button
            onClick={() => fetchPricing(selectedFixtureId)}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 bg-orange-500 hover:bg-orange-600 disabled:opacity-50 text-white text-sm font-medium rounded-lg transition-colors"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            Recalculer avec ces xG
          </button>
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

      {/* Tables + lineup widgets */}
      {pricing && !loading && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {/* Home */}
          <div>
            <TeamTable
              teamName={pricing.home_team}
              matchXg={pricing.home_match_xg}
              xgSource={pricing.xg_source}
              players={pricing.home_players}
              xgOverride={homeXgOverride}
              onXgOverride={setHomeXgOverride}
              penTakerOverride={homePenTaker}
              onPenTakerClick={handleHomePenClick}
              isHome={true}
            />
            <LineupPricingWidget
              fixtureId={pricing.fixture_id}
              team={pricing.home_team}
              teamPlayers={pricing.home_players}
              lineupPlayers={pricing.home_lineup_players ?? null}
              isHome={true}
              onCalculate={(starters) => handleCalculateWithLineup('home', starters)}
            />
          </div>

          {/* Away */}
          <div>
            <TeamTable
              teamName={pricing.away_team}
              matchXg={pricing.away_match_xg}
              xgSource={pricing.xg_source}
              players={pricing.away_players}
              xgOverride={awayXgOverride}
              onXgOverride={setAwayXgOverride}
              penTakerOverride={awayPenTaker}
              onPenTakerClick={handleAwayPenClick}
              isHome={false}
            />
            <LineupPricingWidget
              fixtureId={pricing.fixture_id}
              team={pricing.away_team}
              teamPlayers={pricing.away_players}
              lineupPlayers={pricing.away_lineup_players ?? null}
              isHome={false}
              onCalculate={(starters) => handleCalculateWithLineup('away', starters)}
            />
          </div>
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

export default function CalculatorPage() {
  return (
    <Suspense>
      <CalculatorInner />
    </Suspense>
  )
}
