'use client'

import { useState, useEffect, useCallback, useRef, Suspense } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import { Calculator, RefreshCw, ChevronDown } from 'lucide-react'
import { clsx } from 'clsx'
import { getFixtures, priceMatch, getPenTakers, setPenTakers, type FixtureOut, type MatchPriceResponse, type PlayerAllocationOut } from '@/lib/api'
import { LineupPricingWidget } from '@/components/calculator/LineupPricingWidget'
import { XgSourceBadge } from '@/components/XgSourceBadge'
import { getTeamId } from '@/lib/teamLogos'

// ── Helpers ────────────────────────────────────────────────────────

function fmtOdds(o: number): string {
  return o >= 100 ? '—' : o.toFixed(2)
}

function fmtAge(isoTs: string | null | undefined): string {
  if (!isoTs) return 'inconnu'
  const diffMs = Date.now() - new Date(isoTs).getTime()
  const mins = Math.floor(diffMs / 60000)
  if (mins < 60) return `il y a ${mins} min`
  const hours = Math.floor(mins / 60)
  const rem = mins % 60
  return rem > 0 ? `il y a ${hours}h${String(rem).padStart(2, '0')}` : `il y a ${hours}h`
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

// ── Mode d'affichage : probabilités ou cotes (persisté) ────────────
export type ViewMode = 'proba' | 'cote'
const VIEW_MODE_KEY = 'ev0.calculator.viewMode'

function ViewModeSwitch({ mode, onChange }: { mode: ViewMode; onChange: (m: ViewMode) => void }) {
  return (
    <div className="inline-flex rounded-lg border border-gray-600 overflow-hidden text-xs font-medium">
      {(['proba', 'cote'] as ViewMode[]).map((m) => (
        <button
          key={m}
          onClick={() => onChange(m)}
          className={clsx(
            'px-3 py-1.5 transition-colors',
            mode === m
              ? 'bg-orange-500 text-white'
              : 'bg-gray-800 text-gray-400 hover:text-white',
          )}
        >
          {m === 'proba' ? 'Probabilités' : 'Cotes'}
        </button>
      ))}
    </div>
  )
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
  viewMode: ViewMode
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
  viewMode,
}: TeamTableProps) {
  const showProba = viewMode === 'proba'
  const [logoFailed, setLogoFailed] = useState(false)
  const logoId = getTeamId(teamName)

  return (
    <div className="bg-gray-800 rounded-xl overflow-hidden">
      {/* Header */}
      <div className={clsx(
        'px-4 py-3 flex items-center justify-between',
        isHome ? 'bg-orange-500/10 border-b border-orange-500/20' : 'bg-blue-500/10 border-b border-blue-500/20',
      )}>
        <div className="flex items-center gap-2 flex-wrap">
          {logoId && !logoFailed && (
            <img
              src={`https://media.api-sports.io/football/teams/${logoId}.png`}
              alt={teamName}
              className="w-7 h-7 object-contain shrink-0"
              onError={() => setLogoFailed(true)}
            />
          )}
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
          <XgSourceBadge source={xgSource} />
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
              <th className="px-2 py-2 font-medium text-orange-400 border-l border-gray-700">P(sub)</th>
              <th className="px-2 py-2 font-medium text-orange-400">t̄sub</th>
              <th className="px-3 py-2 font-medium text-blue-400 border-l border-gray-700">{showProba ? 'P(but+sub)' : 'C.But+Sub'}</th>
              <th className="px-3 py-2 font-medium text-blue-400">{showProba ? 'P(ass+sub)' : 'C.Ass+Sub'}</th>
              <th className="px-3 py-2 font-medium text-gray-500 border-l border-gray-700">{showProba ? 'P(but)' : 'C.But'}</th>
              <th className="px-3 py-2 font-medium text-gray-500">{showProba ? 'P(ass)' : 'C.Ass'}</th>
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

                  {/* P(sub) */}
                  <td className="px-2 py-2 text-center border-l border-gray-700/50">
                    <span className={clsx(
                      'text-xs font-medium',
                      (p.p_sub ?? 0) > 0.5 ? 'text-orange-400' :
                      (p.p_sub ?? 0) > 0.25 ? 'text-yellow-400' : 'text-gray-500',
                    )}>
                      {((p.p_sub ?? 0) * 100).toFixed(0)}%
                    </span>
                  </td>

                  {/* t̄sub */}
                  <td className="px-2 py-2 text-center text-gray-400 text-xs">
                    {(p.avg_sub_time ?? 65).toFixed(0)}&apos;
                  </td>

                  {/* Supersub but+sub — primaire, bleu */}
                  <td className="px-3 py-2 text-center border-l border-gray-700/50">
                    <span className="font-semibold text-blue-300">
                      {showProba
                        ? `${((p.p_goal_supersub ?? 0) * 100).toFixed(1)}%`
                        : fmtOdds(p.fair_odds_goal_supersub ?? 99)}
                    </span>
                  </td>

                  {/* Supersub ass+sub — primaire, bleu */}
                  <td className="px-3 py-2 text-center">
                    <span className="font-semibold text-blue-300">
                      {showProba
                        ? `${((p.p_assist_supersub ?? 0) * 100).toFixed(1)}%`
                        : fmtOdds(p.fair_odds_assist_supersub ?? 99)}
                    </span>
                  </td>

                  {/* Standard but — secondaire, gris atténué */}
                  <td className="px-3 py-2 text-center border-l border-gray-700/50">
                    <span className={clsx(
                      'text-xs',
                      !showProba && isPenTaker ? 'text-amber-400' : 'text-gray-500',
                    )}>
                      {showProba ? fmtPct(p.prob_goal) : fmtOdds(p.fair_odds_goal)}
                    </span>
                  </td>

                  {/* Standard assist — secondaire, gris atténué */}
                  <td className="px-3 py-2 text-center">
                    <span className="text-gray-500 text-xs">
                      {showProba ? fmtPct(p.prob_assist) : fmtOdds(p.fair_odds_assist)}
                    </span>
                  </td>
                </tr>
              )
            })}
            {players.length === 0 && (
              <tr>
                <td colSpan={11} className="px-4 py-6 text-center text-gray-500 italic">
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
  const router = useRouter()
  const matchParam = searchParams.get('match')

  const [fixtures, setFixtures] = useState<FixtureOut[]>([])
  const [selectedFixtureId, setSelectedFixtureId] = useState<number | null>(null)
  const [pricing, setPricing] = useState<MatchPriceResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [loadingFixtures, setLoadingFixtures] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastScrapedAt, setLastScrapedAt] = useState<string | null>(null)

  // Mode d'affichage proba / cote — persisté entre visites
  const [viewMode, setViewMode] = useState<ViewMode>('cote')
  useEffect(() => {
    const saved = localStorage.getItem(VIEW_MODE_KEY)
    if (saved === 'proba' || saved === 'cote') setViewMode(saved)
  }, [])
  const handleViewModeChange = useCallback((m: ViewMode) => {
    setViewMode(m)
    localStorage.setItem(VIEW_MODE_KEY, m)
  }, [])

  // Overrides
  const [homeXgOverride, setHomeXgOverride] = useState('')
  const [awayXgOverride, setAwayXgOverride] = useState('')
  const [homePenTaker, setHomePenTaker] = useState<number | null>(null)
  const [awayPenTaker, setAwayPenTaker] = useState<number | null>(null)

  // Lineup starters for compo redistribution (sent to priceMatch)
  const homeStartersRef = useRef<string[] | null>(null)
  const awayStartersRef = useRef<string[] | null>(null)

  // Auto-apply BZZ lineup state
  const [lineupAutoApplied, setLineupAutoApplied] = useState<{ home: boolean; away: boolean }>({ home: false, away: false })
  const lastLineupFetchedForRef = useRef<number | null>(null)

  // xG refs so fetchPricing doesn't need them as deps (avoids re-fetch on each keystroke)
  const homeXgRef = useRef(homeXgOverride)
  const awayXgRef = useRef(awayXgOverride)
  useEffect(() => { homeXgRef.current = homeXgOverride }, [homeXgOverride])
  useEffect(() => { awayXgRef.current = awayXgOverride }, [awayXgOverride])

  // Load upcoming + live fixtures, auto-select if ?match= param present
  useEffect(() => {
    setLoadingFixtures(true)
    // Fetch scheduled (upcoming) and live matches together
    Promise.all([
      getFixtures({ status: 'scheduled', limit: 150, upcoming_only: false }),
      getFixtures({ status: 'live', limit: 50 }),
    ])
      .then(([scheduledRes, liveRes]) => {
        // Merge, deduplicate by id, keep live at the top
        const seen = new Set<number>()
        const merged: FixtureOut[] = []
        for (const f of [...liveRes.fixtures, ...scheduledRes.fixtures]) {
          if (!seen.has(f.id)) { seen.add(f.id); merged.push(f) }
        }
        setFixtures(merged)
        if (matchParam) {
          const id = Number(matchParam)
          // Auto-select whether or not the fixture is in the loaded list
          // (user might have a direct link to a fixture that's not yet live)
          setSelectedFixtureId(id)
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
      setLastScrapedAt(result.last_scraped_at ?? null)
    } catch (e: any) {
      const detail = e?.response?.data?.detail
      if (detail && typeof detail === 'object' && detail.message) {
        setError(detail.message)
        setLastScrapedAt(detail.last_scraped_at ?? null)
      } else {
        setError(typeof detail === 'string' ? detail : 'Erreur lors du chargement du pricing')
        setLastScrapedAt(null)
      }
    } finally {
      setLoading(false)
    }
  }, [homePenTaker, awayPenTaker])

  // Auto-fetch pricing. On fixture change, try to auto-apply BZZ lineup first.
  // On pen taker change only, skip lineup re-fetch (starters refs already set).
  useEffect(() => {
    if (selectedFixtureId === null) return

    const run = async () => {
      if (lastLineupFetchedForRef.current !== selectedFixtureId) {
        lastLineupFetchedForRef.current = selectedFixtureId
        try {
          const r = await fetch(`/api/v1/lineups/fixture/${selectedFixtureId}`)
          if (r.ok) {
            const d = await r.json()
            const homeS: string[] = (d.home?.players ?? [])
              .filter((p: { is_starter: boolean; position?: string }) => p.is_starter && p.position !== 'GK')
              .map((p: { player_name: string }) => p.player_name)
            const awayS: string[] = (d.away?.players ?? [])
              .filter((p: { is_starter: boolean; position?: string }) => p.is_starter && p.position !== 'GK')
              .map((p: { player_name: string }) => p.player_name)
            if (homeS.length >= 5) homeStartersRef.current = homeS
            if (awayS.length >= 5) awayStartersRef.current = awayS
            setLineupAutoApplied({ home: homeS.length >= 5, away: awayS.length >= 5 })
          }
        } catch {
          // pas de compo disponible, on price sans
        }
      }
      fetchPricing(selectedFixtureId)
    }
    run()
  }, [selectedFixtureId, homePenTaker, awayPenTaker, fetchPricing])

  function handleFixtureSelect(e: React.ChangeEvent<HTMLSelectElement>) {
    const id = Number(e.target.value)
    // Persist fixture in URL so page reload auto-restores + re-fetches fresh data
    router.replace(id ? `?match=${id}` : '?', { scroll: false })
    setSelectedFixtureId(id || null)
    setPricing(null)
    setHomeXgOverride('')
    setAwayXgOverride('')
    setHomePenTaker(null)
    setAwayPenTaker(null)
    setLastScrapedAt(null)
    setError(null)
    homeStartersRef.current = null
    awayStartersRef.current = null
    setLineupAutoApplied({ home: false, away: false })
    lastLineupFetchedForRef.current = null
    if (id) {
      getPenTakers(id).then(data => {
        setHomePenTaker(data.home_pen_taker_id)
        setAwayPenTaker(data.away_pen_taker_id)
      }).catch(() => {})
    }
  }

  function handleHomePenClick(playerId: number) {
    setHomePenTaker(prev => {
      const next = prev === playerId ? null : playerId
      if (selectedFixtureId) {
        setPenTakers(selectedFixtureId, next, awayPenTaker).catch(() => {})
      }
      return next
    })
  }

  function handleAwayPenClick(playerId: number) {
    setAwayPenTaker(prev => {
      const next = prev === playerId ? null : playerId
      if (selectedFixtureId) {
        setPenTakers(selectedFixtureId, homePenTaker, next).catch(() => {})
      }
      return next
    })
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
      <div className="mb-6 flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <Calculator className="w-6 h-6" />
            Calculateur Ev0
          </h1>
          <p className="text-gray-400 mt-1 text-sm">
            Modèle Top-Down — Team xG → allocation joueurs → Poisson
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <ViewModeSwitch mode={viewMode} onChange={handleViewModeChange} />
          <span className="text-[10px] text-gray-500">Affichage {viewMode === 'proba' ? 'en probabilités' : 'en cotes'}</span>
        </div>
      </div>

      {/* xG source reminder */}
      <div className="mb-5 flex items-center gap-3 px-3 py-2 rounded-lg bg-gray-800/60 border border-gray-700/50 text-xs text-gray-400 w-fit">
        <span className="font-medium text-gray-300">xG source</span>
        <span className="w-px h-4 bg-gray-700" />
        <span className="flex items-center gap-1.5">
          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-semibold bg-blue-500 text-white">API</span>
          Bzzoiro — xG live scrappés
        </span>
        <span className="w-px h-4 bg-gray-700" />
        <span className="flex items-center gap-1.5">
          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-semibold bg-orange-500 text-white">MODEL</span>
          Modèle interne Ev0 (stats historiques)
        </span>
        <span className="w-px h-4 bg-gray-700" />
        <span className="text-gray-500 italic">Basculer via le bouton en haut à droite</span>
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
            {fixtures.filter(f => !/^[WL]\d+|TBD/i.test(f.home_team) && !/^[WL]\d+|TBD/i.test(f.away_team)).map(f => (
              <option key={f.id} value={f.id}>
                {f.status === 'live' ? '🔴 ' : ''}{f.home_team} vs {f.away_team}
                {' · '}
                {new Date(f.kickoff_utc).toLocaleDateString('fr-FR', {
                  day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
                })}
                {' · '}{f.league.replace(/_/g, ' ').toUpperCase()}
              </option>
            ))}
          </select>
          <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
        </div>
      </div>

      {/* Recalculate button (shown when a match is selected) */}
      {selectedFixtureId && (
        <div className="mb-4 flex items-center gap-3">
          <button
            onClick={() => fetchPricing(selectedFixtureId)}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 bg-orange-500 hover:bg-orange-600 disabled:opacity-50 text-white text-sm font-medium rounded-lg transition-colors"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            Recalculer
          </button>
          {pricing?.p00 != null && (
            <span className="text-xs text-gray-400">
              P(0-0) = <span className="font-mono text-white">{(pricing.p00 * 100).toFixed(1)}%</span>
              <span className="text-gray-600 ml-1">(remboursé si 0-0)</span>
            </span>
          )}
        </div>
      )}

      {/* Scrape freshness banner — shown whenever we have the timestamp */}
      {lastScrapedAt && !loading && (
        <div className={clsx(
          'mb-4 flex items-center gap-2 px-3 py-2 rounded-lg border text-xs w-fit',
          error
            ? 'bg-amber-500/10 border-amber-500/30 text-amber-300'
            : 'bg-gray-800/60 border-gray-700/50 text-gray-400',
        )}>
          <span className={error ? 'text-amber-400' : 'text-gray-500'}>⏱</span>
          <span>Dernier scraping des cotes :</span>
          <span className="font-medium text-white">{fmtAge(lastScrapedAt)}</span>
          {error && <span className="text-amber-400">· cotes trop anciennes, calcul impossible</span>}
        </div>
      )}

      {/* Error (only when no lastScrapedAt to show context) */}
      {error && !lastScrapedAt && (
        <div className="mb-6 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-3 text-red-400 text-sm">
          {error}
        </div>
      )}

      {/* Legend */}
      {pricing && !loading && (
        <div className="mb-4 flex items-center gap-4 text-xs text-gray-500 flex-wrap">
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
          {pricing.xg_source === 'bzzoiro' && lastScrapedAt && (
            <>
              <span className="w-px h-3 bg-gray-700" />
              <span className="text-amber-400/80">
                ⚠ xG via Bzzoiro · cotes {fmtAge(lastScrapedAt)}
              </span>
            </>
          )}
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
              viewMode={viewMode}
            />
            <LineupPricingWidget
              fixtureId={pricing.fixture_id}
              team={pricing.home_team}
              teamPlayers={pricing.home_players}
              lineupPlayers={pricing.home_lineup_players ?? null}
              isHome={true}
              autoApplied={lineupAutoApplied.home}
              onCalculate={(starters) => handleCalculateWithLineup('home', starters)}
              viewMode={viewMode}
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
              viewMode={viewMode}
            />
            <LineupPricingWidget
              fixtureId={pricing.fixture_id}
              team={pricing.away_team}
              teamPlayers={pricing.away_players}
              lineupPlayers={pricing.away_lineup_players ?? null}
              isHome={false}
              autoApplied={lineupAutoApplied.away}
              onCalculate={(starters) => handleCalculateWithLineup('away', starters)}
              viewMode={viewMode}
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
