'use client'

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useRouter } from 'next/navigation'
import { Plus, Calendar, Trash2, ChevronDown, ChevronRight, Target, Share2 } from 'lucide-react'
import { clsx } from 'clsx'
import type { ReactNode } from 'react'
import { getFixtures, createFixture, deleteFixture, createOdds } from '@/lib/api'
import type { FixtureOut } from '@/lib/api'
import { getTeamId } from '@/lib/teamLogos'

interface Match {
  id: string
  kickoffUtc: string
  homeTeam: string
  awayTeam: string
  homeTeamId: string | null
  awayTeamId: string | null
  homeApiFootballId: number | null
  awayApiFootballId: number | null
  league: string
  matchweek: number | null
  status: 'upcoming' | 'live' | 'finished'
  homeScore?: number
  awayScore?: number
  oddsCount: number
  goalscorerCount: number
  assistCount: number
  // only populated when the user expands the detail
  odds: {
    player: string
    market: 'goalscorer' | 'assist'
    bookmaker: string
    odds: number
  }[]
}

function fixtureToMatch(f: FixtureOut): Match {
  const statusMap: Record<string, Match['status']> = {
    scheduled: 'upcoming',
    live: 'live',
    finished: 'finished',
    postponed: 'upcoming',
  }
  return {
    id: f.id.toString(),
    kickoffUtc: f.kickoff_utc,
    homeTeam: f.home_team,
    awayTeam: f.away_team,
    homeTeamId: f.home_team_id,
    awayTeamId: f.away_team_id,
    homeApiFootballId: f.home_api_football_id,
    awayApiFootballId: f.away_api_football_id,
    league: f.league,
    matchweek: f.matchweek,
    status: statusMap[f.status] ?? 'upcoming',
    homeScore: f.home_score ?? undefined,
    awayScore: f.away_score ?? undefined,
    oddsCount: f.odds_count ?? 0,
    goalscorerCount: f.goalscorer_count ?? 0,
    assistCount: f.assist_count ?? 0,
    odds: f.odds.map(o => ({
      player: o.player_name,
      market: o.market_type as 'goalscorer' | 'assist',
      bookmaker: o.bookmaker,
      odds: o.odds,
    })),
  }
}

const LEAGUE_META: Record<string, { label: string; short: string; color: string }> = {
  ligue_1:          { label: 'Ligue 1',               short: 'L1',  color: 'bg-blue-500/20 text-blue-400 border-blue-500/30' },
  premier_league:   { label: 'Premier League',         short: 'PL',  color: 'bg-purple-500/20 text-purple-400 border-purple-500/30' },
  bundesliga:       { label: 'Bundesliga',             short: 'BL',  color: 'bg-red-500/20 text-red-400 border-red-500/30' },
  la_liga:          { label: 'La Liga',                short: 'LL',  color: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30' },
  serie_a:          { label: 'Serie A',                short: 'SA',  color: 'bg-green-500/20 text-green-400 border-green-500/30' },
  champions_league: { label: 'Ligue des Champions',   short: 'UCL', color: 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30' },
  world_cup_2026:   { label: 'Coupe du Monde 2026',   short: 'CDM', color: 'bg-orange-500/20 text-orange-400 border-orange-500/30' },
}

function formatKickoff(utcStr: string): string {
  const d = new Date(utcStr)
  const day = d.toLocaleDateString('fr-FR', { weekday: 'short', day: 'numeric', month: 'short' })
  const time = d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })
  return `${day} · ${time}`
}

// ── Team logo with initials fallback ───────────────────────────────────────────

function TeamLogo({
  apiFootballId,
  teamName,
  size = 'md',
}: {
  apiFootballId?: number | null
  teamName: string
  size?: 'sm' | 'md' | 'lg'
}) {
  const [imgFailed, setImgFailed] = useState(false)
  const sizeClass = { sm: 'w-8 h-8', md: 'w-12 h-12', lg: 'w-14 h-14' }[size]
  const textClass = { sm: 'text-xs', md: 'text-sm', lg: 'text-base' }[size]

  const initials = teamName
    .split(/[\s\-]+/)
    .filter(Boolean)
    .map(w => w[0])
    .join('')
    .toUpperCase()
    .slice(0, 2)

  // Priority: DB canonical id → static map fallback
  const resolvedId = apiFootballId ?? getTeamId(teamName)

  if (resolvedId && !imgFailed) {
    return (
      <img
        src={`https://media.api-sports.io/football/teams/${resolvedId}.png`}
        alt={teamName}
        className={`${sizeClass} object-contain`}
        onError={() => setImgFailed(true)}
      />
    )
  }

  return (
    <div
      className={`${sizeClass} rounded-full bg-gray-700 border border-gray-600 flex items-center justify-center font-bold text-gray-300 ${textClass} shrink-0`}
    >
      {initials}
    </div>
  )
}

// ── MatchCard ──────────────────────────────────────────────────────────────────

function MatchCard({ match, onDelete }: { match: Match; onDelete: (id: string) => void }) {
  const [expanded, setExpanded] = useState(false)
  const router = useRouter()
  const league = LEAGUE_META[match.league]
  const isFinished = match.status === 'finished'
  const isLive = match.status === 'live'

  return (
    <div className="bg-gray-800 border border-gray-700/50 rounded-xl overflow-hidden hover:border-gray-600/70 transition-colors">

      {/* ── Header: league + date ──────────────────────────────────────── */}
      <div className="flex items-center justify-between px-4 pt-3 pb-2">
        <div className="flex items-center gap-2">
          <span className={clsx(
            'px-2 py-0.5 rounded text-xs font-medium border',
            league?.color ?? 'bg-gray-500/20 text-gray-400 border-gray-500/30',
          )}>
            {league?.short ?? match.league}
          </span>
          {match.matchweek && (
            <span className="text-xs text-gray-500">J{match.matchweek}</span>
          )}
        </div>

        <div className="flex items-center gap-2">
          {isLive && (
            <span className="flex items-center gap-1 text-xs text-green-400 font-semibold animate-pulse">
              <span className="w-1.5 h-1.5 rounded-full bg-green-400 inline-block" />
              EN DIRECT
            </span>
          )}
          <span className="text-xs text-gray-400">{formatKickoff(match.kickoffUtc)}</span>
        </div>
      </div>

      {/* ── Match body: logos + teams + score/vs ──────────────────────── */}
      <div
        className="px-4 pb-3 cursor-pointer"
        onClick={() => router.push(`/dashboard/calculator?match=${match.id}`)}
      >
        <div className="flex items-center justify-between gap-3">

          {/* Home team */}
          <div className="flex flex-col items-center gap-1.5 flex-1 min-w-0">
            <TeamLogo apiFootballId={match.homeApiFootballId} teamName={match.homeTeam} size="lg" />
            <span className="text-sm font-medium text-white text-center leading-tight line-clamp-2">
              {match.homeTeam}
            </span>
          </div>

          {/* Score / VS */}
          <div className="flex flex-col items-center shrink-0 px-2">
            {isFinished && match.homeScore != null && match.awayScore != null ? (
              <div className="flex items-center gap-2">
                <span className="text-3xl font-bold text-white tabular-nums">{match.homeScore}</span>
                <span className="text-lg text-gray-500">—</span>
                <span className="text-3xl font-bold text-white tabular-nums">{match.awayScore}</span>
              </div>
            ) : isLive && match.homeScore != null && match.awayScore != null ? (
              <div className="flex items-center gap-2">
                <span className="text-3xl font-bold text-green-400 tabular-nums">{match.homeScore}</span>
                <span className="text-lg text-green-500/60">—</span>
                <span className="text-3xl font-bold text-green-400 tabular-nums">{match.awayScore}</span>
              </div>
            ) : (
              <span className="text-sm text-gray-500 font-medium tracking-widest">VS</span>
            )}
          </div>

          {/* Away team */}
          <div className="flex flex-col items-center gap-1.5 flex-1 min-w-0">
            <TeamLogo apiFootballId={match.awayApiFootballId} teamName={match.awayTeam} size="lg" />
            <span className="text-sm font-medium text-white text-center leading-tight line-clamp-2">
              {match.awayTeam}
            </span>
          </div>
        </div>
      </div>

      {/* ── Stats footer ──────────────────────────────────────────────── */}
      <div className="border-t border-gray-700/60 px-4 py-2.5 flex items-center justify-between gap-2">
        <div className="flex items-center gap-3 flex-wrap">
          {match.oddsCount > 0 ? (
            <>
              <StatChip
                icon={<Target className="w-3 h-3" />}
                value={match.goalscorerCount}
                label="buteurs"
                color="text-orange-400"
              />
              <StatChip
                icon={<Share2 className="w-3 h-3" />}
                value={match.assistCount}
                label="passeurs"
                color="text-blue-400"
              />
              <span className="text-xs text-gray-500">{match.oddsCount} cotes</span>
            </>
          ) : (
            <span className="text-xs text-gray-600 italic">Aucune cote scrapée</span>
          )}
        </div>

        <div className="flex items-center gap-1 shrink-0">
          {match.oddsCount > 0 && (
            <button
              onClick={(e) => { e.stopPropagation(); setExpanded(!expanded) }}
              className="p-1.5 text-gray-500 hover:text-gray-300 transition-colors"
              title={expanded ? 'Masquer les cotes' : 'Voir les cotes'}
            >
              <ChevronDown className={clsx('w-4 h-4 transition-transform', expanded && 'rotate-180')} />
            </button>
          )}
          <button
            onClick={(e) => { e.stopPropagation(); router.push(`/dashboard/calculator?match=${match.id}`) }}
            className="p-1.5 text-gray-500 hover:text-brand-400 transition-colors"
            title="Ouvrir dans le calculateur"
          >
            <ChevronRight className="w-4 h-4" />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onDelete(match.id) }}
            className="p-1.5 text-gray-500 hover:text-red-400 transition-colors"
            title="Supprimer ce match"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* ── Expandable odds list ───────────────────────────────────────── */}
      {expanded && match.oddsCount > 0 && (
        <div className="border-t border-gray-700/60 px-4 py-3">
          {match.odds.length > 0 ? (
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
              {match.odds.map((odd, i) => (
                <div key={i} className="bg-gray-700/50 rounded-lg p-2">
                  <p className="text-sm text-white font-medium truncate">{odd.player}</p>
                  <div className="flex items-center justify-between mt-1 gap-1">
                    <span className={clsx('text-xs', odd.market === 'goalscorer' ? 'text-orange-400' : 'text-blue-400')}>
                      {odd.market === 'goalscorer' ? 'Buteur' : 'Passeur'}
                    </span>
                    <span className="text-sm text-green-400 font-semibold">{odd.odds.toFixed(2)}</span>
                  </div>
                  <span className="text-xs text-gray-500">{odd.bookmaker}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-gray-500 italic">
              Détail des cotes non chargé — ouvrez le calculateur pour les voir.
            </p>
          )}
        </div>
      )}
    </div>
  )
}

function StatChip({
  icon,
  value,
  label,
  color,
}: {
  icon: ReactNode
  value: number
  label: string
  color: string
}) {
  if (value === 0) return null
  return (
    <span className={clsx('flex items-center gap-1 text-xs font-medium', color)}>
      {icon}
      {value} {label}
    </span>
  )
}

// ── Page ───────────────────────────────────────────────────────────────────────

export default function MatchesPage() {
  const queryClient = useQueryClient()
  const [showAddForm, setShowAddForm] = useState(false)
  const [finishedOpen, setFinishedOpen] = useState(false)

  const { data: upcomingData, isLoading: upcomingLoading } = useQuery({
    queryKey: ['fixtures-upcoming'],
    queryFn: () => getFixtures({ status: 'upcoming', upcoming_only: true, limit: 100 }),
  })

  const { data: finishedData, isLoading: finishedLoading } = useQuery({
    queryKey: ['fixtures-finished'],
    queryFn: () => getFixtures({ status: 'finished', limit: 100 }),
    enabled: finishedOpen,
  })

  const upcomingMatches = (upcomingData?.fixtures ?? []).map(fixtureToMatch)
  const finishedMatches = (finishedData?.fixtures ?? []).map(fixtureToMatch)

  const createMutation = useMutation({
    mutationFn: async (match: Omit<Match, 'id'>) => {
      const d = new Date(match.kickoffUtc)
      const fixture = await createFixture({
        date: d.toISOString().split('T')[0],
        time: `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`,
        home_team: match.homeTeam,
        away_team: match.awayTeam,
        league: match.league,
      })
      for (const odd of match.odds) {
        await createOdds(fixture.id, {
          player_name: odd.player,
          market_type: odd.market,
          bookmaker: odd.bookmaker,
          odds: odd.odds,
        })
      }
      return fixture
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fixtures-upcoming'] })
      queryClient.invalidateQueries({ queryKey: ['fixtures-finished'] })
      setShowAddForm(false)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteFixture(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fixtures-upcoming'] })
      queryClient.invalidateQueries({ queryKey: ['fixtures-finished'] })
    },
  })

  const handleDelete = (id: string) => {
    if (confirm('Supprimer ce match ?')) deleteMutation.mutate(Number(id))
  }

  return (
    <div className="p-4 md:p-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">Matchs</h1>
          <p className="text-gray-400 mt-1">{upcomingMatches.length} matchs à venir</p>
        </div>
        <button
          onClick={() => setShowAddForm(true)}
          className="flex items-center gap-2 px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white rounded-lg transition-colors"
        >
          <Plus className="w-4 h-4" />
          Ajouter match
        </button>
      </div>

      {showAddForm && (
        <MatchForm onSave={(m) => createMutation.mutate(m)} onCancel={() => setShowAddForm(false)} />
      )}

      {/* Matchs à venir */}
      <div className="mb-8">
        <h2 className="text-lg font-semibold text-white mb-4">
          Matchs à venir
          <span className="ml-2 text-sm font-normal text-gray-400">({upcomingMatches.length})</span>
        </h2>
        {upcomingLoading ? (
          <div className="space-y-3">
            {[1, 2, 3].map(i => <div key={i} className="bg-gray-800 rounded-xl h-36 animate-pulse" />)}
          </div>
        ) : upcomingMatches.length === 0 ? (
          <div className="bg-gray-800 rounded-xl p-8 text-center">
            <Calendar className="w-12 h-12 text-gray-600 mx-auto mb-4" />
            <p className="text-gray-400">Aucun match à venir</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
            {upcomingMatches.map(match => (
              <MatchCard key={match.id} match={match} onDelete={handleDelete} />
            ))}
          </div>
        )}
      </div>

      {/* Matchs terminés — collapsible */}
      <div>
        <button
          onClick={() => setFinishedOpen(!finishedOpen)}
          className="flex items-center gap-2 text-gray-400 hover:text-ev-t1 transition-colors mb-4"
        >
          <ChevronDown className={`w-4 h-4 transition-transform ${finishedOpen ? 'rotate-180' : ''}`} />
          <span className="text-sm font-medium">Matchs terminés</span>
        </button>

        {finishedOpen && (
          finishedLoading ? (
            <div className="space-y-3">
              {[1, 2, 3].map(i => <div key={i} className="bg-gray-800 rounded-xl h-36 animate-pulse" />)}
            </div>
          ) : finishedMatches.length === 0 ? (
            <p className="text-sm text-gray-500 italic">Aucun match terminé.</p>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4 opacity-75">
              {finishedMatches.map(match => (
                <MatchCard key={match.id} match={match} onDelete={handleDelete} />
              ))}
            </div>
          )
        )}
      </div>
    </div>
  )
}

// ── MatchForm (unchanged) ──────────────────────────────────────────────────────

function MatchForm({ onSave, onCancel }: { onSave: (m: Omit<Match, 'id'>) => void; onCancel: () => void }) {
  const [data, setData] = useState({
    date: new Date().toISOString().split('T')[0],
    time: '21:00',
    homeTeam: '',
    awayTeam: '',
    league: 'ligue_1',
    status: 'upcoming' as Match['status'],
    oddsCount: 0,
    goalscorerCount: 0,
    assistCount: 0,
    matchweek: null as number | null,
    homeTeamId: null as string | null,
    awayTeamId: null as string | null,
    homeApiFootballId: null as number | null,
    awayApiFootballId: null as number | null,
    odds: [] as Match['odds'],
  })

  const [newOdd, setNewOdd] = useState({
    player: '',
    market: 'goalscorer' as 'goalscorer' | 'assist',
    bookmaker: 'Betclic',
    odds: 2.0,
  })

  const addOdd = () => {
    if (newOdd.player && newOdd.odds > 1) {
      setData({ ...data, odds: [...data.odds, { ...newOdd }] })
      setNewOdd({ ...newOdd, player: '', odds: 2.0 })
    }
  }

  const kickoffUtc = `${data.date}T${data.time}:00+00:00`

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 overflow-auto py-8">
      <div className="bg-gray-800 rounded-xl p-6 w-full max-w-2xl mx-4">
        <h2 className="text-xl font-bold text-white mb-4">Ajouter un match</h2>

        <div className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
              <label className="block text-sm text-gray-400 mb-1">Date</label>
              <input type="date" value={data.date}
                onChange={e => setData({ ...data, date: e.target.value })}
                className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white" />
            </div>
            <div>
              <label className="block text-sm text-gray-400 mb-1">Heure (UTC)</label>
              <input type="time" value={data.time}
                onChange={e => setData({ ...data, time: e.target.value })}
                className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white" />
            </div>
            <div>
              <label className="block text-sm text-gray-400 mb-1">Ligue</label>
              <select value={data.league}
                onChange={e => setData({ ...data, league: e.target.value })}
                className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white">
                {Object.entries(LEAGUE_META).map(([k, v]) => (
                  <option key={k} value={k}>{v.label}</option>
                ))}
              </select>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm text-gray-400 mb-1">Équipe domicile</label>
              <input type="text" value={data.homeTeam}
                onChange={e => setData({ ...data, homeTeam: e.target.value })}
                className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white"
                placeholder="Paris Saint-Germain" />
            </div>
            <div>
              <label className="block text-sm text-gray-400 mb-1">Équipe extérieur</label>
              <input type="text" value={data.awayTeam}
                onChange={e => setData({ ...data, awayTeam: e.target.value })}
                className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white"
                placeholder="Olympique Lyonnais" />
            </div>
          </div>

          <div className="border-t border-gray-700 pt-4">
            <h3 className="text-sm font-medium text-gray-400 mb-3">Cotes bookmaker</h3>
            <div className="flex flex-wrap gap-2 mb-3">
              <input type="text" placeholder="Joueur" value={newOdd.player}
                onChange={e => setNewOdd({ ...newOdd, player: e.target.value })}
                className="flex-1 min-w-0 bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white text-sm" />
              <select value={newOdd.market}
                onChange={e => setNewOdd({ ...newOdd, market: e.target.value as 'goalscorer' | 'assist' })}
                className="bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white text-sm">
                <option value="goalscorer">Buteur</option>
                <option value="assist">Passeur</option>
              </select>
              <select value={newOdd.bookmaker}
                onChange={e => setNewOdd({ ...newOdd, bookmaker: e.target.value })}
                className="bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white text-sm">
                <option>Betclic</option><option>Unibet</option>
                <option>PMU</option><option>Winamax</option>
              </select>
              <input type="number" step="0.05" value={newOdd.odds}
                onChange={e => setNewOdd({ ...newOdd, odds: Number(e.target.value) })}
                className="w-20 bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white text-sm" />
              <button onClick={addOdd}
                className="px-3 py-2 bg-brand-600 hover:bg-brand-700 text-white rounded-lg text-sm">
                +
              </button>
            </div>
            {data.odds.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {data.odds.map((odd, i) => (
                  <div key={i} className="flex items-center gap-2 bg-gray-700 rounded-lg px-3 py-1.5">
                    <span className="text-sm text-white">{odd.player}</span>
                    <span className="text-xs text-gray-400">{odd.market === 'goalscorer' ? 'Buteur' : 'Passeur'}</span>
                    <span className="text-sm text-green-400">{odd.odds}</span>
                    <button onClick={() => setData({ ...data, odds: data.odds.filter((_, j) => j !== i) })}
                      className="text-gray-400 hover:text-red-400">×</button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="flex justify-end gap-3 mt-6">
          <button onClick={onCancel} className="px-4 py-2 text-gray-400 hover:text-ev-t1 transition-colors">
            Annuler
          </button>
          <button
            onClick={() => onSave({ ...data, kickoffUtc })}
            className="px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white rounded-lg transition-colors"
          >
            Ajouter
          </button>
        </div>
      </div>
    </div>
  )
}
