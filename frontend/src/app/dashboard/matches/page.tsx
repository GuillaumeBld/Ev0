'use client'

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Calendar, Clock, Trash2, ChevronRight } from 'lucide-react'
import { clsx } from 'clsx'
import Link from 'next/link'
import { getFixtures, createFixture, deleteFixture } from '@/lib/api'
import type { FixtureOut } from '@/lib/api'

interface Match {
  id: string
  date: string
  time: string
  homeTeam: string
  awayTeam: string
  league: 'ligue1' | 'premier_league'
  status: 'upcoming' | 'live' | 'finished'
  homeScore?: number
  awayScore?: number
  odds: {
    player: string
    market: 'goalscorer' | 'assist'
    bookmaker: string
    odds: number
  }[]
}

function fixtureToMatch(f: FixtureOut): Match {
  const kickoff = new Date(f.kickoff_utc)
  const statusMap: Record<string, Match['status']> = {
    scheduled: 'upcoming',
    live: 'live',
    finished: 'finished',
    postponed: 'upcoming',
  }
  return {
    id: f.id.toString(),
    date: kickoff.toISOString().split('T')[0],
    time: kickoff.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' }),
    homeTeam: f.home_team,
    awayTeam: f.away_team,
    league: f.league as Match['league'],
    status: statusMap[f.status] || 'upcoming',
    homeScore: f.home_score ?? undefined,
    awayScore: f.away_score ?? undefined,
    odds: f.odds.map(o => ({
      player: o.player_name,
      market: o.market_type as 'goalscorer' | 'assist',
      bookmaker: o.bookmaker,
      odds: o.odds,
    })),
  }
}

export default function MatchesPage() {
  const queryClient = useQueryClient()
  const [showAddForm, setShowAddForm] = useState(false)
  const [filter, setFilter] = useState<'all' | 'upcoming' | 'finished'>('upcoming')

  const { data, isLoading } = useQuery({
    queryKey: ['fixtures', filter],
    queryFn: () => getFixtures(filter !== 'all' ? { status: filter } : {}),
  })

  const matches = (data?.fixtures || []).map(fixtureToMatch)

  const createMutation = useMutation({
    mutationFn: (match: Omit<Match, 'id'>) =>
      createFixture({
        date: match.date,
        time: match.time,
        home_team: match.homeTeam,
        away_team: match.awayTeam,
        league: match.league,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fixtures'] })
      setShowAddForm(false)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteFixture(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fixtures'] })
    },
  })

  const handleAdd = (match: Omit<Match, 'id'>) => {
    createMutation.mutate(match)
  }

  const handleDelete = (id: string) => {
    if (confirm('Supprimer ce match ?')) {
      deleteMutation.mutate(Number(id))
    }
  }

  return (
    <div className="p-4 md:p-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">Matchs</h1>
          <p className="text-gray-400 mt-1">{matches.length} matchs enregistrés</p>
        </div>
        <button
          onClick={() => setShowAddForm(true)}
          className="flex items-center gap-2 px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white rounded-lg transition-colors"
        >
          <Plus className="w-4 h-4" />
          Ajouter match
        </button>
      </div>

      {/* Filter */}
      <div className="flex gap-2 mb-6">
        {(['upcoming', 'finished', 'all'] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={clsx(
              'px-4 py-2 rounded-lg text-sm transition-colors',
              filter === f
                ? 'bg-brand-600 text-white'
                : 'bg-gray-800 text-gray-400 hover:text-white'
            )}
          >
            {f === 'upcoming' ? 'À venir' : f === 'finished' ? 'Terminés' : 'Tous'}
          </button>
        ))}
      </div>

      {/* Add Form Modal */}
      {showAddForm && (
        <MatchForm onSave={handleAdd} onCancel={() => setShowAddForm(false)} />
      )}

      {/* Loading */}
      {isLoading && (
        <div className="space-y-4">
          {[1, 2].map((i) => (
            <div key={i} className="bg-gray-800 rounded-xl h-24 animate-pulse" />
          ))}
        </div>
      )}

      {/* Matches Grid */}
      {!isLoading && (
        <div className="space-y-4">
          {matches.map((match) => (
            <MatchCard key={match.id} match={match} onDelete={handleDelete} />
          ))}
          {matches.length === 0 && (
            <div className="bg-gray-800 rounded-xl p-8 text-center">
              <Calendar className="w-12 h-12 text-gray-600 mx-auto mb-4" />
              <p className="text-gray-400">Aucun match trouvé</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function MatchCard({ match, onDelete }: { match: Match; onDelete: (id: string) => void }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="bg-gray-800 rounded-xl overflow-hidden">
      <div className="p-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="flex flex-col sm:flex-row sm:items-center gap-3">
            {/* Date/Time */}
            <div className="text-left sm:text-center sm:w-20">
              <p className="text-sm text-gray-400">{match.date}</p>
              <p className="text-lg font-bold text-white">{match.time}</p>
            </div>

            {/* Teams */}
            <div className="flex items-center gap-3">
              <span className="text-white font-medium text-right">{match.homeTeam}</span>
              {match.status === 'finished' ? (
                <span className="text-2xl font-bold text-white px-3">
                  {match.homeScore} - {match.awayScore}
                </span>
              ) : (
                <span className="text-gray-500 px-3">vs</span>
              )}
              <span className="text-white font-medium">{match.awayTeam}</span>
            </div>

            {/* League Badge + Status */}
            <div className="flex flex-wrap gap-2">
              <span className={clsx(
                'px-2 py-1 rounded text-xs',
                match.league === 'ligue1' ? 'bg-blue-500/20 text-blue-400' : 'bg-purple-500/20 text-purple-400'
              )}>
                {match.league === 'ligue1' ? 'L1' : 'PL'}
              </span>

              <span className={clsx(
                'px-2 py-1 rounded text-xs',
                match.status === 'upcoming' ? 'bg-yellow-500/20 text-yellow-400' :
                match.status === 'live' ? 'bg-green-500/20 text-green-400 animate-pulse' :
                'bg-gray-500/20 text-gray-400'
              )}>
                {match.status === 'upcoming' ? 'À venir' : match.status === 'live' ? 'En cours' : 'Terminé'}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-sm text-gray-400">
              {match.odds.length} cotes
            </span>
            <Link
              href={`/dashboard/calculator?match=${match.id}`}
              className="p-2 text-brand-400 hover:text-brand-300 transition-colors"
              title="Calculer pricing"
            >
              <ChevronRight className="w-5 h-5" />
            </Link>
            <button
              onClick={() => onDelete(match.id)}
              className="p-2 text-gray-400 hover:text-red-400 transition-colors"
            >
              <Trash2 className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Odds Preview */}
        {match.odds.length > 0 && (
          <div className="mt-4 pt-4 border-t border-gray-700">
            <button
              onClick={() => setExpanded(!expanded)}
              className="text-sm text-gray-400 hover:text-white transition-colors"
            >
              {expanded ? 'Masquer les cotes' : 'Voir les cotes'}
            </button>

            {expanded && (
              <div className="mt-3 grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
                {match.odds.map((odd, i) => (
                  <div key={i} className="bg-gray-700/50 rounded-lg p-2">
                    <p className="text-sm text-white font-medium">{odd.player}</p>
                    <div className="flex items-center justify-between mt-1">
                      <span className={clsx(
                        'text-xs',
                        odd.market === 'goalscorer' ? 'text-orange-400' : 'text-blue-400'
                      )}>
                        {odd.market === 'goalscorer' ? 'Buteur' : 'Passeur'}
                      </span>
                      <span className="text-sm text-green-400 font-medium">{odd.odds.toFixed(2)}</span>
                      <span className="text-xs text-gray-500">{odd.bookmaker}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function MatchForm({ onSave, onCancel }: { onSave: (m: Omit<Match, 'id'>) => void; onCancel: () => void }) {
  const [data, setData] = useState({
    date: new Date().toISOString().split('T')[0],
    time: '21:00',
    homeTeam: '',
    awayTeam: '',
    league: 'ligue1' as Match['league'],
    status: 'upcoming' as Match['status'],
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

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 overflow-auto py-8">
      <div className="bg-gray-800 rounded-xl p-6 w-full max-w-2xl mx-4">
        <h2 className="text-xl font-bold text-white mb-4">Ajouter un match</h2>

        <div className="space-y-4">
          {/* Date & Time */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
              <label className="block text-sm text-gray-400 mb-1">Date</label>
              <input
                type="date"
                value={data.date}
                onChange={(e) => setData({ ...data, date: e.target.value })}
                className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-400 mb-1">Heure</label>
              <input
                type="time"
                value={data.time}
                onChange={(e) => setData({ ...data, time: e.target.value })}
                className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-400 mb-1">Ligue</label>
              <select
                value={data.league}
                onChange={(e) => setData({ ...data, league: e.target.value as Match['league'] })}
                className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white"
              >
                <option value="ligue1">Ligue 1</option>
                <option value="premier_league">Premier League</option>
              </select>
            </div>
          </div>

          {/* Teams */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm text-gray-400 mb-1">Équipe domicile</label>
              <input
                type="text"
                value={data.homeTeam}
                onChange={(e) => setData({ ...data, homeTeam: e.target.value })}
                className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white"
                placeholder="Paris Saint-Germain"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-400 mb-1">Équipe extérieur</label>
              <input
                type="text"
                value={data.awayTeam}
                onChange={(e) => setData({ ...data, awayTeam: e.target.value })}
                className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white"
                placeholder="Olympique Lyon"
              />
            </div>
          </div>

          {/* Odds Section */}
          <div className="border-t border-gray-700 pt-4">
            <h3 className="text-sm font-medium text-gray-400 mb-3">Cotes bookmaker</h3>

            {/* Add Odd Form */}
            <div className="flex flex-wrap gap-2 mb-3">
              <input
                type="text"
                placeholder="Joueur"
                value={newOdd.player}
                onChange={(e) => setNewOdd({ ...newOdd, player: e.target.value })}
                className="flex-1 min-w-0 bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white text-sm"
              />
              <select
                value={newOdd.market}
                onChange={(e) => setNewOdd({ ...newOdd, market: e.target.value as 'goalscorer' | 'assist' })}
                className="bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white text-sm"
              >
                <option value="goalscorer">Buteur</option>
                <option value="assist">Passeur</option>
              </select>
              <select
                value={newOdd.bookmaker}
                onChange={(e) => setNewOdd({ ...newOdd, bookmaker: e.target.value })}
                className="bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white text-sm"
              >
                <option>Betclic</option>
                <option>Unibet</option>
                <option>PMU</option>
                <option>Winamax</option>
              </select>
              <input
                type="number"
                step="0.05"
                value={newOdd.odds}
                onChange={(e) => setNewOdd({ ...newOdd, odds: Number(e.target.value) })}
                className="w-20 bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white text-sm"
              />
              <button
                onClick={addOdd}
                className="px-3 py-2 bg-brand-600 hover:bg-brand-700 text-white rounded-lg text-sm"
              >
                +
              </button>
            </div>

            {/* Odds List */}
            {data.odds.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {data.odds.map((odd, i) => (
                  <div key={i} className="flex items-center gap-2 bg-gray-700 rounded-lg px-3 py-1.5">
                    <span className="text-sm text-white">{odd.player}</span>
                    <span className="text-xs text-gray-400">{odd.market === 'goalscorer' ? 'Buteur' : 'Passeur'}</span>
                    <span className="text-sm text-green-400">{odd.odds}</span>
                    <button
                      onClick={() => setData({ ...data, odds: data.odds.filter((_, j) => j !== i) })}
                      className="text-gray-400 hover:text-red-400"
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="flex justify-end gap-3 mt-6">
          <button onClick={onCancel} className="px-4 py-2 text-gray-400 hover:text-white transition-colors">
            Annuler
          </button>
          <button
            onClick={() => onSave(data)}
            className="px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white rounded-lg transition-colors"
          >
            Ajouter
          </button>
        </div>
      </div>
    </div>
  )
}
