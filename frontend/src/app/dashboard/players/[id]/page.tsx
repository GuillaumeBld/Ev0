'use client'

import { useEffect, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { RefreshCw, ArrowLeft, AlertCircle } from 'lucide-react'
import { clsx } from 'clsx'
import { PlayerDetail, SeasonStatsOut, RecentMatch } from '@/lib/api'
import { PlayerMatchChart } from '@/components/PlayerMatchChart'

function fmt(v: number | null | undefined, decimals = 2): string {
  if (v === null || v === undefined) return '—'
  return v.toFixed(decimals)
}

function pct(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return `${(v * 100).toFixed(1)}%`
}

function fmtInt(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return String(v)
}

function positionColor(pos: string | null | undefined): string {
  switch (pos) {
    case 'F': return 'bg-red-500/20 text-red-400'
    case 'M': return 'bg-green-500/20 text-green-400'
    case 'D': return 'bg-blue-500/20 text-blue-400'
    case 'G': return 'bg-yellow-500/20 text-yellow-400'
    default:   return 'bg-gray-500/20 text-gray-400'
  }
}

function StatCard({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-gray-900 rounded-lg px-3 py-2.5">
      <p className="text-xs text-gray-500 mb-1 truncate">{label}</p>
      <p className={clsx('text-sm font-semibold font-mono', color ?? 'text-white')}>{value}</p>
    </div>
  )
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">{children}</p>
  )
}

function StatsGrid({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3 mb-6">
      {children}
    </div>
  )
}

function AttaqueSection({ s }: { s: SeasonStatsOut }) {
  return (
    <div>
      <SectionTitle>⚽ Attaque</SectionTitle>
      <StatsGrid>
        <StatCard label="xG/90" value={fmt(s.xg_per_90)} color="text-green-400" />
        <StatCard label="xA/90" value={fmt(s.xa_per_90)} color="text-blue-400" />
        <StatCard label="xG total" value={fmt(s.expected_goals)} color="text-green-300" />
        <StatCard label="xA total" value={fmt(s.expected_assists)} color="text-blue-300" />
        <StatCard label="Buts" value={fmtInt(s.goals)} color="text-white" />
        <StatCard label="Passes D." value={fmtInt(s.goal_assist)} color="text-white" />
        <StatCard label="Tirs/90" value={fmt(s.shots_per_90)} />
        <StatCard label="SoT/90" value={fmt(s.shots_on_target_per_90)} color="text-purple-400" />
        <StatCard label="Précision tir" value={pct(s.shot_accuracy)} />
        <StatCard label="xG/tir" value={fmt(s.xg_per_shot)} />
        <StatCard label="Finishing Δ" value={fmt(s.finishing_delta)} color={s.finishing_delta != null && s.finishing_delta > 0 ? 'text-green-400' : 'text-red-400'} />
        <StatCard label="xA Δ" value={fmt(s.xa_delta)} color={s.xa_delta != null && s.xa_delta > 0 ? 'text-green-400' : 'text-red-400'} />
      </StatsGrid>
    </div>
  )
}

function PassesSection({ s }: { s: SeasonStatsOut }) {
  return (
    <div>
      <SectionTitle>🎯 Passes</SectionTitle>
      <StatsGrid>
        <StatCard label="Complétion passes" value={pct(s.pass_completion)} color="text-indigo-400" />
        <StatCard label="Passes clés/90" value={fmt(s.key_pass_per_90)} color="text-indigo-300" />
        <StatCard label="Passes clés" value={fmtInt(s.key_pass)} />
        <StatCard label="Passes totales" value={fmtInt(s.total_pass)} />
        <StatCard label="Passes précises" value={fmtInt(s.accurate_pass)} />
        <StatCard label="Préc. longues balles" value={pct(s.long_ball_accuracy)} />
        <StatCard label="Longues balles (tot.)" value={fmtInt(s.total_long_balls)} />
        <StatCard label="Centres/90" value={fmt(s.accurate_cross_per_90)} />
        <StatCard label="Précision centres" value={pct(s.cross_accuracy)} />
        <StatCard label="Centres totaux" value={fmtInt(s.total_cross)} />
        <StatCard label="Centres précis" value={fmtInt(s.accurate_cross)} />
      </StatsGrid>
    </div>
  )
}

function DefenseSection({ s }: { s: SeasonStatsOut }) {
  return (
    <div>
      <SectionTitle>🛡️ Défense</SectionTitle>
      <StatsGrid>
        <StatCard label="Duels gagnés %" value={pct(s.duel_win_rate)} color="text-orange-400" />
        <StatCard label="Aériens gagnés %" value={pct(s.aerial_win_rate)} color="text-orange-300" />
        <StatCard label="Tacles réussis %" value={pct(s.tackle_success_rate)} color="text-yellow-400" />
        <StatCard label="Tacles/90" value={fmt(s.tackles_per_90)} />
        <StatCard label="Interceptions/90" value={fmt(s.interceptions_per_90)} />
        <StatCard label="Récupérations/90" value={fmt(s.recoveries_per_90)} />
        <StatCard label="Tacles won" value={fmtInt(s.won_tackle)} />
        <StatCard label="Tacles tot." value={fmtInt(s.total_tackle)} />
        <StatCard label="Interceptions" value={fmtInt(s.interception)} />
        <StatCard label="Récupérations" value={fmtInt(s.ball_recovery)} />
        <StatCard label="Duels gagnés" value={fmtInt(s.duel_won)} />
        <StatCard label="Duels perdus" value={fmtInt(s.duel_lost)} />
      </StatsGrid>
    </div>
  )
}

function FormeSection({ s }: { s: SeasonStatsOut }) {
  const trend = s.rating_trend
  const trendColor = trend == null ? 'text-gray-400' : trend > 0 ? 'text-green-400' : trend < 0 ? 'text-red-400' : 'text-gray-400'
  const trendArrow = trend == null ? '—' : trend > 0.05 ? '↑' : trend < -0.05 ? '↓' : '→'

  return (
    <div>
      <SectionTitle>📈 Forme (5 derniers matchs)</SectionTitle>
      <StatsGrid>
        <StatCard label="xG form" value={fmt(s.form_xg_5)} color="text-green-400" />
        <StatCard label="Rating form" value={fmt(s.form_rating_5, 1)} color="text-amber-400" />
        <StatCard label="Buts form" value={fmtInt(s.form_goals_5)} />
        <StatCard label="Passes D. form" value={fmtInt(s.form_assists_5)} />
        <StatCard label="Tendance rating" value={trendArrow} color={trendColor} />
        <StatCard label="Rating moyen" value={fmt(s.avg_rating, 1)} color="text-amber-400" />
      </StatsGrid>
    </div>
  )
}

function MatchHistoryTable({ matches }: { matches: RecentMatch[] }) {
  return (
    <div>
      <SectionTitle>📋 Historique match par match</SectionTitle>
      <div className="overflow-x-auto">
        <table className="w-full text-xs whitespace-nowrap">
          <thead>
            <tr className="border-b border-gray-700">
              {['Date','Adv','Mins','Buts','PD','xG','xA','Tirs','SoT','Passes','%Pass','KP','LB','Ctr','Duel+','Duel-','Aér+','Tacles','Inter','Récup','J','R','Fautes','Subies','Rating'].map((h) => (
                <th key={h} className="py-2 px-2 text-gray-500 font-medium text-right first:text-left">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matches.map((m) => (
              <tr key={m.event_api_id} className="border-b border-gray-700/30 hover:bg-gray-700/20 transition-colors">
                <td className="py-1.5 px-2 text-gray-400 font-mono">
                  {m.event_date ? new Date(m.event_date).toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' }) : '—'}
                </td>
                <td className="py-1.5 px-2 text-gray-300 min-w-[100px]">
                  <span className="text-gray-500">{m.is_home ? 'vs ' : '@ '}</span>{m.opponent ?? '—'}
                </td>
                <td className="py-1.5 px-2 text-right font-mono text-gray-400">{m.minutes_played ?? '—'}</td>
                <td className={clsx('py-1.5 px-2 text-right font-mono font-semibold', (m.goals ?? 0) > 0 ? 'text-green-400' : 'text-gray-400')}>{m.goals ?? 0}</td>
                <td className={clsx('py-1.5 px-2 text-right font-mono', (m.goal_assist ?? 0) > 0 ? 'text-blue-400' : 'text-gray-400')}>{m.goal_assist ?? 0}</td>
                <td className="py-1.5 px-2 text-right font-mono text-green-400">{fmt(m.expected_goals)}</td>
                <td className="py-1.5 px-2 text-right font-mono text-blue-400">{fmt(m.expected_assists)}</td>
                <td className="py-1.5 px-2 text-right font-mono text-gray-300">{m.total_shots ?? '—'}</td>
                <td className="py-1.5 px-2 text-right font-mono text-gray-300">{m.shots_on_target ?? '—'}</td>
                <td className="py-1.5 px-2 text-right font-mono text-gray-400">{m.total_pass ?? '—'}</td>
                <td className="py-1.5 px-2 text-right font-mono text-gray-400">{pct(m.pass_completion)}</td>
                <td className="py-1.5 px-2 text-right font-mono text-gray-300">{m.key_pass ?? '—'}</td>
                <td className="py-1.5 px-2 text-right font-mono text-gray-400">{m.total_long_balls ?? '—'}</td>
                <td className="py-1.5 px-2 text-right font-mono text-gray-400">{m.total_cross ?? '—'}</td>
                <td className="py-1.5 px-2 text-right font-mono text-gray-400">{m.duel_won ?? '—'}</td>
                <td className="py-1.5 px-2 text-right font-mono text-gray-400">{m.duel_lost ?? '—'}</td>
                <td className="py-1.5 px-2 text-right font-mono text-gray-400">{m.aerial_won ?? '—'}</td>
                <td className="py-1.5 px-2 text-right font-mono text-gray-400">{m.won_tackle ?? '—'}</td>
                <td className="py-1.5 px-2 text-right font-mono text-gray-400">{m.interception ?? '—'}</td>
                <td className="py-1.5 px-2 text-right font-mono text-gray-400">{m.ball_recovery ?? '—'}</td>
                <td className={clsx('py-1.5 px-2 text-right font-mono', (m.yellow_card ?? 0) > 0 ? 'text-yellow-400' : 'text-gray-600')}>{m.yellow_card ?? 0}</td>
                <td className={clsx('py-1.5 px-2 text-right font-mono', (m.red_card ?? 0) > 0 ? 'text-red-400' : 'text-gray-600')}>{m.red_card ?? 0}</td>
                <td className="py-1.5 px-2 text-right font-mono text-gray-400">{m.fouls ?? '—'}</td>
                <td className="py-1.5 px-2 text-right font-mono text-gray-400">{m.was_fouled ?? '—'}</td>
                <td className={clsx('py-1.5 px-2 text-right font-mono',
                  m.rating != null ? m.rating >= 7.5 ? 'text-amber-400 font-semibold' : m.rating >= 7.0 ? 'text-yellow-400' : 'text-gray-300' : 'text-gray-600'
                )}>
                  {fmt(m.rating, 1)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default function PlayerDetailPage() {
  const { id } = useParams<{ id: string }>()
  const router = useRouter()

  const [player, setPlayer] = useState<PlayerDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    setLoading(true)
    setError(null)
    fetch(`/api/v1/players/${id}?season=2025-2026`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json()
      })
      .then((data: PlayerDetail) => setPlayer(data))
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false))
  }, [id])

  const age = (dob: string | null) => {
    if (!dob) return null
    return new Date().getFullYear() - new Date(dob).getFullYear()
  }

  const formatMarketValue = (v: number | null) => {
    if (!v) return null
    return v >= 1_000_000 ? `${parseFloat((v / 1_000_000).toFixed(1))}M €` : `${(v / 1_000).toFixed(0)}K €`
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <RefreshCw className="w-8 h-8 text-brand-500 animate-spin" />
      </div>
    )
  }

  if (error || !player) {
    return (
      <div className="p-8">
        <button onClick={() => router.back()} className="flex items-center gap-2 text-gray-400 hover:text-white mb-6 text-sm transition-colors">
          <ArrowLeft className="w-4 h-4" /> Retour
        </button>
        <div className="bg-red-900/50 border border-red-500 rounded-lg p-4 flex items-center gap-2">
          <AlertCircle className="w-4 h-4 text-red-400" />
          <p className="text-red-300 text-sm">{error ?? 'Joueur introuvable'}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="p-4 md:p-8 max-w-7xl">
      {/* Back */}
      <button
        onClick={() => router.back()}
        className="flex items-center gap-2 text-gray-400 hover:text-white mb-6 text-sm transition-colors"
      >
        <ArrowLeft className="w-4 h-4" /> Retour aux joueurs
      </button>

      {/* Header joueur */}
      <div className="flex flex-wrap gap-4 items-start mb-8">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap mb-1">
            <h1 className="text-2xl font-bold text-white">{player.name}</h1>
            {player.position && (
              <span className={clsx('px-2 py-0.5 rounded text-sm font-medium', positionColor(player.position))}>
                {player.position}
              </span>
            )}
          </div>
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-sm text-gray-400">
            {player.team_name && <span className="text-gray-300 font-medium">{player.team_name}</span>}
            {player.nationality && <span>{player.nationality}</span>}
            {player.date_of_birth && age(player.date_of_birth) && <span>{age(player.date_of_birth)} ans</span>}
            {player.height && <span>{player.height} cm</span>}
            {player.jersey_number && <span>#{player.jersey_number}</span>}
          </div>
        </div>
        {player.market_value && (
          <div className="text-right">
            <p className="text-xs text-gray-500">Valeur marchande</p>
            <p className="text-lg font-bold text-white">{formatMarketValue(player.market_value)}</p>
          </div>
        )}
      </div>

      {/* Stats saison */}
      {player.season_stats ? (
        <div className="space-y-2">
          <AttaqueSection s={player.season_stats} />
          <PassesSection s={player.season_stats} />
          <DefenseSection s={player.season_stats} />
          <FormeSection s={player.season_stats} />
        </div>
      ) : (
        <div className="bg-gray-800 rounded-xl p-6 mb-6 text-center text-gray-400 text-sm">
          Pas de stats saison disponibles pour 2025-2026.
        </div>
      )}

      {/* Graphique */}
      {player.recent_matches.length > 0 && (
        <div className="mb-6">
          <SectionTitle>📊 Graphique match par match</SectionTitle>
          <div className="bg-gray-800 rounded-xl p-4">
            <PlayerMatchChart matches={player.recent_matches} metric="xg" />
          </div>
        </div>
      )}

      {/* Historique */}
      {player.recent_matches.length > 0 && (
        <div className="bg-gray-800 rounded-xl p-4">
          <MatchHistoryTable matches={player.recent_matches} />
        </div>
      )}
    </div>
  )
}
