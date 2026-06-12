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
  const side = isHome ? homeTeam : awayTeam

  if (inc.type === 'period') {
    return (
      <div className="flex justify-center py-1">
        <span className="text-[10px] text-gray-600 bg-gray-800 px-2 py-0.5 rounded uppercase tracking-wider">
          {inc.text}
        </span>
      </div>
    )
  }

  if (inc.type === 'injuryTime') {
    return (
      <div className="flex justify-center py-0.5">
        <span className="text-[10px] text-gray-700">+{inc.length}' temps additionnel</span>
      </div>
    )
  }

  if (inc.type === 'goal') {
    return (
      <div className={clsx('flex items-center gap-2 py-1', isHome ? 'flex-row' : 'flex-row-reverse')}>
        <span className="text-[11px] text-gray-500 w-10 shrink-0 text-center">{minuteStr(inc)}</span>
        <div className={clsx('flex items-center gap-1', isHome ? 'flex-row' : 'flex-row-reverse')}>
          <span className="text-green-400">⚽</span>
          <div className={clsx('text-xs', isHome ? 'text-left' : 'text-right')}>
            <span className="text-white font-medium">
              {inc.is_own_goal ? `${inc.player} (CSC)` : inc.player}
            </span>
            {inc.assist && (
              <span className="text-gray-500 ml-1 text-[10px]">ass. {inc.assist}</span>
            )}
          </div>
        </div>
        <div className="ml-auto mr-auto" />
      </div>
    )
  }

  if (inc.type === 'substitution') {
    return (
      <div className={clsx('flex items-center gap-2 py-0.5 text-[11px]', isHome ? 'flex-row' : 'flex-row-reverse')}>
        <span className="text-gray-600 w-10 shrink-0 text-center">{minuteStr(inc)}</span>
        <span className="text-green-600">↑</span>
        <span className="text-gray-400">{inc.player_in}</span>
        <span className="text-red-600">↓</span>
        <span className="text-gray-500">{inc.player_out}</span>
      </div>
    )
  }

  if (inc.type === 'card') {
    const color = inc.card_type === 'yellow' ? 'text-yellow-400' : 'text-red-500'
    const emoji = inc.card_type === 'yellow' ? '🟨' : '🟥'
    return (
      <div className={clsx('flex items-center gap-2 py-0.5 text-[11px]', isHome ? 'flex-row' : 'flex-row-reverse')}>
        <span className="text-gray-600 w-10 shrink-0 text-center">{minuteStr(inc)}</span>
        <span>{emoji}</span>
        <span className={clsx('text-gray-400', color)}>{inc.player}</span>
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
    return <p className="text-gray-600 text-xs text-center py-4">Pas de données xG</p>
  }

  const maxXg = Math.max(...data.map(d => Math.max(d.cum_home, d.cum_away)), 0.1)
  const maxMin = Math.max(...data.map(d => d.m), 90)
  const W = 300
  const H = 80
  const pad = { l: 28, r: 8, t: 8, b: 20 }
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
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" style={{ maxHeight: 90 }}>
        {/* Grid */}
        {[0.5, 1.0, 1.5].filter(v => v <= maxXg).map(v => (
          <line key={v}
            x1={pad.l} x2={W - pad.r}
            y1={yScale(v)} y2={yScale(v)}
            stroke="#1f2937" strokeWidth={0.5} />
        ))}

        {/* xG 0 baseline */}
        <line x1={pad.l} x2={W - pad.r} y1={yScale(0)} y2={yScale(0)} stroke="#374151" strokeWidth={0.5} />

        {/* Lines */}
        <path d={homePath} fill="none" stroke="#3b82f6" strokeWidth={1.5} />
        <path d={awayPath} fill="none" stroke="#ef4444" strokeWidth={1.5} />

        {/* Axis labels */}
        {ticks.map(t => (
          <text key={t} x={xScale(t)} y={H - 4} textAnchor="middle" fontSize={7} fill="#4b5563">
            {t}'
          </text>
        ))}
        <text x={4} y={pad.t + 3} fontSize={7} fill="#4b5563">xG</text>
      </svg>

      <div className="flex justify-center gap-6 text-[10px] mt-1">
        <span className="flex items-center gap-1">
          <span className="w-6 h-0.5 bg-blue-500 inline-block" />
          <span className="text-blue-400">{homeTeam} ({homeXg?.toFixed(2) ?? '—'})</span>
        </span>
        <span className="flex items-center gap-1">
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
    return <p className="text-gray-600 text-xs py-2 text-center">Pas de données</p>
  }

  return (
    <div>
      <div className="text-[10px] font-bold text-gray-500 uppercase tracking-wider mb-1">{teamName}</div>
      <table className="w-full text-[11px]">
        <thead>
          <tr className="border-b border-gray-800 text-gray-500 text-[10px] uppercase">
            <th className="text-left py-0.5 pr-2">Joueur</th>
            <th className="text-right pr-1">Min</th>
            <th className="text-right pr-1">Note</th>
            <th className="text-right pr-1">B</th>
            <th className="text-right pr-1">Pa</th>
            <th className="text-right pr-1">xG</th>
            <th className="text-right pr-1">xA</th>
            <th className="text-right pr-1">Tirs</th>
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
                <td className="py-0.5 pr-2 text-gray-200 truncate max-w-[100px]">
                  {p.player_name ?? `#${p.player_id}`}
                  {p.yellow_card ? ' 🟨' : ''}
                  {p.red_card ? ' 🟥' : ''}
                </td>
                <td className="text-right pr-1 text-gray-500">{p.minutes_played ?? '—'}</td>
                <td className={clsx('text-right pr-1 font-semibold', ratingColor)}>
                  {p.rating?.toFixed(1) ?? '—'}
                </td>
                <td className="text-right pr-1 text-white font-bold">{p.goals || '—'}</td>
                <td className="text-right pr-1 text-orange-400">{p.goal_assist || '—'}</td>
                <td className="text-right pr-1 text-blue-400">{p.expected_goals?.toFixed(2) ?? '—'}</td>
                <td className="text-right pr-1 text-purple-400">{p.expected_assists?.toFixed(2) ?? '—'}</td>
                <td className="text-right pr-1 text-gray-400">
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
      <div className="px-4 py-3 border-b border-gray-700 bg-gray-800/60">
        <div className="flex items-center justify-between">
          <div className="text-[10px] text-gray-500 uppercase tracking-wider">
            {match.group_name ?? `Journée ${match.round_number}`}
            {match.period && match.status !== 'finished' && (
              <span className="ml-2 text-orange-400">{match.period}</span>
            )}
          </div>
          <button onClick={onClose} className="text-gray-600 hover:text-white text-lg leading-none">×</button>
        </div>

        {/* Score */}
        <div className="flex items-center justify-between mt-2 gap-4">
          <div className="flex-1 text-right">
            <div className="text-sm font-semibold text-white">{match.home_team}</div>
            {match.home_xg !== null && (
              <div className="text-[10px] text-blue-400/70">xG {match.home_xg.toFixed(2)}</div>
            )}
          </div>

          <div className="flex flex-col items-center shrink-0">
            {match.home_score !== null ? (
              <>
                <div className="text-2xl font-bold text-white tabular-nums">
                  {match.home_score} – {match.away_score}
                </div>
                {match.home_score_ht !== null && (
                  <div className="text-[10px] text-gray-600">
                    MT : {match.home_score_ht} – {match.away_score_ht}
                  </div>
                )}
              </>
            ) : (
              <div className="text-lg text-gray-600">vs</div>
            )}
          </div>

          <div className="flex-1 text-left">
            <div className="text-sm font-semibold text-white">{match.away_team}</div>
            {match.away_xg !== null && (
              <div className="text-[10px] text-red-400/70">xG {match.away_xg.toFixed(2)}</div>
            )}
          </div>
        </div>

        {/* Quick goal summary */}
        {goals.length > 0 && (
          <div className="mt-1.5 text-[10px] text-gray-500 text-center">
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
              'flex-1 py-1.5 text-xs font-medium transition-colors border-b-2 -mb-px',
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
      <div className="p-3 overflow-y-auto" style={{ maxHeight: 420 }}>
        {tab === 'incidents' && (
          <div className="space-y-0.5">
            {match.incidents.filter(i => !['injuryTime'].includes(i.type) || i.type === 'period').map((inc, i) => (
              <IncidentRow key={i} inc={inc} homeTeam={match.home_team} awayTeam={match.away_team} />
            ))}
            {match.incidents.length === 0 && (
              <p className="text-gray-600 text-xs text-center py-4">Pas d&apos;incidents</p>
            )}
          </div>
        )}

        {tab === 'stats' && (
          <div className="space-y-4">
            {/* xG timeline */}
            <div>
              <div className="text-[10px] font-bold text-gray-500 uppercase tracking-wider mb-1">xG cumulé</div>
              <XgTimeline
                data={match.xg_per_minute}
                homeTeam={match.home_team}
                awayTeam={match.away_team}
                homeXg={match.home_xg}
                awayXg={match.away_xg}
              />
            </div>

            {/* Player tables */}
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
          <div className="space-y-2">
            <div className="flex gap-1 justify-center">
              {(['both', 'home', 'away'] as const).map(s => (
                <button
                  key={s}
                  onClick={() => setShotSide(s)}
                  className={clsx(
                    'px-2 py-0.5 text-[10px] rounded border transition-colors',
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
              <p className="text-gray-600 text-xs text-center py-4">Pas de données de tirs</p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
