'use client'

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { clsx } from 'clsx'
import { X, ChevronDown } from 'lucide-react'
import styles from './atlas.module.css'
import { ClubCrest } from './ClubCrest'
import { ratingColor } from './PlayerCard'
import { fetchPlayer, fetchCareer } from '@/lib/atlas'
import type { SeasonStatsOut } from '@/lib/api'

type Field = keyof SeasonStatsOut

interface MetricRow {
  label: string
  field: Field
  sub?: string
  /** Pour la barre de progression — null = pas de barre (ex: xG brut). */
  max?: number
  /** Multiplie par 100 et affiche en % (les ratios backend sont 0..1). */
  pct?: boolean
  decimals?: number
}

interface Group {
  key: string
  title: string
  icon: string
  color: string
  kpi: (s: SeasonStatsOut) => string
  rows: MetricRow[]
}

const GROUPS: Group[] = [
  {
    key: 'fin', title: 'Finition', icon: '⚽', color: 'var(--crit)',
    kpi: (s) => `${s.goals ?? 0} buts`,
    rows: [
      { label: 'Buts', field: 'goals', sub: 'sur la saison', max: 30 },
      { label: 'xG (buts attendus)', field: 'expected_goals', decimals: 1 },
      { label: 'Tirs', field: 'total_shots', sub: 'total', max: 80 },
      { label: 'Tirs cadrés', field: 'shots_on_target', max: 40 },
      { label: 'Taux de conversion', field: 'shot_accuracy', sub: '% tirs cadrés', pct: true, max: 100 },
    ],
  },
  {
    key: 'cre', title: 'Création', icon: '🎯', color: 'var(--a2)',
    kpi: (s) => `${s.goal_assist ?? 0} passes d.`,
    rows: [
      { label: 'Passes décisives', field: 'goal_assist', max: 15 },
      { label: 'xA (passes attendues)', field: 'expected_assists', decimals: 1 },
      { label: 'Passes clés', field: 'key_pass', sub: 'créent un tir', max: 60 },
      { label: 'Passes clés / 90', field: 'key_pass_per_90', decimals: 2 },
    ],
  },
  {
    key: 'dis', title: 'Distribution', icon: '🔀', color: 'var(--a)',
    kpi: () => '',
    rows: [
      { label: 'Passes réussies', field: 'pass_completion', sub: '%', pct: true, max: 100 },
      { label: 'Centres réussis / 90', field: 'accurate_cross_per_90', decimals: 2 },
      { label: 'Précision longues passes', field: 'long_ball_accuracy', sub: '%', pct: true, max: 100 },
      { label: 'Précision centres', field: 'cross_accuracy', sub: '%', pct: true, max: 100 },
    ],
  },
  {
    key: 'def', title: 'Défense', icon: '🛡️', color: '#8aa0ff',
    kpi: () => '',
    rows: [
      { label: 'Tacles / 90', field: 'tackles_per_90', decimals: 1, max: 5 },
      { label: 'Interceptions / 90', field: 'interceptions_per_90', decimals: 1, max: 4 },
      { label: 'Récupérations / 90', field: 'recoveries_per_90', decimals: 1, max: 10 },
      { label: 'Duels gagnés', field: 'duel_win_rate', sub: '%', pct: true, max: 100 },
      { label: 'Duels aériens gagnés', field: 'aerial_win_rate', sub: '%', pct: true, max: 100 },
      { label: 'Taux de réussite tacles', field: 'tackle_success_rate', sub: '%', pct: true, max: 100 },
    ],
  },
  {
    key: 'dsc', title: 'Discipline', icon: '🟨', color: 'var(--warn)',
    kpi: () => '',
    rows: [
      { label: 'Cartons jaunes', field: 'yellow_card', max: 12 },
      { label: 'Cartons rouges', field: 'red_card', max: 2 },
    ],
  },
  {
    key: 'jeu', title: 'Temps de jeu', icon: '⏱️', color: 'var(--t2)',
    kpi: (s) => `${s.minutes_played ?? 0} min`,
    rows: [
      { label: 'Minutes jouées', field: 'minutes_played', max: 3000 },
      { label: 'Matchs', field: 'matches_played', max: 40 },
      { label: 'Titularisations', field: 'starts', max: 40 },
      { label: '% titularisation', field: 'starts_pct', sub: '%', pct: true, max: 100 },
    ],
  },
]

function Metric({ row, stats }: { row: MetricRow; stats: SeasonStatsOut }) {
  const raw = stats[row.field]
  if (raw == null) return null
  const value = typeof raw === 'number' ? raw : null
  if (value == null) return null
  const display = row.pct
    ? `${(value * 100).toFixed(row.decimals ?? 0)}%`
    : value.toFixed(row.decimals ?? 0)
  const trackVal = row.pct ? value * 100 : value
  const pct = row.max ? Math.min((trackVal / row.max) * 100, 100) : 0
  return (
    <div className={styles.metric}>
      <div className={styles.ml}>
        <div className={styles.mn}>{row.label}</div>
        {row.sub && !row.pct && <div className={styles.md}>{row.sub}</div>}
      </div>
      {row.max != null && (
        <div className={styles.track}><i style={{ width: `${pct}%` }} /></div>
      )}
      <div className={styles.mv}>{display}</div>
    </div>
  )
}

function Ring({ note }: { note: number | null }) {
  const r = 30
  const c = 2 * Math.PI * r
  const off = note == null ? c : c * (1 - Math.min(note / 10, 1))
  const color = ratingColor(note)
  return (
    <div className={styles.ring}>
      <svg viewBox="0 0 70 70">
        <circle cx="35" cy="35" r={r} fill="none" stroke="var(--bd)" strokeWidth="6" />
        <circle
          cx="35" cy="35" r={r} fill="none" stroke={color} strokeWidth="6"
          strokeLinecap="round" strokeDasharray={c} strokeDashoffset={off}
          transform="rotate(-90 35 35)"
        />
      </svg>
      <div className={styles.rv} style={{ color }}>{note != null ? note.toFixed(1) : '—'}</div>
    </div>
  )
}

export function PlayerDetail({ playerApiId, onClose }: { playerApiId: number | null; onClose: () => void }) {
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set(['fin', 'cre']))
  const [careerOpen, setCareerOpen] = useState(false)

  const { data: player, isLoading, isError, refetch } = useQuery({
    queryKey: ['atlas-player', playerApiId],
    queryFn: () => fetchPlayer(playerApiId as number),
    enabled: playerApiId != null,
  })

  const { data: career, isLoading: careerLoading, isError: careerIsError } = useQuery({
    queryKey: ['atlas-career', playerApiId],
    queryFn: () => fetchCareer(playerApiId as number),
    enabled: playerApiId != null && careerOpen,
  })

  const open = playerApiId != null

  function toggleGroup(key: string) {
    setOpenGroups((s) => {
      const next = new Set(s)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  return (
    <>
      <div className={clsx(styles.scrim, open && styles.on)} onClick={onClose} />
      <aside className={clsx(styles.pdet, open && styles.on)}>
        {open && (
          isError ? (
            <div className={styles.phd}>
              <div className={styles.x} onClick={onClose}><X size={16} /></div>
              <p style={{ color: 'var(--t2)' }}>Impossible de charger ce joueur.</p>
              <button className={styles.chip} onClick={() => refetch()}>Réessayer</button>
            </div>
          ) : isLoading || !player ? (
            <div className={styles.phd}>
              <div className={styles.x} onClick={onClose}><X size={16} /></div>
              <p style={{ color: 'var(--t2)' }}>Chargement…</p>
            </div>
          ) : (
            <>
              <div className={styles.phd}>
                <div className={styles.x} onClick={onClose}><X size={16} /></div>
                <div className={styles.top}>
                  <ClubCrest crestUrl={player.crest_url} apiFootballId={player.api_football_id} name={player.team_name ?? ''} size={52} />
                  <div>
                    <h2>{player.name}</h2>
                    <div className={styles.meta}>
                      {player.position && <span className={clsx(styles.pos, styles[player.position])}>{player.position}</span>}
                      {player.team_name ?? '—'}
                      {!player.season_stats && <b>· couverture partielle</b>}
                    </div>
                  </div>
                  <Ring note={player.season_stats?.avg_rating ?? null} />
                </div>
              </div>
              <div className={styles.pbody}>
                {GROUPS.map((g) => {
                  const stats = player.season_stats
                  const has = !!stats
                  const isOpen = openGroups.has(g.key)
                  return (
                    <div key={g.key} className={clsx(styles.seg2, isOpen && styles.open, !has && styles.na)}>
                      <div className={styles.sh} onClick={() => toggleGroup(g.key)}>
                        <div className={styles.ico} style={{ background: `${g.color}22` }}>{g.icon}</div>
                        <h3>{g.title}</h3>
                        <span className={styles.k}>{stats ? g.kpi(stats) : ''}</span>
                        <ChevronDown className={styles.chev} width={16} height={16} />
                      </div>
                      <div className={styles.bd}>
                        {has ? (
                          g.rows.map((row) => <Metric key={row.field} row={row} stats={stats as SeasonStatsOut} />)
                        ) : (
                          <div className={styles.nainfo}>Données avancées non disponibles pour ce championnat.</div>
                        )}
                      </div>
                    </div>
                  )
                })}
                <details className={styles.seg2} onToggle={(e) => setCareerOpen((e.target as HTMLDetailsElement).open)}>
                  <summary className={styles.sh} style={{ listStyle: 'none', cursor: 'pointer' }}>
                    <div className={styles.ico} style={{ background: 'var(--bd)' }}>📅</div>
                    <h3>Historique de carrière</h3>
                    <ChevronDown className={styles.chev} width={16} height={16} />
                  </summary>
                  <div className={styles.bd}>
                    {careerIsError && (
                      <div className={styles.nainfo}>Erreur de chargement de l’historique de carrière.</div>
                    )}
                    {!careerIsError && careerLoading && <p style={{ color: 'var(--t2)', fontSize: 12.5, padding: '10px 0' }}>Chargement…</p>}
                    {!careerIsError && career && career.career.length === 0 && (
                      <div className={styles.nainfo}>Historique de carrière non disponible pour ce joueur.</div>
                    )}
                    {career?.career.map((s) => (
                      <div className={styles.metric} key={s.season}>
                        <div className={styles.ml}>
                          <div className={styles.mn}>{s.season} · {s.competitions.map((c) => c.competition).filter(Boolean).join(', ') || '—'}</div>
                        </div>
                        <div className={styles.mv}>{s.goals} B · {s.assists} PD</div>
                      </div>
                    ))}
                  </div>
                </details>
              </div>
            </>
          )
        )}
      </aside>
    </>
  )
}
