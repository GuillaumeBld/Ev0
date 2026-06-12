'use client'

import { useState } from 'react'
import { clsx } from 'clsx'
import { type WCMatchDetail, type WCIncident } from '@/lib/api'
import { ShotMap } from './ShotMap'

type DetailTab = 'incidents' | 'stats' | 'shotmap'

// ── Incident row ──────────────────────────────────────────────────────────────

function minuteStr(inc: WCIncident): string {
  const m = inc.minute ?? 0
  return inc.added_time ? `${m}+${inc.added_time}'` : `${m}'`
}

function IncidentRow({ inc, homeTeam, awayTeam }: {
  inc: WCIncident; homeTeam: string; awayTeam: string
}) {
  const isHome = inc.is_home

  if (inc.type === 'period') {
    return (
      <div className="flex justify-center py-2">
        <span className="text-xs text-gray-500 bg-gray-800 px-3 py-1 rounded uppercase tracking-wider">
          {inc.text}
        </span>
      </div>
    )
  }

  if (inc.type === 'injuryTime') {
    return (
      <div className="flex justify-center py-1">
        <span className="text-xs text-gray-600">+{inc.length}&apos; temps additionnel</span>
      </div>
    )
  }

  if (inc.type === 'goal') {
    return (
      <div className={clsx('flex items-center gap-3 py-1.5', isHome ? 'flex-row' : 'flex-row-reverse')}>
        <span className="text-sm text-gray-500 w-12 shrink-0 text-center">{minuteStr(inc)}</span>
        <div className={clsx('flex items-center gap-1.5', isHome ? 'flex-row' : 'flex-row-reverse')}>
          <span className="text-green-400 text-base">⚽</span>
          <div className={clsx('text-sm', isHome ? 'text-left' : 'text-right')}>
            <span className="text-white font-medium">
              {inc.is_own_goal ? `${inc.player} (CSC)` : inc.player}
            </span>
            {inc.assist && (
              <span className="text-gray-500 ml-1.5 text-xs">ass. {inc.assist}</span>
            )}
          </div>
        </div>
        <div className="ml-auto mr-auto" />
      </div>
    )
  }

  if (inc.type === 'substitution') {
    return (
      <div className={clsx('flex items-center gap-2 py-1 text-sm', isHome ? 'flex-row' : 'flex-row-reverse')}>
        <span className="text-gray-600 w-12 shrink-0 text-center">{minuteStr(inc)}</span>
        <span className="text-green-600">↑</span>
        <span className="text-gray-400">{inc.player_in}</span>
        <span className="text-red-600">↓</span>
        <span className="text-gray-500">{inc.player_out}</span>
      </div>
    )
  }

  if (inc.type === 'card') {
    const emoji = inc.card_type === 'yellow' ? '🟨' : '🟥'
    return (
      <div className={clsx('flex items-center gap-2 py-1 text-sm', isHome ? 'flex-row' : 'flex-row-reverse')}>
        <span className="text-gray-600 w-12 shrink-0 text-center">{minuteStr(inc)}</span>
        <span>{emoji}</span>
        <span className="text-gray-300">{inc.player}</span>
      </div>
    )
  }

  return null
}

// ── xG timeline ──────────────────────────────────────────────────────────────

function XgTimeline({ data, homeTeam, awayTeam, homeXg, awayXg }: {
  data: WCMatchDetail['xg_per_minute']
  homeTeam: string
  awayTeam: string
  homeXg: number | null
  awayXg: number | null
}) {
  if (!data || data.length === 0) {
    return <p className="text-gray-600 text-sm text-center py-4">Pas de données xG</p>
  }

  const maxXg = Math.max(...data.map(d => Math.max(d.cum_home, d.cum_away)), 0.1)
  const maxMin = Math.max(...data.map(d => d.m), 90)
  const W = 400
  const H = 120
  const pad = { l: 32, r: 10, t: 10, b: 24 }
  const innerW = W - pad.l - pad.r
  const innerH = H - pad.t - pad.b

  const xScale = (m: number) => pad.l + (m / maxMin) * innerW
  const yScale = (v: number) => pad.t + innerH - (v / maxXg) * innerH

  const homePath = data.map((d, i) =>
    `${i === 0 ? 'M' : 'L'}${xScale(d.m)},${yScale(d.cum_home)}`
  ).join(' ')
  const awayPath = data.map((d, i) =>
    `${i === 0 ? 'M' : 'L'}${xScale(d.m)},${yScale(d.cum_away)}`
  ).join(' ')

  const ticks = [0, 15, 30, 45, 60, 75, 90].filter(t => t <= maxMin)

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" style={{ maxHeight: 140 }}>
        {[0.5, 1.0, 1.5, 2.0].filter(v => v <= maxXg).map(v => (
          <line key={v}
            x1={pad.l} x2={W - pad.r}
            y1={yScale(v)} y2={yScale(v)}
            stroke="#1f2937" strokeWidth={0.5} />
        ))}
        <line x1={pad.l} x2={W - pad.r} y1={yScale(0)} y2={yScale(0)} stroke="#374151" strokeWidth={0.5} />
        <path d={homePath} fill="none" stroke="#3b82f6" strokeWidth={2} />
        <path d={awayPath} fill="none" stroke="#ef4444" strokeWidth={2} />
        {ticks.map(t => (
          <text key={t} x={xScale(t)} y={H - 6} textAnchor="middle" fontSize={9} fill="#4b5563">
            {t}&apos;
          </text>
        ))}
        <text x={4} y={pad.t + 4} fontSize={9} fill="#4b5563">xG</text>
      </svg>

      <div className="flex justify-center gap-8 text-xs mt-2">
        <span className="flex items-center gap-1.5">
          <span className="w-6 h-0.5 bg-blue-500 inline-block" />
          <span className="text-blue-400">{homeTeam} ({homeXg?.toFixed(2) ?? '—'})</span>
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-6 h-0.5 bg-red-500 inline-block" />
          <span className="text-red-400">{awayTeam} ({awayXg?.toFixed(2) ?? '—'})</span>
        </span>
      </div>
    </div>
  )
}

// ── Player stats table ────────────────────────────────────────────────────────

function PlayerStatsTable({ players, teamId, teamName }: {
  players: WCMatchDetail['player_stats']
  teamId: number | null
  teamName: string
}) {
  const teamPlayers = players
    .filter(p => p.team_id === teamId)
    .sort((a, b) => (b.minutes_played ?? 0) - (a.minutes_played ?? 0))

  if (teamPlayers.length === 0) {
    return <p className="text-gray-600 text-sm py-2 text-center">Pas de données</p>
  }

  return (
    <div>
      <div className="text-xs font-bold text-gray-400 uppercase tracking-wider mb-2">{teamName}</div>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-700 text-gray-500 text-xs uppercase">
            <th className="text-left py-1 pr-2">Joueur</th>
            <th className="text-right pr-2">Min</th>
            <th className="text-right pr-2">Note</th>
            <th className="text-right pr-2">B</th>
            <th className="text-right pr-2">Pa</th>
            <th className="text-right pr-2">xG</th>
            <th className="text-right pr-2">xA</th>
            <th className="text-right pr-2">Tirs</th>
            <th className="text-right">Cts</th>
          </tr>
        </thead>
        <tbody>
          {teamPlayers.map(p => {
            const ratingColor =
              (p.rating ?? 0) >= 8 ? 'text-emerald-400' :
              (p.rating ?? 0) >= 7 ? 'text-yellow-300' :
              (p.rating ?? 0) >= 6 ? 'text-gray-300' :
              'text-red-400'
            return (
              <tr key={p.player_id} className="border-b border-gray-800/40 hover:bg-gray-800/20">
                <td className="py-1 pr-2 text-gray-200 truncate max-w-[160px]">
                  {p.player_name ?? `#${p.player_id}`}
                  {p.yellow_card ? ' 🟨' : ''}
                  {p.red_card ? ' 🟥' : ''}
                </td>
                <td className="text-right pr-2 text-gray-500">{p.minutes_played ?? '—'}</td>
                <td className={clsx('text-right pr-2 font-semibold', ratingColor)}>
                  {p.rating?.toFixed(1) ?? '—'}
                </td>
                <td className="text-right pr-2 text-white font-bold">{p.goals || '—'}</td>
                <td className="text-right pr-2 text-orange-400">{p.goal_assist || '—'}</td>
                <td className="text-right pr-2 text-blue-400">{p.expected_goals?.toFixed(2) ?? '—'}</td>
                <td className="text-right pr-2 text-purple-400">{p.expected_assists?.toFixed(2) ?? '—'}</td>
                <td className="text-right pr-2 text-gray-400">
                  {p.total_shots ? `${p.shots_on_target ?? 0}/${p.total_shots}` : '—'}
                </td>
                <td className="text-right text-gray-500">
                  {(p.duel_won !== null && p.duel_lost !== null)
                    ? `${p.duel_won}/${(p.duel_won ?? 0) + (p.duel_lost ?? 0)}`
                    : '—'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface MatchDetailPanelProps {
  match: WCMatchDetail
  onClose: () => void
}

export function MatchDetailPanel({ match, onClose }: MatchDetailPanelProps) {
  const [tab, setTab] = useState<DetailTab>('incidents')
  const [shotSide, setShotSide] = useState<'both' | 'home' | 'away'>('both')

  const goals = match.incidents.filter(i => i.type === 'goal')

  return (
    <div className="bg-gray-900 border border-gray-700 rounded-xl overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 border-b border-gray-700 bg-gray-800/60">
        <div className="flex items-center justify-between">
          <div className="text-xs text-gray-500 uppercase tracking-wider">
            {match.group_name ?? `Journée ${match.round_number}`}
            {match.period && match.status !== 'finished' && (
              <span className="ml-2 text-orange-400">{match.period}</span>
            )}
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white text-2xl leading-none">×</button>
        </div>

        {/* Score */}
        <div className="flex items-center justify-between mt-3 gap-6">
          <div className="flex-1 text-right">
            <div className="text-lg font-semibold text-white">{match.home_team}</div>
            {match.home_xg !== null && (
              <div className="text-xs text-blue-400/80 mt-0.5">xG {match.home_xg.toFixed(2)}</div>
            )}
          </div>

          <div className="flex flex-col items-center shrink-0">
            {match.home_score !== null ? (
              <>
                <div className="text-4xl font-bold text-white tabular-nums">
                  {match.home_score} – {match.away_score}
                </div>
                {match.home_score_ht !== null && (
                  <div className="text-xs text-gray-600 mt-1">
                    MT : {match.home_score_ht} – {match.away_score_ht}
                  </div>
                )}
              </>
            ) : (
              <div className="text-2xl text-gray-600">vs</div>
            )}
          </div>

          <div className="flex-1 text-left">
            <div className="text-lg font-semibold text-white">{match.away_team}</div>
            {match.away_xg !== null && (
              <div className="text-xs text-red-400/80 mt-0.5">xG {match.away_xg.toFixed(2)}</div>
            )}
          </div>
        </div>

        {/* Quick goal summary */}
        {goals.length > 0 && (
          <div className="mt-2 text-xs text-gray-500 text-center">
            {goals.filter(g => g.is_home).map(g =>
              `${g.player} ${g.minute}'`
            ).join(', ')}
            {goals.filter(g => g.is_home).length > 0 && goals.filter(g => !g.is_home).length > 0 && ' | '}
            {goals.filter(g => !g.is_home).map(g =>
              `${g.player} ${g.minute}'`
            ).join(', ')}
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="flex border-b border-gray-700">
        {([
          ['incidents', 'Timeline'],
          ['stats', 'Stats'],
          ['shotmap', 'Tirs'],
        ] as [DetailTab, string][]).map(([t, label]) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={clsx(
              'flex-1 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px',
              tab === t
                ? 'border-orange-500 text-orange-300'
                : 'border-transparent text-gray-500 hover:text-gray-300',
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="p-5">
        {tab === 'incidents' && (
          <div className="space-y-0.5">
            {match.incidents.filter(i => !['injuryTime'].includes(i.type) || i.type === 'period').map((inc, i) => (
              <IncidentRow key={i} inc={inc} homeTeam={match.home_team} awayTeam={match.away_team} />
            ))}
            {match.incidents.length === 0 && (
              <p className="text-gray-600 text-sm text-center py-6">Pas d&apos;incidents</p>
            )}
          </div>
        )}

        {tab === 'stats' && (
          <div className="space-y-6">
            <div>
              <div className="text-xs font-bold text-gray-400 uppercase tracking-wider mb-2">xG cumulé</div>
              <XgTimeline
                data={match.xg_per_minute}
                homeTeam={match.home_team}
                awayTeam={match.away_team}
                homeXg={match.home_xg}
                awayXg={match.away_xg}
              />
            </div>

            <PlayerStatsTable
              players={match.player_stats}
              teamId={match.home_team_id}
              teamName={match.home_team}
            />
            <PlayerStatsTable
              players={match.player_stats}
              teamId={match.away_team_id}
              teamName={match.away_team}
            />
          </div>
        )}

        {tab === 'shotmap' && (
          <div className="space-y-3">
            <div className="flex gap-2 justify-center">
              {(['both', 'home', 'away'] as const).map(s => (
                <button
                  key={s}
                  onClick={() => setShotSide(s)}
                  className={clsx(
                    'px-3 py-1 text-sm rounded border transition-colors',
                    shotSide === s
                      ? 'border-orange-500/50 bg-orange-500/10 text-orange-300'
                      : 'border-gray-700 text-gray-500 hover:text-gray-300',
                  )}
                >
                  {s === 'both' ? 'Les deux' : s === 'home' ? match.home_team : match.away_team}
                </button>
              ))}
            </div>
            <ShotMap
              shots={match.shotmap}
              homeTeam={match.home_team}
              awayTeam={match.away_team}
              side={shotSide}
            />
            {match.shotmap.length === 0 && (
              <p className="text-gray-600 text-sm text-center py-4">Pas de données de tirs</p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
