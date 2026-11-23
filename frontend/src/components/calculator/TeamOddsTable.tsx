'use client'

import { useState } from 'react'
import type { PlayerAllocationOut, TeamBadgeOut } from '@/lib/api'
import { TeamBadge } from '@/components/TeamBadge'
import { PlayerLink } from '@/components/PlayerLink'
import { BOOKS, ev0Odds, fmtOdds, fmtXg, isValue, type BookOddsByPlayer, type Market } from './format'
import { useCrestColor } from './useCrestColor'
import s from './calculateur.module.css'

export type TeamMode = 'squad' | 'xi'
type SortCol = 'alpha' | 'beta' | 'betclic' | 'unibet' | 'pmu'
type SortKey = 'name' | `${Market}-${SortCol}`

const cx = (...names: (string | false | null | undefined)[]) =>
  names
    .filter(Boolean)
    .map((n) => s[n as string] ?? n)
    .join(' ')

export function TeamOddsTable({
  name,
  badge,
  xg,
  mode,
  onMode,
  squad,
  lineup,
  starterIds,
  onToggleStarter,
  note,
  bookOdds,
  penTakerId,
  onPenTaker,
}: {
  name: string
  badge: TeamBadgeOut | null | undefined
  xg: number
  mode: TeamMode
  onMode: (m: TeamMode) => void
  /** Cotes de l'effectif complet. */
  squad: PlayerAllocationOut[]
  /** Cotes recalculées avec les titulaires cochés (null tant que non calculées). */
  lineup: PlayerAllocationOut[] | null
  starterIds: number[]
  onToggleStarter: (playerId: number) => void
  /** Ligne courte sous l'en-tête en mode Compo (signalements de l'API inclus). */
  note: string | null
  bookOdds: BookOddsByPlayer
  /** Tireur de penalty choisi à la main (null = détection automatique). */
  penTakerId: number | null
  onPenTaker: (playerId: number) => void
}) {
  const [r, g, b] = useCrestColor(badge)
  const isXi = mode === 'xi'
  // Tri : colonne active et sens ; par défaut cote Alpha buteur croissante. « — » toujours en bas.
  const [sortKey, setSortKey] = useState<SortKey>('goal-alpha')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')
  const clickSort = (k: SortKey) => {
    if (k === sortKey) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    else {
      setSortKey(k)
      setSortDir('asc')
    }
  }
  const value = (p: PlayerAllocationOut, priced: boolean): string | number | null => {
    if (sortKey === 'name') return p.player_name
    const [market, col] = sortKey.split('-') as [Market, SortCol]
    if (col === 'alpha' || col === 'beta') return priced ? ev0Odds(p, market)[col] : null
    return bookOdds.get(p.player_id)?.[market]?.[col] ?? null
  }
  const sortRows = <T,>(rows: T[], get: (r: T) => [PlayerAllocationOut, boolean]): T[] => {
    const sign = sortDir === 'asc' ? 1 : -1
    return [...rows].sort((a, c) => {
      const va = value(...get(a))
      const vc = value(...get(c))
      if (va == null || vc == null) return va == null && vc == null ? 0 : va == null ? 1 : -1
      if (typeof va === 'string' || typeof vc === 'string') return sign * String(va).localeCompare(String(vc), 'fr')
      return sign * (va - vc)
    })
  }

  let starters: { p: PlayerAllocationOut; priced: boolean }[] = []
  let bench: PlayerAllocationOut[] = []
  if (!isXi) {
    starters = squad.map((p) => ({ p, priced: true }))
  } else {
    const chosen = new Set(starterIds)
    const lineupById = new Map((lineup ?? []).map((p) => [p.player_id, p]))
    starters = squad
      .filter((p) => chosen.has(p.player_id))
      .map((p) => {
        const lp = lineupById.get(p.player_id)
        return lp ? { p: lp, priced: true } : { p, priced: false }
      })
    bench = squad.filter((p) => !chosen.has(p.player_id))
  }
  starters = sortRows(starters, (r) => [r.p, r.priced])
  bench = sortRows(bench, (p) => [p, false])

  const isTaker = (p: PlayerAllocationOut) => (penTakerId != null ? p.player_id === penTakerId : p.is_pen_taker)

  const sortTh = (k: SortKey, label: string, classes: (string | false)[]) => (
    <th key={k} className={cx(...classes)} aria-sort={sortKey === k ? (sortDir === 'asc' ? 'ascending' : 'descending') : undefined}>
      <button className={s.sortBtn} onClick={() => clickSort(k)}>
        {label}
        {sortKey === k && <span className={s.sortArrow}>{sortDir === 'asc' ? '▲' : '▼'}</span>}
      </button>
    </th>
  )

  const cells = (p: PlayerAllocationOut, market: Market, priced: boolean) => {
    const { alpha, beta } = priced ? ev0Odds(p, market) : { alpha: null, beta: null }
    const books = bookOdds.get(p.player_id)?.[market] ?? {}
    return [
      <td key={`${market}-a`} className={cx('s', 'al')}>{fmtOdds(alpha)}</td>,
      <td key={`${market}-b`} className={cx('be')}>{fmtOdds(beta)}</td>,
      ...BOOKS.map((bk, i) => {
        const o = books[bk]
        const cls = o == null ? 'dn' : isValue(o, alpha, beta) ? 'up' : null
        return (
          <td key={`${market}-${bk}`} className={cx(i === 0 && 's', 'book', cls)}>
            {fmtOdds(o)}
          </td>
        )
      }),
    ]
  }

  return (
    <div className={cx('panel', 'teamPanel')}>
      <div
        className={s.theadTeam}
        style={{ background: `linear-gradient(90deg, rgba(${r},${g},${b},.4), rgba(${r},${g},${b},0) 70%)` }}
      >
        <span
          className={s.disc}
          style={{ width: 30, height: 30, filter: `drop-shadow(0 4px 12px rgba(${r},${g},${b},.45))` }}
        >
          <TeamBadge badge={badge} name={name} size={30} />
        </span>
        <span style={{ fontSize: 16, fontWeight: 700 }}>{name}</span>
        <span style={{ fontSize: 13, color: '#e6edf3' }}>
          xG <b>{fmtXg(xg)}</b>
        </span>
        <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 10 }}>
          {isXi && (
            <span style={{ fontSize: 12, color: '#e6edf3' }}>{starters.length} / 10 joueurs de champ</span>
          )}
          <span className={s.seg} role="group" aria-label="Mode de calcul">
            {(
              [
                ['squad', 'Effectif'],
                ['xi', 'Compo'],
              ] as const
            ).map(([v, label]) => (
              <button key={v} className={mode === v ? s.on : ''} onClick={() => onMode(v)}>
                {label}
              </button>
            ))}
          </span>
        </span>
      </div>
      {isXi && note && (
        <div className={s.faint} style={{ fontSize: 12, padding: '0 14px 8px' }}>
          {note}
        </div>
      )}
      <div className={s.scrollX}>
        <table className={s.table}>
          <thead>
            <tr className={s.top}>
              <th className={s.l}></th>
              <th className={cx('s', 'gB')} colSpan={5}>
                Buteur
              </th>
              <th className={cx('s', 'gP')} colSpan={5}>
                Passeur
              </th>
            </tr>
            <tr>
              {sortTh('name', 'Joueur', ['l'])}
              {(['goal', 'assist'] as const).map((m) => [
                sortTh(`${m}-alpha`, 'Alpha', ['s', 'al']),
                sortTh(`${m}-beta`, 'Beta', ['be']),
                sortTh(`${m}-betclic`, 'Betclic', ['s', 'bk']),
                sortTh(`${m}-unibet`, 'Unibet', ['bk']),
                sortTh(`${m}-pmu`, 'PMU', ['bk']),
              ])}
            </tr>
          </thead>
          <tbody>
            {isXi && (
              <tr className={s.sec}>
                <td className={s.l} colSpan={11}>
                  Titulaires
                </td>
              </tr>
            )}
            {starters.map(({ p, priced }) => (
              <tr key={p.player_id}>
                <td className={cx('l', 'name')}>
                  {isXi && (
                    <input
                      type="checkbox"
                      checked
                      onChange={() => onToggleStarter(p.player_id)}
                      aria-label="Retirer des titulaires"
                    />
                  )}
                  <PlayerLink playerId={p.player_id} name={p.player_name} className={s.plink} />
                  <button
                    className={cx('pen', isTaker(p) && 'penOn')}
                    onClick={() => onPenTaker(p.player_id)}
                    aria-pressed={isTaker(p)}
                    title={isTaker(p) ? 'Tireur de penalty' : 'Désigner tireur de penalty'}
                  >
                    pen.
                  </button>
                </td>
                {cells(p, 'goal', priced)}
                {cells(p, 'assist', priced)}
              </tr>
            ))}
            {isXi && (
              <tr className={s.sec}>
                <td className={s.l} colSpan={11}>
                  Remplaçants
                </td>
              </tr>
            )}
            {bench.map((p) => (
              <tr key={p.player_id} className={s.bench}>
                <td className={cx('l', 'name')}>
                  <input
                    type="checkbox"
                    checked={false}
                    onChange={() => onToggleStarter(p.player_id)}
                    aria-label="Mettre titulaire"
                  />
                  <PlayerLink playerId={p.player_id} name={p.player_name} className={s.plink} />
                </td>
                {cells(p, 'goal', false)}
                {cells(p, 'assist', false)}
              </tr>
            ))}
            {squad.length === 0 && (
              <tr>
                <td className={s.l} colSpan={11}>
                  Aucun joueur dans l&apos;effectif
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
