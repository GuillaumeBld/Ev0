'use client'

import { clsx } from 'clsx'
import { Check } from 'lucide-react'
import styles from './atlas.module.css'
import { ClubCrest } from './ClubCrest'
import type { PlayerSummary } from '@/lib/atlas'

export function ratingColor(r: number | null | undefined): string {
  if (r == null) return 'var(--t2)'
  return r >= 7.6 ? 'var(--a)' : r >= 7 ? 'var(--a2)' : 'var(--t2)'
}

export function ratingBg(r: number | null | undefined): string {
  if (r == null) return 'var(--bd)'
  return r >= 7.6 ? 'var(--a-soft)' : r >= 7 ? 'rgba(111,147,184,.16)' : 'var(--bd)'
}

function fmt(v: number | null | undefined, decimals = 1): string {
  return v == null ? '—' : v.toFixed(decimals)
}

interface PlayerTileProps {
  player: PlayerSummary
  selected: boolean
  compareMode: boolean
  onClick: () => void
}

function Tick({ on }: { on: boolean }) {
  return (
    <div className={clsx(styles.ctick, on && styles.on)}>
      <Check />
    </div>
  )
}

export function PlayerCard({ player: p, selected, compareMode, onClick }: PlayerTileProps) {
  return (
    <div className={clsx(styles.pcard, selected && styles.on)} onClick={onClick}>
      <div className={styles.r1}>
        {compareMode ? <Tick on={selected} /> : (
          <ClubCrest crestUrl={p.crest_url} apiFootballId={p.api_football_id} name={p.team_name ?? ''} size={36} />
        )}
        <div>
          <div className={styles.nm}>{p.name}</div>
          <div className={styles.sub}>
            {p.position && <span className={clsx(styles.pos, styles[p.position])}>{p.position}</span>}
            {p.team_name ?? '—'}
          </div>
        </div>
        <span className={styles.rt} style={{ background: ratingBg(p.avg_rating), color: ratingColor(p.avg_rating) }}>
          {fmt(p.avg_rating)}
        </span>
      </div>
      <div className={styles.r2}>
        <div className={styles.st}><span className={styles.v}>{p.goals ?? '—'}</span><span className={styles.l}>Buts</span></div>
        <div className={styles.st}><span className={styles.v}>{p.goal_assist ?? '—'}</span><span className={styles.l}>Passes d.</span></div>
        <div className={styles.st}><span className={styles.v}>{fmt(p.xg_per_90, 2)}</span><span className={styles.l}>xG/90</span></div>
        <div className={styles.st}><span className={styles.v}>{p.matches_played ?? '—'}</span><span className={styles.l}>Matchs</span></div>
      </div>
    </div>
  )
}

export function PlayerRow({ player: p, selected, compareMode, onClick }: PlayerTileProps) {
  return (
    <div className={clsx(styles.prow, selected && styles.on)} onClick={onClick}>
      {compareMode ? <Tick on={selected} /> : (
        <ClubCrest crestUrl={p.crest_url} apiFootballId={p.api_football_id} name={p.team_name ?? ''} size={34} />
      )}
      <div className={styles.who}>
        <div className={styles.nm}>{p.name}</div>
        <div className={styles.sub}>
          {p.position && <span className={clsx(styles.pos, styles[p.position])}>{p.position}</span>}
          {p.team_name ?? '—'}
        </div>
      </div>
      <span className={styles.rt} style={{ background: ratingBg(p.avg_rating), color: ratingColor(p.avg_rating) }}>
        {fmt(p.avg_rating)}
      </span>
      <div className={styles.stats}>
        <div className={styles.s}><div className={styles.v}>{p.goals ?? '—'}</div><div className={styles.l}>Buts</div></div>
        <div className={styles.s}><div className={styles.v}>{p.goal_assist ?? '—'}</div><div className={styles.l}>PD</div></div>
        <div className={styles.s}><div className={styles.v}>{fmt(p.xg_per_90, 2)}</div><div className={styles.l}>xG/90</div></div>
        <div className={styles.s}><div className={styles.v}>{fmt(p.xa_per_90, 2)}</div><div className={styles.l}>xA/90</div></div>
        <div className={styles.s}><div className={styles.v}>{p.matches_played ?? '—'}</div><div className={styles.l}>Matchs</div></div>
      </div>
    </div>
  )
}
