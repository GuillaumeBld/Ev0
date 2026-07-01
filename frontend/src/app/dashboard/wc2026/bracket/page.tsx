'use client'

import { useState, useEffect, useMemo } from 'react'
import { RefreshCw, ChevronDown } from 'lucide-react'
import { clsx } from 'clsx'
import { getWCAdvancement, getFixtures, getWCKoH2H, getKOPredictions, type WCTeamAdvancement, type FixtureOut, type KOMatchH2H, type KOPrediction } from '@/lib/api'

// ── Flags ─────────────────────────────────────────────────────────────────────

const NATION_FLAGS: Record<string, string> = {
  'Spain': '🇪🇸', 'Brazil': '🇧🇷', 'Germany': '🇩🇪', 'England': '🏴󠁧󠁢󠁥󠁮󠁧󠁿',
  'France': '🇫🇷', 'Argentina': '🇦🇷', 'Portugal': '🇵🇹', 'Belgium': '🇧🇪',
  'Netherlands': '🇳🇱', 'Switzerland': '🇨🇭', 'Colombia': '🇨🇴', 'Norway': '🇳🇴',
  'Mexico': '🇲🇽', 'Ecuador': '🇪🇨', 'Uruguay': '🇺🇾', 'Canada': '🇨🇦',
  'United States': '🇺🇸', 'Croatia': '🇭🇷', 'Morocco': '🇲🇦', 'Ivory Coast': '🇨🇮',
  'Austria': '🇦🇹', 'Turkey': '🇹🇷', 'Japan': '🇯🇵', 'Senegal': '🇸🇳',
  'Egypt': '🇪🇬', 'Scotland': '🏴󠁧󠁢󠁳󠁣󠁴󠁿', 'South Korea': '🇰🇷', 'Czechia': '🇨🇿',
  'Sweden': '🇸🇪', 'Bosnia-Herzegovina': '🇧🇦', 'Algeria': '🇩🇿', 'Paraguay': '🇵🇾',
  'Iran': '🇮🇷', 'Ghana': '🇬🇭', 'Australia': '🇦🇺', 'Congo DR': '🇨🇩',
  'Panama': '🇵🇦', 'New Zealand': '🇳🇿', 'South Africa': '🇿🇦', 'Uzbekistan': '🇺🇿',
  'Tunisia': '🇹🇳', 'Cape Verde Islands': '🇨🇻', 'Saudi Arabia': '🇸🇦',
  'Curaçao': '🇨🇼', 'Haiti': '🇭🇹', 'Jordan': '🇯🇴', 'Qatar': '🇶🇦', 'Iraq': '🇮🇶',
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function eloWinProb(eloA: number, eloB: number): number {
  return 1 / (1 + Math.pow(10, (eloB - eloA) / 400))
}

function isPlaceholder(name: string): boolean {
  return /^[WL]\d+$/.test(name.trim())
}

// Groupe position codes (2A, 1C, 3A/3B/…) ou W/L codes → pas une vraie nation
function isRealTeam(name: string): boolean {
  if (!name) return false
  if (/^[WL]\d+$/.test(name.trim())) return false
  if (/^[1-3][A-Z]/.test(name.trim())) return false
  return true
}

function isEliminated(t: WCTeamAdvancement): boolean {
  return t.p_winner === 0 && t.p_finalist === 0 && t.p_sf === 0 && t.p_qf === 0 && t.p_r16 === 0
}

// ── Shared sub-components (Classement + Croisements tabs) ─────────────────────

function ProbBar({ value, color }: { value: number; color: string }) {
  const pct = Math.round(value * 100)
  if (pct === 0) return <span className="text-gray-600 tabular-nums">—</span>
  return (
    <div className="flex items-center gap-1.5 min-w-[56px]">
      <div className="flex-1 h-1.5 bg-gray-800 rounded-full overflow-hidden">
        <div className={clsx('h-full rounded-full', color)} style={{ width: `${Math.min(pct, 100)}%` }} />
      </div>
      <span className="text-xs tabular-nums w-8 text-right">{pct}%</span>
    </div>
  )
}

function EloChip({ elo }: { elo: number }) {
  const color =
    elo >= 1650 ? 'text-emerald-400' :
    elo >= 1550 ? 'text-yellow-400' :
    elo >= 1450 ? 'text-orange-400' : 'text-gray-400'
  return <span className={clsx('font-mono font-semibold tabular-nums text-xs', color)}>{Math.round(elo)}</span>
}

interface MatchupCardProps {
  home: string
  away: string
  byNation: Record<string, WCTeamAdvancement>
  label?: string
  kickoff?: string
  finished?: boolean
  homeScore?: number | null
  awayScore?: number | null
}

function MatchupCard({ home, away, byNation, label, kickoff, finished, homeScore, awayScore }: MatchupCardProps) {
  const tHome = byNation[home]
  const tAway = byNation[away]
  const eloHome = tHome?.elo ?? 1500
  const eloAway = tAway?.elo ?? 1500
  const pHome = eloWinProb(eloHome, eloAway)
  const pAway = 1 - pHome

  const fmtKickoff = kickoff
    ? new Date(kickoff).toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
    : null

  return (
    <div className={clsx(
      'rounded-xl border bg-gray-900 overflow-hidden',
      finished ? 'border-gray-700/50 opacity-70' : 'border-gray-700',
    )}>
      <div className="flex items-center justify-between px-3 py-1.5 bg-gray-800/60 border-b border-gray-700/50">
        {label && <span className="text-[10px] font-bold text-orange-400 uppercase tracking-wider">{label}</span>}
        {fmtKickoff && <span className="text-[10px] text-gray-500 ml-auto">{fmtKickoff}</span>}
        {finished && homeScore != null && awayScore != null && (
          <span className="text-[10px] font-bold text-gray-300 ml-auto">{homeScore} — {awayScore}</span>
        )}
      </div>
      <div className="px-3 py-3 flex items-stretch gap-2">
        <div className="flex-1 flex flex-col gap-1">
          <div className="flex items-center gap-1.5">
            <span className="text-xl leading-none">{NATION_FLAGS[home] ?? '🏳'}</span>
            <span className="font-semibold text-sm text-white truncate">{home}</span>
          </div>
          <EloChip elo={eloHome} />
          {tHome && (
            <div className="flex flex-col gap-0.5 mt-1">
              <StatRow label="SF" value={tHome.p_sf} />
              <StatRow label="Fin." value={tHome.p_finalist} />
              <StatRow label="Win" value={tHome.p_winner} highlight />
            </div>
          )}
        </div>
        <div className="flex flex-col items-center justify-center gap-1 px-2 min-w-[80px]">
          <WinProb pct={pHome} side="home" />
          <span className="text-[10px] text-gray-600 font-bold">VS</span>
          <WinProb pct={pAway} side="away" />
        </div>
        <div className="flex-1 flex flex-col items-end gap-1">
          <div className="flex items-center gap-1.5 flex-row-reverse">
            <span className="text-xl leading-none">{NATION_FLAGS[away] ?? '🏳'}</span>
            <span className="font-semibold text-sm text-white truncate">{away}</span>
          </div>
          <div className="self-end"><EloChip elo={eloAway} /></div>
          {tAway && (
            <div className="flex flex-col items-end gap-0.5 mt-1">
              <StatRow label="SF" value={tAway.p_sf} right />
              <StatRow label="Fin." value={tAway.p_finalist} right />
              <StatRow label="Win" value={tAway.p_winner} highlight right />
            </div>
          )}
        </div>
      </div>
      <div className="px-3 pb-3">
        <div className="h-1.5 rounded-full overflow-hidden flex bg-gray-800">
          <div className="bg-orange-500 h-full rounded-l-full" style={{ width: `${pHome * 100}%` }} />
          <div className="bg-blue-500 h-full rounded-r-full flex-1" />
        </div>
        <div className="flex justify-between mt-0.5">
          <span className="text-[10px] text-orange-400">{(pHome * 100).toFixed(0)}%</span>
          <span className="text-[10px] text-blue-400">{(pAway * 100).toFixed(0)}%</span>
        </div>
      </div>
    </div>
  )
}

function WinProb({ pct, side }: { pct: number; side: 'home' | 'away' }) {
  const color = side === 'home' ? 'text-orange-300' : 'text-blue-300'
  const bg = side === 'home' ? 'bg-orange-500/10' : 'bg-blue-500/10'
  return (
    <div className={clsx('rounded px-2 py-0.5', bg)}>
      <span className={clsx('text-sm font-bold tabular-nums', color)}>{(pct * 100).toFixed(0)}%</span>
    </div>
  )
}

function StatRow({ label, value, highlight, right }: { label: string; value: number; highlight?: boolean; right?: boolean }) {
  const pct = Math.round(value * 100)
  return (
    <div className={clsx('flex items-center gap-1', right && 'flex-row-reverse')}>
      <span className="text-[10px] text-gray-500 w-6">{label}</span>
      <span className={clsx(
        'text-[10px] tabular-nums font-medium',
        highlight ? (pct > 0 ? 'text-emerald-400' : 'text-gray-600') :
        pct > 0 ? 'text-gray-300' : 'text-gray-600'
      )}>
        {pct > 0 ? `${pct}%` : '—'}
      </span>
    </div>
  )
}

function TeamSelect({
  value, onChange, teams, placeholder,
}: {
  value: string
  onChange: (v: string) => void
  teams: WCTeamAdvancement[]
  placeholder: string
}) {
  return (
    <div className="relative">
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        className="appearance-none w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white pr-8 focus:outline-none focus:border-orange-500"
      >
        <option value="">{placeholder}</option>
        <optgroup label="En lice">
          {teams.filter(t => !isEliminated(t)).map(t => (
            <option key={t.nation} value={t.nation}>{NATION_FLAGS[t.nation] ?? ''} {t.nation}</option>
          ))}
        </optgroup>
        <optgroup label="Éliminées">
          {teams.filter(t => isEliminated(t)).map(t => (
            <option key={t.nation} value={t.nation}>{NATION_FLAGS[t.nation] ?? ''} {t.nation} ✗</option>
          ))}
        </optgroup>
      </select>
      <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
    </div>
  )
}

// ── Bracket tree ───────────────────────────────────────────────────────────────

const BK_CARD_H = 60
const BK_CARD_W = 182
const BK_UNIT = 68        // card height + 8px gap
const BK_COL_GAP = 44
const BK_COL_STRIDE = BK_CARD_W + BK_COL_GAP  // 226
const BK_TOTAL_H = 16 * BK_UNIT               // 1088
const BK_TOTAL_W = 5 * BK_CARD_W + 4 * BK_COL_GAP  // 1086

// Bzzoiro round_number → bracket position (idx 0=R32 … 4=Final)
const BK_ROUNDS: { matchweek: number; label: string; idx: number }[] = [
  { matchweek: 6,  label: '32es de finale', idx: 0 },
  { matchweek: 5,  label: '16es de finale', idx: 1 },
  { matchweek: 27, label: 'Quarts',          idx: 2 },
  { matchweek: 28, label: 'Demies',           idx: 3 },
  { matchweek: 29, label: 'Finale',           idx: 4 },
]

// Advancement probability field at the stage AFTER each KO round
const NEXT_STAGE_KEY: (keyof WCTeamAdvancement)[] = [
  'p_r16', 'p_qf', 'p_sf', 'p_finalist', 'p_winner',
]

// Y-center of the i-th match in round roundIdx
function bkCenters(roundIdx: number): number[] {
  const count = Math.round(16 / Math.pow(2, roundIdx))
  const spacing = BK_UNIT * Math.pow(2, roundIdx)
  const firstCenter = spacing / 2
  return Array.from({ length: count }, (_, i) => firstCenter + i * spacing)
}

// X position (left edge) of cards in round roundIdx
function bkX(roundIdx: number): number {
  return roundIdx * BK_COL_STRIDE
}

// ── BkSlot: one team row inside a bracket match card ──────────────────────────

function BkSlot({
  team, ph, score, elo, prob, isWinner, isLoser, isPen,
}: {
  team: string; ph: boolean
  score: number | null; elo: number | null; prob: number | null
  isWinner: boolean; isLoser: boolean; isPen: boolean
}) {
  return (
    <div className={clsx(
      'flex-1 flex items-center gap-1.5 px-2 min-w-0 overflow-hidden',
      isWinner && 'bg-emerald-500/10',
      isLoser && 'opacity-40',
    )}>
      {ph ? (
        <span className="text-gray-700 text-[10px] truncate">{team || '?'}</span>
      ) : (
        <>
          <span className="text-base leading-none shrink-0">{NATION_FLAGS[team] ?? '🏳'}</span>
          <span className={clsx(
            'text-[10px] truncate min-w-0 flex-1',
            isWinner ? 'font-semibold text-white' : 'text-gray-300',
          )}>
            {team}
          </span>
          {elo != null && (
            <span className={clsx(
              'text-[9px] font-mono shrink-0',
              elo >= 1600 ? 'text-emerald-400' : elo >= 1500 ? 'text-yellow-400' : 'text-gray-600',
            )}>
              {Math.round(elo)}
            </span>
          )}
        </>
      )}
      {score != null && (
        <span className={clsx(
          'text-[11px] font-bold tabular-nums shrink-0 ml-auto',
          isWinner ? 'text-white' : 'text-gray-500',
        )}>
          {score}
        </span>
      )}
      {prob != null && (
        <span className="text-[9px] text-gray-400 shrink-0 ml-auto tabular-nums">
          {Math.round(prob * 100)}%
        </span>
      )}
      {isPen && <span className="text-[8px] text-amber-400 shrink-0 ml-0.5">p.o.</span>}
    </div>
  )
}

// ── BracketMatchCard ──────────────────────────────────────────────────────────

function BracketMatchCard({
  fixture, byNation, roundIdx,
}: {
  fixture: FixtureOut | null
  byNation: Record<string, WCTeamAdvancement>
  roundIdx: number
}) {
  if (!fixture) {
    return (
      <div
        style={{ width: BK_CARD_W, height: BK_CARD_H }}
        className="rounded border border-gray-800/60 bg-gray-900/30 flex flex-col overflow-hidden"
      >
        <div className="flex-1 flex items-center px-2">
          <span className="text-gray-800 text-[10px]">—</span>
        </div>
        <div className="h-px bg-gray-800 shrink-0" />
        <div className="flex-1 flex items-center px-2">
          <span className="text-gray-800 text-[10px]">—</span>
        </div>
      </div>
    )
  }

  const { home_team: home, away_team: away, status, home_score: hs, away_score: as } = fixture
  const finished = status === 'finished'
  const ph_h = isPlaceholder(home)
  const ph_a = isPlaceholder(away)

  let homeWins = false, awayWins = false, isPen = false
  if (finished && hs != null && as != null) {
    if (hs > as) homeWins = true
    else if (as > hs) awayWins = true
    else {
      isPen = true
      const h = byNation[home]
      const a = byNation[away]
      if (h && a) {
        const key = NEXT_STAGE_KEY[roundIdx] ?? 'p_r16'
        const hp = h[key] as number ?? 0
        const ap = a[key] as number ?? 0
        if (hp > ap) homeWins = true
        else if (ap > hp) awayWins = true
      }
    }
  }

  // ELO win probability for upcoming matches with two real teams
  const upcoming = !finished && !ph_h && !ph_a
  const eloH = byNation[home]?.elo ?? 1500
  const eloA = byNation[away]?.elo ?? 1500
  const pHome = upcoming ? eloWinProb(eloH, eloA) : null
  const pAway = pHome !== null ? 1 - pHome : null

  return (
    <div
      style={{ width: BK_CARD_W, height: BK_CARD_H }}
      className="rounded border border-gray-700 bg-gray-900 flex flex-col overflow-hidden"
    >
      <BkSlot
        team={home} ph={ph_h}
        score={finished ? hs : null}
        elo={!finished && !ph_h ? (byNation[home]?.elo ?? null) : null}
        prob={pHome}
        isWinner={homeWins} isLoser={finished && awayWins}
        isPen={homeWins && isPen}
      />
      <div className="h-px bg-gray-700 shrink-0" />
      <BkSlot
        team={away} ph={ph_a}
        score={finished ? as : null}
        elo={!finished && !ph_a ? (byNation[away]?.elo ?? null) : null}
        prob={pAway}
        isWinner={awayWins} isLoser={finished && homeWins}
        isPen={awayWins && isPen}
      />
    </div>
  )
}

// ── BracketTree ───────────────────────────────────────────────────────────────

const KO_MATCHWEEKS = new Set([6, 5, 27, 28, 29, 50])

function BracketTree({
  fixtures, byNation,
}: {
  fixtures: FixtureOut[]
  byNation: Record<string, WCTeamAdvancement>
}) {
  const rounds = useMemo(() => {
    const groups: Record<number, FixtureOut[]> = {}
    for (const f of fixtures) {
      if (f.matchweek == null || !KO_MATCHWEEKS.has(f.matchweek)) continue
      if (!groups[f.matchweek]) groups[f.matchweek] = []
      groups[f.matchweek].push(f)
    }
    for (const k in groups) {
      groups[k].sort((a, b) => new Date(a.kickoff_utc).getTime() - new Date(b.kickoff_utc).getTime())
    }
    return groups
  }, [fixtures])

  // Pre-compute all SVG connector paths between consecutive rounds
  const connectorPaths = useMemo(() => {
    const paths: string[] = []
    for (let r = 0; r < 4; r++) {
      const leftX = bkX(r) + BK_CARD_W         // right edge of cards in round r
      const midX  = leftX + BK_COL_GAP / 2      // center of gap
      const rightX = bkX(r + 1)                  // left edge of cards in round r+1
      const cur = bkCenters(r)
      const nxt = bkCenters(r + 1)
      for (let i = 0; i < nxt.length; i++) {
        const ct = cur[i * 2]
        const cb = cur[i * 2 + 1]
        const cp = nxt[i]
        // ⌐ shape: horizontal out → vertical → horizontal to parent
        paths.push(`M ${leftX} ${ct} H ${midX}`)
        paths.push(`M ${midX} ${ct} V ${cb}`)
        paths.push(`M ${leftX} ${cb} H ${midX}`)
        paths.push(`M ${midX} ${cp} H ${rightX}`)
      }
    }
    return paths
  }, [])

  const thirdPlace = rounds[50]?.[0] ?? null

  return (
    <div className="overflow-auto pb-6">
      {/* Round labels — sticky */}
      <div
        style={{ width: BK_TOTAL_W }}
        className="flex mb-3 sticky top-0 z-10 bg-gray-950 py-1.5"
      >
        {BK_ROUNDS.map(({ label, idx }) => (
          <div
            key={idx}
            style={{ width: BK_CARD_W, marginRight: idx < 4 ? BK_COL_GAP : 0 }}
            className="text-center text-[10px] font-bold uppercase tracking-wider text-gray-500"
          >
            {label}
            {idx < 4 && (
              <span className="text-gray-700 text-[9px] ml-1">
                ({rounds[BK_ROUNDS.find(r => r.idx === idx)!.matchweek]?.length ?? 0}/{Math.round(16 / Math.pow(2, idx))})
              </span>
            )}
          </div>
        ))}
      </div>

      {/* Bracket container */}
      <div style={{ position: 'relative', width: BK_TOTAL_W, height: BK_TOTAL_H }}>
        {/* SVG connector lines */}
        <svg
          width={BK_TOTAL_W}
          height={BK_TOTAL_H}
          style={{ position: 'absolute', inset: 0, pointerEvents: 'none', zIndex: 0 }}
        >
          {connectorPaths.map((d, i) => (
            <path key={i} d={d} stroke="#374151" strokeWidth="1.5" fill="none" />
          ))}
        </svg>

        {/* Match cards */}
        {BK_ROUNDS.map(({ matchweek, idx }) => {
          const roundFixtures = rounds[matchweek] ?? []
          const expectedCount = Math.round(16 / Math.pow(2, idx))
          const centers = bkCenters(idx)
          const x = bkX(idx)

          return Array.from({ length: expectedCount }, (_, i) => (
            <div
              key={`${matchweek}-${i}`}
              style={{
                position: 'absolute',
                left: x,
                top: centers[i] - BK_CARD_H / 2,
                zIndex: 1,
              }}
            >
              <BracketMatchCard
                fixture={roundFixtures[i] ?? null}
                byNation={byNation}
                roundIdx={idx}
              />
            </div>
          ))
        })}
      </div>

      {/* 3rd place match (separate from main bracket) */}
      {thirdPlace && (
        <div className="mt-8 flex items-center gap-4">
          <span className="text-[10px] font-bold text-gray-600 uppercase tracking-wider whitespace-nowrap">
            3e place
          </span>
          <BracketMatchCard fixture={thirdPlace} byNation={byNation} roundIdx={3} />
        </div>
      )}
    </div>
  )
}

// ── Backtest tab ───────────────────────────────────────────────────────────────

const ROUND_LABEL: Record<number, string> = { 6: '32es', 5: '16es', 27: 'QF', 28: 'SF', 29: 'Fin' }

function BacktestTab({ predictions }: { predictions: KOPrediction[] }) {
  const finished = predictions.filter(p => p.winner !== null)
  const pending  = predictions.filter(p => p.winner === null)

  // Summary stats on finished matches
  const stats = finished.reduce(
    (acc, p) => {
      const eloPH = p.elo_prob_home ?? 0.5
      const mktPH = p.pin_prob_home ?? 0.5
      const actual = p.winner === 'home' ? 1 : 0
      acc.eloBS  += (eloPH - actual) ** 2
      acc.mktBS  += (mktPH - actual) ** 2
      acc.eloCorrect += (eloPH >= 0.5) === (actual === 1) ? 1 : 0
      acc.mktCorrect += (mktPH >= 0.5) === (actual === 1) ? 1 : 0
      acc.n += 1
      return acc
    },
    { eloBS: 0, mktBS: 0, eloCorrect: 0, mktCorrect: 0, n: 0 },
  )

  function fmtP(v: number | null) {
    return v != null ? `${(v * 100).toFixed(1)}%` : '—'
  }
  function fmtOdds(v: number | null) {
    return v != null ? v.toFixed(2) : '—'
  }

  function ResultBadge({ p }: { p: KOPrediction }) {
    if (!p.winner) return <span className="text-gray-500">en cours</span>
    const eloWin = (p.elo_prob_home ?? 0.5) >= 0.5 ? 'home' : 'away'
    const mktWin = (p.pin_prob_home ?? 0.5) >= 0.5 ? 'home' : 'away'
    const eloRight = eloWin === p.winner
    const mktRight = mktWin === p.winner
    const score = p.home_score != null ? `${p.home_score}–${p.away_score}` : '?'
    return (
      <div className="flex items-center gap-2">
        <span className="text-gray-300 font-mono text-xs">{score}</span>
        <span className={clsx('text-[10px] font-bold', eloRight ? 'text-emerald-400' : 'text-red-400')}>
          ELO {eloRight ? '✓' : '✗'}
        </span>
        <span className={clsx('text-[10px] font-bold', mktRight ? 'text-emerald-400' : 'text-red-400')}>
          Pin {mktRight ? '✓' : '✗'}
        </span>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Summary card */}
      {stats.n > 0 && (
        <div className="rounded-xl border border-gray-700 bg-gray-900 p-4">
          <p className="text-xs font-semibold text-gray-300 mb-3">
            Résumé — {stats.n} match{stats.n > 1 ? 's' : ''} terminé{stats.n > 1 ? 's' : ''}
          </p>
          <div className="grid grid-cols-2 gap-4 text-xs">
            <div>
              <p className="text-gray-500 mb-1 uppercase text-[10px] tracking-wider">Brier Score ↓</p>
              <div className="flex gap-4">
                <div>
                  <span className="text-gray-400">ELO hist. </span>
                  <span className={clsx('font-bold', stats.eloBS / stats.n < stats.mktBS / stats.n ? 'text-emerald-400' : 'text-orange-400')}>
                    {(stats.eloBS / stats.n).toFixed(4)}
                  </span>
                </div>
                <div>
                  <span className="text-gray-400">Pinnacle </span>
                  <span className={clsx('font-bold', stats.mktBS / stats.n <= stats.eloBS / stats.n ? 'text-emerald-400' : 'text-orange-400')}>
                    {(stats.mktBS / stats.n).toFixed(4)}
                  </span>
                </div>
              </div>
            </div>
            <div>
              <p className="text-gray-500 mb-1 uppercase text-[10px] tracking-wider">Précision (favori)</p>
              <div className="flex gap-4">
                <div>
                  <span className="text-gray-400">ELO </span>
                  <span className="font-bold text-white">{stats.eloCorrect}/{stats.n}</span>
                </div>
                <div>
                  <span className="text-gray-400">Pin </span>
                  <span className="font-bold text-white">{stats.mktCorrect}/{stats.n}</span>
                </div>
              </div>
            </div>
          </div>
          <p className="text-[10px] text-gray-600 mt-3">
            Brier Score = erreur quadratique moyenne. Plus bas = meilleur. Favoris = équipe avec P% &gt; 50%.
          </p>
        </div>
      )}

      {/* Predictions table */}
      {[...finished, ...pending].length === 0 ? (
        <p className="text-gray-500 text-sm p-4">Aucune prédiction enregistrée — le premier snapshot sera pris automatiquement avant le prochain match KO.</p>
      ) : (
        <div className="rounded-xl border border-gray-700 bg-gray-900 overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-gray-800 text-gray-400 border-b border-gray-700">
                <th className="text-left px-3 py-2 font-medium">Tour</th>
                <th className="text-left px-3 py-2 font-medium">Match</th>
                <th className="px-3 py-2 font-medium text-orange-300">ELO%</th>
                <th className="px-3 py-2 font-medium text-blue-300 border-l border-gray-700">Pin%</th>
                <th className="px-3 py-2 font-medium text-blue-300">Cote Pin</th>
                <th className="px-3 py-2 font-medium border-l border-gray-700">Résultat</th>
                <th className="px-3 py-2 font-medium text-gray-500">Edge</th>
              </tr>
            </thead>
            <tbody>
              {[...finished, ...pending].map(p => {
                const eloPH = p.elo_prob_home ?? null
                const pinPH = p.pin_prob_home ?? null
                const edge  = eloPH != null && pinPH != null ? (eloPH - pinPH) * 100 : null
                return (
                  <tr key={p.fixture_id} className="border-b border-gray-700/50 hover:bg-gray-800/50">
                    <td className="px-3 py-2 text-gray-400 text-[10px] whitespace-nowrap">
                      {ROUND_LABEL[p.matchweek ?? 0] ?? p.matchweek}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      <div className="flex flex-col">
                        <span className="text-white font-medium">{p.home_team}</span>
                        <span className="text-gray-400 text-[10px]">vs {p.away_team}</span>
                      </div>
                    </td>
                    <td className="px-3 py-2 text-center">
                      {eloPH != null ? (
                        <div className="flex flex-col items-center gap-0.5">
                          <span className={clsx('font-semibold', eloPH >= 0.65 ? 'text-emerald-400' : eloPH >= 0.5 ? 'text-yellow-400' : 'text-orange-400')}>
                            {fmtP(eloPH)}
                          </span>
                          <span className="text-gray-500 text-[9px]">{fmtP(1 - eloPH)}</span>
                        </div>
                      ) : '—'}
                    </td>
                    <td className="px-3 py-2 text-center border-l border-gray-700/50">
                      {pinPH != null ? (
                        <div className="flex flex-col items-center gap-0.5">
                          <span className={clsx('font-semibold', pinPH >= 0.65 ? 'text-emerald-400' : pinPH >= 0.5 ? 'text-yellow-400' : 'text-orange-400')}>
                            {fmtP(pinPH)}
                          </span>
                          <span className="text-gray-500 text-[9px]">{fmtP(1 - pinPH)}</span>
                        </div>
                      ) : '—'}
                    </td>
                    <td className="px-3 py-2 text-center text-gray-300">
                      <div className="flex flex-col items-center gap-0.5">
                        <span>{fmtOdds(p.pin_odds_home)}</span>
                        <span className="text-gray-500 text-[9px]">{fmtOdds(p.pin_odds_away)}</span>
                      </div>
                    </td>
                    <td className="px-3 py-2 border-l border-gray-700/50">
                      <ResultBadge p={p} />
                    </td>
                    <td className="px-3 py-2 text-center">
                      {edge != null ? (
                        <span className={clsx(
                          'font-bold text-[10px]',
                          edge >= 5  ? 'text-emerald-400' :
                          edge <= -5 ? 'text-red-400' :
                          'text-gray-400',
                        )}>
                          {edge >= 0 ? '+' : ''}{edge.toFixed(1)}%
                        </span>
                      ) : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {pending.length > 0 && (
        <p className="text-[10px] text-gray-600 px-1">
          {pending.length} match{pending.length > 1 ? 's' : ''} en attente — résultat mis à jour automatiquement après le coup de sifflet final.
        </p>
      )}
    </div>
  )
}

// ── Valeur tab: ELO vs market comparison ──────────────────────────────────────

const FR_BOOKS = new Set(['betclic', 'unibet', 'pmu'])

// Vig-removed implied P% from a single bookmaker line (h/draw/a), draw split 50/50 for KO
function lineImplied(h: number, d: number, a: number): { home: number; away: number; vig: number } {
  const rh = 1 / h, rd = 1 / d, ra = 1 / a
  const total = rh + rd + ra
  return {
    home: rh / total + (rd / total) / 2,
    away: ra / total + (rd / total) / 2,
    vig: total,
  }
}

function pinnacleImp(lines: KOMatchH2H['lines']): { home: number; away: number; vig: number; h: number; a: number } | null {
  const pin = lines.find(l => l.bookmaker === 'pinnacle')
  if (!pin?.home || !pin?.draw || !pin?.away) return null
  const imp = lineImplied(pin.home, pin.draw, pin.away)
  return { ...imp, h: pin.home, a: pin.away }
}

function bestFROdds(lines: KOMatchH2H['lines']): { home: number | null; away: number | null } {
  const fr = lines.filter(l => FR_BOOKS.has(l.bookmaker))
  const homes = fr.map(l => l.home).filter((v): v is number => v != null)
  const aways = fr.map(l => l.away).filter((v): v is number => v != null)
  return {
    home: homes.length > 0 ? Math.max(...homes) : null,
    away: aways.length > 0 ? Math.max(...aways) : null,
  }
}

const ROUND_ORDER: Record<number, number> = { 6: 0, 5: 1, 27: 2, 28: 3, 29: 4, 50: 5 }
const ROUND_LABELS: Record<number, string> = { 6: '32es', 5: '16es', 27: 'QF', 28: 'SF', 29: 'Finale', 50: '3e place' }

function ValeurTab({
  h2hMatches, byNation,
}: {
  h2hMatches: KOMatchH2H[]
  byNation: Record<string, WCTeamAdvancement>
}) {
  const realMatches = h2hMatches.filter(m => isRealTeam(m.home_team) && isRealTeam(m.away_team))

  if (realMatches.length === 0) {
    return (
      <p className="text-gray-500 text-sm p-4">
        Aucune cote h2h disponible pour des matchs KO avec équipes identifiées.
        Les cotes arriveront au fur et à mesure que les équipes qualifiées sont connues.
      </p>
    )
  }

  const sorted = [...realMatches].sort((a, b) => {
    const ro = (ROUND_ORDER[a.matchweek ?? 99] ?? 99) - (ROUND_ORDER[b.matchweek ?? 99] ?? 99)
    if (ro !== 0) return ro
    return new Date(a.kickoff_utc).getTime() - new Date(b.kickoff_utc).getTime()
  })

  function EdgeCell({ elo, market }: { elo: number; market: number }) {
    const edge = elo - market
    const pct = Math.round(edge * 100)
    const abs = Math.abs(pct)
    const color =
      pct >= 5  ? 'text-emerald-400 font-bold' :
      pct >= 2  ? 'text-green-400' :
      pct <= -5 ? 'text-red-400 font-bold' :
      pct <= -2 ? 'text-orange-400' :
                  'text-gray-500'
    return (
      <span className={clsx('tabular-nums', color)}>
        {pct >= 0 ? '+' : '−'}{abs}%
      </span>
    )
  }

  function OddsCell({ value, highlight }: { value: number | null; highlight?: boolean }) {
    if (value == null) return <span className="text-gray-700">—</span>
    return (
      <span className={clsx('tabular-nums font-medium', highlight ? 'text-white' : 'text-gray-300')}>
        {value.toFixed(2)}
      </span>
    )
  }

  return (
    <div className="flex-1 overflow-auto">
      <table className="w-full text-xs border-collapse">
        <thead className="sticky top-0 bg-gray-900 z-10">
          <tr className="border-b border-gray-700 text-[10px] font-semibold uppercase tracking-wider">
            <th className="px-3 py-2 text-left text-gray-500">Tour</th>
            <th className="px-3 py-2 text-left text-gray-500">Équipe</th>
            <th className="px-3 py-2 text-right text-orange-400">ELO P%</th>
            <th className="px-3 py-2 text-right text-gray-400 border-l border-gray-800">Pinnacle</th>
            <th className="px-3 py-2 text-right text-blue-400">P% nette</th>
            <th className="px-3 py-2 text-right text-gray-400">Edge</th>
            <th className="px-3 py-2 text-right text-yellow-600 border-l border-gray-800">Meil. FR</th>
            <th className="px-3 py-2 text-right text-gray-600">Vig Pin.</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((m) => {
            const pin = pinnacleImp(m.lines)
            const fr  = bestFROdds(m.lines)
            const eloH = byNation[m.home_team]?.elo ?? 1500
            const eloA = byNation[m.away_team]?.elo ?? 1500
            const pEloH = eloWinProb(eloH, eloA)
            const pEloA = 1 - pEloH
            const finished = m.status === 'finished'
            const roundLabel = ROUND_LABELS[m.matchweek ?? 0] ?? '?'

            // Score display
            const scoreH = finished && m.home_score != null && m.away_score != null
              ? (m.home_score > m.away_score ? 'text-emerald-400 font-bold' : 'text-gray-600') : ''
            const scoreA = finished && m.home_score != null && m.away_score != null
              ? (m.away_score > m.home_score ? 'text-emerald-400 font-bold' : 'text-gray-600') : ''

            return [
              <tr key={`${m.fixture_id}-h`} className={clsx(
                'border-b border-gray-800/40 hover:bg-gray-800/30',
                finished && 'opacity-50',
              )}>
                <td className="px-3 py-1.5 whitespace-nowrap" rowSpan={2}>
                  <span className="text-[10px] font-bold text-gray-500 uppercase">{roundLabel}</span>
                </td>
                <td className="px-3 py-1.5">
                  <div className="flex items-center gap-1.5">
                    <span className="text-sm leading-none">{NATION_FLAGS[m.home_team] ?? '🏳'}</span>
                    <span className="font-medium text-gray-200">{m.home_team}</span>
                    {finished && m.home_score != null && (
                      <span className={clsx('text-[10px] ml-1', scoreH)}>{m.home_score}–{m.away_score}</span>
                    )}
                  </div>
                </td>
                <td className="px-3 py-1.5 text-right">
                  <span className="text-orange-300 font-semibold">{Math.round(pEloH * 100)}%</span>
                </td>
                {/* Pinnacle */}
                <td className="px-3 py-1.5 text-right border-l border-gray-800">
                  <OddsCell value={pin?.h ?? null} />
                </td>
                <td className="px-3 py-1.5 text-right">
                  {pin
                    ? <span className="text-blue-300">{Math.round(pin.home * 100)}%</span>
                    : <span className="text-gray-700">—</span>}
                </td>
                <td className="px-3 py-1.5 text-right">
                  {pin ? <EdgeCell elo={pEloH} market={pin.home} /> : <span className="text-gray-700">—</span>}
                </td>
                {/* Best FR */}
                <td className="px-3 py-1.5 text-right border-l border-gray-800">
                  <OddsCell value={fr.home} highlight={!!pin && !!fr.home && fr.home > pin.h!} />
                </td>
                {/* Vig Pinnacle (rowspan) */}
                <td className="px-3 py-1.5 text-right" rowSpan={2}>
                  {pin
                    ? <span className={clsx('text-[10px]', pin.vig > 1.025 ? 'text-orange-400' : 'text-gray-600')}>
                        {((pin.vig - 1) * 100).toFixed(1)}%
                      </span>
                    : <span className="text-gray-700">—</span>}
                </td>
              </tr>,
              <tr key={`${m.fixture_id}-a`} className={clsx(
                'border-b border-gray-700/50 hover:bg-gray-800/30',
                finished && 'opacity-50',
              )}>
                <td className="px-3 py-1.5">
                  <div className="flex items-center gap-1.5">
                    <span className="text-sm leading-none">{NATION_FLAGS[m.away_team] ?? '🏳'}</span>
                    <span className="font-medium text-gray-200">{m.away_team}</span>
                    {finished && m.away_score != null && (
                      <span className={clsx('text-[10px] ml-1', scoreA)}>{m.away_score}–{m.home_score}</span>
                    )}
                  </div>
                </td>
                <td className="px-3 py-1.5 text-right">
                  <span className="text-orange-300 font-semibold">{Math.round(pEloA * 100)}%</span>
                </td>
                <td className="px-3 py-1.5 text-right border-l border-gray-800">
                  <OddsCell value={pin?.a ?? null} />
                </td>
                <td className="px-3 py-1.5 text-right">
                  {pin
                    ? <span className="text-blue-300">{Math.round(pin.away * 100)}%</span>
                    : <span className="text-gray-700">—</span>}
                </td>
                <td className="px-3 py-1.5 text-right">
                  {pin ? <EdgeCell elo={pEloA} market={pin.away} /> : <span className="text-gray-700">—</span>}
                </td>
                <td className="px-3 py-1.5 text-right border-l border-gray-800">
                  <OddsCell value={fr.away} highlight={!!pin && !!fr.away && fr.away > pin.a!} />
                </td>
              </tr>,
            ]
          })}
        </tbody>
      </table>

      <div className="flex items-center gap-6 px-3 py-3 border-t border-gray-800 flex-wrap text-[10px]">
        <span className="text-gray-600">P% nette = prob. implicite Pinnacle vig-removed, draw splitté 50/50</span>
        <span className="text-yellow-600">Meil. FR en blanc = meilleure que Pinnacle (opportunité d&apos;arbitrage)</span>
        <div className="flex items-center gap-3 ml-auto">
          <span className="text-emerald-400">■</span><span className="text-gray-500">Edge ≥+5%</span>
          <span className="text-red-400">■</span><span className="text-gray-500">Sous-coté ≥5%</span>
        </div>
      </div>
    </div>
  )
}

// ── Main page ──────────────────────────────────────────────────────────────────

type Tab = 'classement' | 'croisements' | 'arbre' | 'valeur' | 'backtest'
type SortKey = 'elo' | 'p_r32' | 'p_r16' | 'p_qf' | 'p_sf' | 'p_finalist' | 'p_winner' | 'e_games'

export default function WC2026BracketPage() {
  const [tab, setTab] = useState<Tab>('classement')
  const [teams, setTeams] = useState<WCTeamAdvancement[]>([])
  const [fixtures, setFixtures] = useState<FixtureOut[]>([])
  const [loading, setLoading] = useState(true)
  const [sortKey, setSortKey] = useState<SortKey>('elo')
  const [showEliminated, setShowEliminated] = useState(true)
  const [computedAt, setComputedAt] = useState<string | null>(null)
  const [manualHome, setManualHome] = useState('')
  const [manualAway, setManualAway] = useState('')
  const [h2hMatches, setH2hMatches] = useState<KOMatchH2H[]>([])
  const [predictions, setPredictions] = useState<KOPrediction[]>([])

  async function load() {
    setLoading(true)
    try {
      const [adv, fx, h2h, preds] = await Promise.all([
        getWCAdvancement(),
        getFixtures({ league: 'world_cup_2026', limit: 200 }),
        getWCKoH2H(),
        getKOPredictions(),
      ])
      setTeams(adv)
      setFixtures(fx.fixtures)
      setH2hMatches(h2h)
      setPredictions(preds)
      if (adv.length > 0) setComputedAt(adv[0].computed_at)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const byNation = useMemo(
    () => Object.fromEntries(teams.map(t => [t.nation, t])),
    [teams],
  )

  // Croisements tab: only real-team fixtures
  const realFixtures = useMemo(
    () =>
      fixtures
        .filter(f => !isPlaceholder(f.home_team) && !isPlaceholder(f.away_team))
        .sort((a, b) => new Date(a.kickoff_utc).getTime() - new Date(b.kickoff_utc).getTime()),
    [fixtures],
  )
  const upcomingFixtures = realFixtures.filter(f => f.status !== 'finished')
  const finishedFixtures = realFixtures.filter(f => f.status === 'finished')

  const sorted = useMemo(() => {
    const filtered = showEliminated ? teams : teams.filter(t => !isEliminated(t))
    return [...filtered].sort((a, b) => b[sortKey] - a[sortKey])
  }, [teams, sortKey, showEliminated])

  function Th({ label, k, className }: { label: string; k: SortKey; className?: string }) {
    const active = sortKey === k
    return (
      <th
        className={clsx(
          'px-3 py-2 text-right text-[10px] font-semibold uppercase tracking-wider cursor-pointer select-none whitespace-nowrap',
          active ? 'text-orange-400' : 'text-gray-500 hover:text-gray-300',
          className,
        )}
        onClick={() => setSortKey(k)}
      >
        {label}{active && ' ↓'}
      </th>
    )
  }

  const formattedAt = computedAt
    ? new Date(computedAt).toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
    : null

  const TAB_LABELS: Record<Tab, string> = {
    classement: 'Classement ELO',
    croisements: 'Croisements',
    arbre: 'Arbre',
    valeur: 'Valeur ELO',
    backtest: 'Backtest',
  }

  return (
    <div className="p-4 flex flex-col h-full gap-4">
      {/* Header */}
      <div className="flex items-center gap-3 flex-wrap">
        <h2 className="text-sm font-semibold text-white">Bracket CDM 2026 — ELO & probabilités</h2>
        {formattedAt && (
          <span className="text-[10px] text-gray-500">calculé le {formattedAt}</span>
        )}
        <button
          onClick={load}
          disabled={loading}
          className="ml-auto flex items-center gap-1.5 px-2.5 py-1.5 bg-gray-700 hover:bg-gray-600 disabled:opacity-40 text-white text-xs rounded-lg transition-colors"
        >
          <RefreshCw className={clsx('w-3 h-3', loading && 'animate-spin')} />
          Rafraîchir
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-gray-700">
        {(['classement', 'croisements', 'arbre', 'valeur', 'backtest'] as Tab[]).map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={clsx(
              'px-4 py-2 text-xs font-medium capitalize transition-colors border-b-2 -mb-px',
              tab === t
                ? 'border-orange-500 text-orange-400'
                : 'border-transparent text-gray-400 hover:text-gray-200',
            )}
          >
            {TAB_LABELS[t]}
          </button>
        ))}
      </div>

      {/* ── Tab: Classement ── */}
      {tab === 'classement' && (
        <div className="flex-1 flex flex-col gap-3 overflow-hidden">
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={showEliminated}
                onChange={e => setShowEliminated(e.target.checked)}
                className="accent-orange-400"
              />
              <span className="text-xs text-gray-400">Afficher les équipes éliminées</span>
            </label>
            <span className="text-[10px] text-gray-600">{sorted.length} équipes</span>
          </div>

          <div className="flex-1 overflow-auto">
            {loading ? (
              <p className="text-gray-500 text-sm p-4">Chargement…</p>
            ) : teams.length === 0 ? (
              <p className="text-gray-500 text-sm p-4">Aucune donnée — le job bracket n&apos;a pas encore tourné.</p>
            ) : (
              <table className="w-full text-xs border-collapse">
                <thead className="sticky top-0 bg-gray-900 z-10">
                  <tr className="border-b border-gray-700">
                    <th className="px-3 py-2 text-left text-[10px] font-semibold text-gray-500 uppercase tracking-wider w-6">#</th>
                    <th className="px-3 py-2 text-left text-[10px] font-semibold text-gray-500 uppercase tracking-wider">Nation</th>
                    <Th label="ELO" k="elo" className="text-left" />
                    <Th label="Groupes" k="p_r32" />
                    <Th label="R32" k="p_r16" />
                    <Th label="R16" k="p_qf" />
                    <Th label="QF" k="p_sf" />
                    <Th label="SF" k="p_finalist" />
                    <Th label="Finale" k="p_winner" />
                    <Th label="E[matchs]" k="e_games" />
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((t, i) => {
                    const eliminated = isEliminated(t)
                    return (
                      <tr
                        key={t.nation}
                        className={clsx(
                          'border-b border-gray-800/60 hover:bg-gray-800/40 transition-colors',
                          eliminated && 'opacity-40',
                        )}
                      >
                        <td className="px-3 py-2 text-gray-600 tabular-nums">{i + 1}</td>
                        <td className="px-3 py-2">
                          <div className="flex items-center gap-2">
                            <span className="text-base leading-none">{NATION_FLAGS[t.nation] ?? '🏳'}</span>
                            <span className={clsx('font-medium', eliminated ? 'text-gray-500 line-through' : 'text-gray-200')}>{t.nation}</span>
                            {eliminated && <span className="text-[9px] text-red-500 font-bold uppercase">élim.</span>}
                          </div>
                        </td>
                        <td className="px-3 py-2 text-left"><EloChip elo={t.elo} /></td>
                        <td className="px-3 py-2"><ProbBar value={t.p_r32} color="bg-blue-500" /></td>
                        <td className="px-3 py-2"><ProbBar value={t.p_r16} color="bg-cyan-500" /></td>
                        <td className="px-3 py-2"><ProbBar value={t.p_qf} color="bg-teal-500" /></td>
                        <td className="px-3 py-2"><ProbBar value={t.p_sf} color="bg-yellow-500" /></td>
                        <td className="px-3 py-2"><ProbBar value={t.p_finalist} color="bg-orange-500" /></td>
                        <td className="px-3 py-2"><ProbBar value={t.p_winner} color="bg-emerald-500" /></td>
                        <td className="px-3 py-2 text-right">
                          <span className="tabular-nums text-gray-300">{t.e_games.toFixed(2)}</span>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {/* ── Tab: Croisements ── */}
      {tab === 'croisements' && (
        <div className="flex-1 overflow-auto flex flex-col gap-6 pb-4">
          {loading ? (
            <p className="text-gray-500 text-sm">Chargement…</p>
          ) : (
            <>
              {upcomingFixtures.length > 0 && (
                <section>
                  <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
                    À venir ({upcomingFixtures.length})
                  </h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                    {upcomingFixtures.map(f => (
                      <MatchupCard
                        key={f.id}
                        home={f.home_team}
                        away={f.away_team}
                        byNation={byNation}
                        kickoff={f.kickoff_utc}
                      />
                    ))}
                  </div>
                </section>
              )}

              {finishedFixtures.length > 0 && (
                <section>
                  <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
                    Joués ({finishedFixtures.length})
                  </h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                    {finishedFixtures.map(f => (
                      <MatchupCard
                        key={f.id}
                        home={f.home_team}
                        away={f.away_team}
                        byNation={byNation}
                        kickoff={f.kickoff_utc}
                        finished
                        homeScore={f.home_score}
                        awayScore={f.away_score}
                      />
                    ))}
                  </div>
                </section>
              )}

              <section className="border-t border-gray-700 pt-6">
                <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
                  Comparer deux équipes
                </h3>
                <div className="flex gap-3 items-center flex-wrap mb-4">
                  <div className="w-52">
                    <TeamSelect value={manualHome} onChange={setManualHome} teams={teams} placeholder="Équipe A" />
                  </div>
                  <span className="text-gray-500 text-sm font-bold">vs</span>
                  <div className="w-52">
                    <TeamSelect value={manualAway} onChange={setManualAway} teams={teams} placeholder="Équipe B" />
                  </div>
                  {(manualHome || manualAway) && (
                    <button
                      onClick={() => { setManualHome(''); setManualAway('') }}
                      className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
                    >
                      Effacer
                    </button>
                  )}
                </div>
                {manualHome && manualAway && manualHome !== manualAway && (
                  <div className="max-w-sm">
                    <MatchupCard home={manualHome} away={manualAway} byNation={byNation} />
                  </div>
                )}
              </section>

              {realFixtures.length === 0 && (
                <p className="text-gray-500 text-sm">
                  Aucun match WC2026 avec des équipes réelles dans le calculateur.
                </p>
              )}
            </>
          )}
        </div>
      )}

      {/* ── Tab: Arbre ── */}
      {tab === 'arbre' && (
        <div className="flex-1 overflow-auto">
          {loading ? (
            <p className="text-gray-500 text-sm p-4">Chargement…</p>
          ) : (
            <BracketTree fixtures={fixtures} byNation={byNation} />
          )}
        </div>
      )}

      {/* ── Tab: Valeur ELO ── */}
      {tab === 'valeur' && (
        <div className="flex-1 overflow-hidden flex flex-col">
          {loading ? (
            <p className="text-gray-500 text-sm p-4">Chargement…</p>
          ) : (
            <ValeurTab h2hMatches={h2hMatches} byNation={byNation} />
          )}
        </div>
      )}

      {/* ── Tab: Backtest ── */}
      {tab === 'backtest' && (
        <div className="flex-1 overflow-auto">
          {loading ? (
            <p className="text-gray-500 text-sm p-4">Chargement…</p>
          ) : (
            <BacktestTab predictions={predictions} />
          )}
        </div>
      )}
    </div>
  )
}
