'use client'

import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { clsx } from 'clsx'
import { ArrowLeft } from 'lucide-react'
import styles from './atlas.module.css'
import { ClubCrest } from './ClubCrest'
import { ratingColor, ratingBg } from './PlayerCard'
import { MiniChart, BarsGX, Radar, type GxBar, type RadarAxis } from './charts'
import { fetchPlayer, fetchCareer } from '@/lib/atlas'
import type { SeasonStatsOut, RecentMatch, CareerSeasonOut } from '@/lib/atlas'

/**
 * Fiche joueur plein écran ("grimoire") — portée depuis la fonction
 * `openPlayer(p)` de `docs/superpowers/atlas-reference-mockup.html` (même
 * structure/CSS : `.pdet` plein écran, `.phero`, `.ptiles`, `.pgrid` /
 * `.ppanel`, `.gcards`, `.mlog`, `.pcar`), mais câblée aux vraies données
 * `GET /players/{id}` et `GET /players/{id}/career` au lieu des valeurs
 * aléatoires de la maquette.
 */

// ── Mapping season_stats → 7 catégories ─────────────────────────────────────
// Chaque ligne est TOUJOURS rendue (jamais masquée) ; une valeur manquante
// affiche "n/d", jamais un 0 trompeur. Les "/90" absents côté backend sont
// recalculés à partir du total et de minutes_played/90 (voir `per90`).

interface MetricRow {
  label: string
  value: number | null
  max?: number
  pct?: boolean
  decimals?: number
}

interface Category {
  key: string
  title: string
  icon: string
  color: string
  rows: MetricRow[]
}

function per90(total: number | null | undefined, minutes: number | null | undefined): number | null {
  if (total == null || minutes == null || minutes <= 0) return null
  return total / (minutes / 90)
}

function buildCategories(s: SeasonStatsOut): Category[] {
  const goalsPer90 = per90(s.goals, s.minutes_played)
  const assistsPer90 = per90(s.goal_assist, s.minutes_played)
  const R = (
    label: string,
    value: number | null | undefined,
    max?: number,
    opts: { pct?: boolean; decimals?: number } = {},
  ): MetricRow => ({ label, value: value ?? null, max, ...opts })

  return [
    {
      key: 'fin', title: 'Finition', icon: '⚽', color: 'var(--crit)',
      rows: [
        R('Buts', s.goals, 30),
        R('Buts / 90', goalsPer90, 1.2, { decimals: 2 }),
        R('Buts (forme, 5 derniers)', s.form_goals_5, 6),
        R('xG (buts attendus)', s.expected_goals, undefined, { decimals: 1 }),
        R('xG / 90', s.xg_per_90, 1, { decimals: 2 }),
        R('xG (forme, 5 derniers)', s.form_xg_5, undefined, { decimals: 1 }),
        R('Buts − xG', s.finishing_delta, undefined, { decimals: 1 }),
        R('Tirs', s.total_shots, 100),
        R('Tirs / 90', s.shots_per_90, 6, { decimals: 1 }),
        R('Tirs cadrés', s.shots_on_target, 50),
        R('Tirs cadrés / 90', s.shots_on_target_per_90, 3, { decimals: 1 }),
        R('Précision des tirs', s.shot_accuracy, 100, { pct: true }),
        R('xG par tir', s.xg_per_shot, undefined, { decimals: 2 }),
      ],
    },
    {
      key: 'cre', title: 'Création', icon: '🎯', color: 'var(--a2)',
      rows: [
        R('Passes décisives', s.goal_assist, 20),
        R('Passes déc. / 90', assistsPer90, 1, { decimals: 2 }),
        R('Passes déc. (forme, 5 derniers)', s.form_assists_5, 5),
        R('xA (passes attendues)', s.expected_assists, undefined, { decimals: 1 }),
        R('xA / 90', s.xa_per_90, 0.6, { decimals: 2 }),
        R('xA − passes décisives', s.xa_delta, undefined, { decimals: 1 }),
        R('Passes clés', s.key_pass, 80),
        R('Passes clés / 90', s.key_pass_per_90, 4, { decimals: 1 }),
      ],
    },
    {
      key: 'dis', title: 'Distribution', icon: '🔀', color: 'var(--a)',
      rows: [
        R('Passes tentées', s.total_pass, 3000),
        R('Passes réussies', s.accurate_pass, 2800),
        R('Précision des passes', s.pass_completion, 100, { pct: true }),
        R('Longues passes tentées', s.total_long_balls, 300),
        R('Longues passes réussies', s.accurate_long_balls, 250),
        R('Précision longues passes', s.long_ball_accuracy, 100, { pct: true }),
        R('Centres tentés', s.total_cross, 150),
        R('Centres réussis', s.accurate_cross, 80),
        R('Centres réussis / 90', s.accurate_cross_per_90, 2, { decimals: 2 }),
        R('Précision des centres', s.cross_accuracy, 100, { pct: true }),
      ],
    },
    {
      key: 'def', title: 'Défense', icon: '🛡️', color: '#8aa0ff',
      rows: [
        R('Tacles', s.total_tackle, 150),
        R('Tacles gagnés', s.won_tackle, 120),
        R('Taux de réussite des tacles', s.tackle_success_rate, 100, { pct: true }),
        R('Tacles / 90', s.tackles_per_90, 5, { decimals: 1 }),
        R('Interceptions', s.interception, 100),
        R('Interceptions / 90', s.interceptions_per_90, 4, { decimals: 1 }),
        R('Récupérations', s.ball_recovery, 300),
        R('Récupérations / 90', s.recoveries_per_90, 10, { decimals: 1 }),
      ],
    },
    {
      key: 'duel', title: 'Duels & aérien', icon: '💪', color: '#6fb0a0',
      rows: [
        R('Duels gagnés', s.duel_won, 300),
        R('Duels perdus', s.duel_lost, 300),
        R('Taux de réussite des duels', s.duel_win_rate, 100, { pct: true }),
        R('Duels aériens gagnés', s.aerial_won, 150),
        R('Duels aériens perdus', s.aerial_lost, 150),
        R('Taux de réussite aérien', s.aerial_win_rate, 100, { pct: true }),
      ],
    },
    {
      key: 'dsc', title: 'Discipline', icon: '🟨', color: 'var(--warn)',
      rows: [
        R('Cartons jaunes', s.yellow_card, 12),
        R('Cartons rouges', s.red_card, 3),
      ],
    },
    {
      key: 'jeu', title: 'Temps de jeu', icon: '⏱️', color: 'var(--t2)',
      rows: [
        R('Matchs joués', s.matches_played, 38),
        R('Titularisations', s.starts, 38),
        R('% de titularisation', s.starts_pct, 100, { pct: true }),
        R('Minutes jouées', s.minutes_played, 3420),
        R('Minutes / match', s.avg_minutes_per_match, 90),
        R('Note moyenne', s.avg_rating, 10, { decimals: 1 }),
        R('Note (forme, 5 derniers)', s.form_rating_5, 10, { decimals: 1 }),
        R('Tendance de note', s.rating_trend, undefined, { decimals: 2 }),
      ],
    },
  ]
}

/**
 * Radar de profil (6 axes, 0-100). Calcul documenté — ce n'est pas un
 * percentile contre la ligue, juste une mise à l'échelle lisible à partir des
 * stats saison déjà chargées :
 *  - Finition : buts/90 (poids fort) + xG/90 (poids modéré)
 *  - Création : passes déc./90 (poids fort) + passes clés/90 (poids modéré)
 *  - Passes   : % de passes réussies, directement (ratio 0..1 -> 0..100)
 *  - Défense  : tacles/90 + interceptions/90
 *  - Duels    : % de duels gagnés, directement
 *  - Activité : minutes jouées rapportées à une saison pleine (38 x 90 min)
 * Champ manquant -> traité comme 0 sur cet axe (pas de n/d sur un radar).
 */
function buildRadar(s: SeasonStatsOut): RadarAxis[] {
  const clamp = (v: number) => Math.max(0, Math.min(100, v))
  const goalsPer90 = per90(s.goals, s.minutes_played) ?? 0
  const assistsPer90 = per90(s.goal_assist, s.minutes_played) ?? 0
  return [
    { label: 'Finition', value: clamp(goalsPer90 * 55 + (s.xg_per_90 ?? 0) * 25) },
    { label: 'Création', value: clamp(assistsPer90 * 65 + (s.key_pass_per_90 ?? 0) * 7) },
    { label: 'Passes', value: clamp((s.pass_completion ?? 0) * 100) },
    { label: 'Défense', value: clamp((s.tackles_per_90 ?? 0) * 12 + (s.interceptions_per_90 ?? 0) * 12) },
    { label: 'Duels', value: clamp((s.duel_win_rate ?? 0) * 100) },
    { label: 'Activité', value: clamp(((s.minutes_played ?? 0) / (38 * 90)) * 100) },
  ]
}

/** Champs "avancés" dont l'absence déclenche le badge "couverture partielle" —
 *  en-dessous des Big 5, ces métriques (xG/xA, tacles, duels…) manquent
 *  souvent alors que les stats basiques (buts, minutes) existent déjà. */
const ADVANCED_FIELDS: (keyof SeasonStatsOut)[] = [
  'expected_goals', 'expected_assists', 'key_pass_per_90',
  'tackles_per_90', 'interceptions_per_90', 'duel_win_rate', 'aerial_win_rate',
]

function hasPartialCoverage(s: SeasonStatsOut | null): boolean {
  if (!s) return true
  return ADVANCED_FIELDS.some((f) => s[f] == null)
}

function fmt(value: number | null, opts: { pct?: boolean; decimals?: number } = {}): string {
  if (value == null) return 'n/d'
  if (opts.pct) return `${(value * 100).toFixed(opts.decimals ?? 0)}%`
  return value.toFixed(opts.decimals ?? 0)
}

function Metric({ row }: { row: MetricRow }) {
  const value = row.value
  const na = value == null
  const trackVal = value == null ? 0 : row.pct ? value * 100 : value
  const pct = !na && row.max ? Math.min(Math.max((trackVal / row.max) * 100, 0), 100) : 0
  return (
    <div className={styles.metric}>
      <div className={styles.ml}>
        <div className={styles.mn}>{row.label}</div>
      </div>
      {!na && row.max != null && (
        <div className={styles.track}><i style={{ width: `${pct}%` }} /></div>
      )}
      <div className={clsx(styles.mv, na && styles.na)}>{fmt(row.value, row)}</div>
    </div>
  )
}

function Tile({ value, label, decimals = 0, thousands = false }: { value: number | null; label: string; decimals?: number; thousands?: boolean }) {
  const display = value == null ? '—' : thousands ? value.toLocaleString('fr-FR') : value.toFixed(decimals)
  return (
    <div className={styles.ptile}>
      <div className={styles.v}>{display}</div>
      <div className={styles.l}>{label}</div>
    </div>
  )
}

function Ring({ note, size = 84 }: { note: number | null; size?: number }) {
  const r = size / 2 - 7
  const c = 2 * Math.PI * r
  const off = note == null ? c : c * (1 - Math.min(Math.max(note, 0) / 10, 1))
  const color = ratingColor(note)
  return (
    <div className={styles.ring} style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--bd)" strokeWidth={7} />
        <circle
          cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={7}
          strokeLinecap="round" strokeDasharray={c} strokeDashoffset={off}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </svg>
      <div className={styles.rv} style={{ color, fontSize: size * 0.27 }}>{note != null ? note.toFixed(1) : '—'}</div>
    </div>
  )
}

/**
 * Dérive, à partir de `recent_matches` :
 *  - `sortedDesc` : historique match par match, du plus récent au plus ancien
 *    (matches sans date rejetés en fin de liste, ils ne peuvent pas être
 *    replacés chronologiquement).
 *  - `ratings` : notes en ordre chronologique croissant, pour la courbe.
 *  - `bars` : buts vs xG agrégés par mois calendaire (triés chronologiquement).
 *    Un mois n'a de xG "n/d" que si AUCUN de ses matchs n'a de expected_goals
 *    renseigné ; sinon on somme ce qui est connu (agrégat, pas une valeur
 *    isolée — ne tombe pas sous la règle "jamais de 0 trompeur").
 */
function useMatchSeries(matches: RecentMatch[]) {
  return useMemo(() => {
    const withDate = matches.filter((m) => m.event_date)
    const sortedAsc = [...withDate].sort((a, b) => (a.event_date as string).localeCompare(b.event_date as string))
    const sortedDesc = [...sortedAsc].reverse()
    const ratings = sortedAsc.map((m) => m.rating).filter((r): r is number => r != null)

    const months = new Map<string, { label: string; goals: number; xgSum: number; xgKnown: boolean }>()
    for (const m of sortedAsc) {
      const d = new Date(m.event_date as string)
      const key = `${d.getFullYear()}-${String(d.getMonth()).padStart(2, '0')}`
      const label = d.toLocaleDateString('fr-FR', { month: 'short' }).replace('.', '')
      const bucket = months.get(key) ?? { label, goals: 0, xgSum: 0, xgKnown: false }
      bucket.goals += m.goals ?? 0
      if (m.expected_goals != null) {
        bucket.xgSum += m.expected_goals
        bucket.xgKnown = true
      }
      months.set(key, bucket)
    }
    const bars: GxBar[] = Array.from(months.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([, v]) => ({ label: v.label, goals: v.goals, xg: v.xgKnown ? Math.round(v.xgSum * 10) / 10 : null }))

    return { sortedDesc, ratings, bars }
  }, [matches])
}

export function PlayerDetail({ playerApiId, onClose }: { playerApiId: number | null; onClose: () => void }) {
  const open = playerApiId != null

  const { data: player, isLoading, isError, refetch } = useQuery({
    queryKey: ['atlas-player', playerApiId],
    queryFn: () => fetchPlayer(playerApiId as number),
    enabled: open,
  })

  const { data: career, isLoading: careerLoading, isError: careerIsError } = useQuery({
    queryKey: ['atlas-career', playerApiId],
    queryFn: () => fetchCareer(playerApiId as number),
    enabled: open,
  })

  const [tab, setTab] = useState<'club' | 'selection' | 'career'>('club')
  const hasSelection = !!player?.selection_stats

  const matches = player?.recent_matches ?? []
  const { sortedDesc, ratings, bars } = useMatchSeries(matches)
  const categories = useMemo(() => (player?.season_stats ? buildCategories(player.season_stats) : []), [player?.season_stats])
  const radarAxes = useMemo(() => (player?.season_stats ? buildRadar(player.season_stats) : []), [player?.season_stats])
  const partial = hasPartialCoverage(player?.season_stats ?? null)
  const seasonLabel = player?.season_stats?.season
  const forme5 = ratings.slice(-5)
  const formeAvg = forme5.length ? forme5.reduce((a, b) => a + b, 0) / forme5.length : null

  return (
    <>
      <div className={clsx(styles.scrim, open && styles.on)} onClick={onClose} />
      <div className={clsx(styles.pdet, open && styles.on)}>
        {open && (
          isError ? (
            <div className={styles.pdwrap}>
              <div className={styles.pdtop}>
                <button className={styles.back} onClick={onClose}><ArrowLeft size={16} />Retour</button>
              </div>
              <p style={{ color: 'var(--t2)', marginTop: 40 }}>Impossible de charger ce joueur.</p>
              <button className={styles.chip} onClick={() => refetch()}>Réessayer</button>
            </div>
          ) : isLoading || !player ? (
            <div className={styles.pdwrap}>
              <div className={styles.pdtop}>
                <button className={styles.back} onClick={onClose}><ArrowLeft size={16} />Retour</button>
              </div>
              <p style={{ color: 'var(--t2)', marginTop: 40 }}>Chargement…</p>
            </div>
          ) : (
            <>
              <div className={styles.pdtop}>
                <button className={styles.back} onClick={onClose}><ArrowLeft size={16} />Retour</button>
                <span className={styles.nm}>{player.name}</span>
              </div>

              <div className={styles.phero}>
                <div className={styles.id}>
                  <ClubCrest crestUrl={player.crest_url} apiFootballId={player.api_football_id} name={player.team_name ?? ''} size={68} />
                  <div>
                    <h2>{player.name}</h2>
                    <div className={styles.meta}>
                      {player.position && <span className={clsx(styles.pos, styles[player.position])}>{player.position}</span>}
                      {player.team_name ?? '—'}
                      {seasonLabel && <span>· {seasonLabel}</span>}
                      {partial && <span className={styles.napill}>couverture partielle</span>}
                    </div>
                  </div>
                </div>
                <Ring note={player.season_stats?.avg_rating ?? null} />
                <div className={styles.ptiles}>
                  <Tile value={player.season_stats?.goals ?? null} label="Buts" />
                  <Tile value={player.season_stats?.goal_assist ?? null} label="Passes d." />
                  <Tile value={player.season_stats?.expected_goals ?? null} label="xG" decimals={1} />
                  <Tile value={player.season_stats?.matches_played ?? null} label="Matchs" />
                  <Tile value={player.season_stats?.minutes_played ?? null} label="Minutes" thousands />
                  <Tile value={player.season_stats?.avg_rating ?? null} label="Note" decimals={1} />
                </div>
              </div>

              <div className={styles.ptabs}>
                <button className={clsx(styles.ptab, tab === 'club' && styles.on)} onClick={() => setTab('club')}>Club</button>
                <button
                  className={clsx(styles.ptab, tab === 'selection' && styles.on)}
                  onClick={() => hasSelection && setTab('selection')}
                  disabled={!hasSelection}
                  title={hasSelection ? undefined : 'Aucune stat en sélection'}
                >
                  Sélection{player?.national_team_name ? ` · ${player.national_team_name}` : ''}
                </button>
                <button className={clsx(styles.ptab, tab === 'career' && styles.on)} onClick={() => setTab('career')}>Carrière</button>
              </div>

              <div className={styles.pdwrap}>
              {tab === 'club' && (
                <>
                <div className={styles.sect}>Statistiques détaillées</div>
                {categories.length === 0 ? (
                  <p style={{ color: 'var(--t2)', fontSize: 13 }}>Données avancées non disponibles pour ce championnat.</p>
                ) : (
                  <div className={styles.pgrid}>
                    {categories.map((cat) => (
                      <div className={styles.ppanel} key={cat.key}>
                        <div className={styles.ph}>
                          <div className={styles.ico} style={{ background: `${cat.color}22` }}>{cat.icon}</div>
                          <h3>{cat.title}</h3>
                        </div>
                        <div className={styles.pb}>
                          {cat.rows.map((row) => <Metric key={row.label} row={row} />)}
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                <div className={styles.sect}>Graphiques</div>
                <div className={styles.gcards}>
                  <div className={styles.gcard}>
                    <div className={styles.gt}>Évolution de la note</div>
                    <div className={styles.gsub}>{ratings.length} derniers matchs avec note</div>
                    <MiniChart values={ratings} />
                  </div>
                  <div className={styles.gcard}>
                    <div className={styles.gt}>Profil du joueur</div>
                    <div className={styles.gsub}>radar de performance (saison)</div>
                    {radarAxes.length ? <Radar axes={radarAxes} /> : <div className={styles.pempty}>Profil non disponible.</div>}
                  </div>
                </div>
                <div className={styles.gcards} style={{ marginTop: 13 }}>
                  <div className={styles.gcard}>
                    <div className={styles.gt}>Buts vs xG par mois</div>
                    <div className={styles.gsub}>rendement offensif</div>
                    <BarsGX data={bars} />
                    <div className={styles.lgnd}>
                      <span><i style={{ background: 'var(--a)' }} />Buts</span>
                      <span><i style={{ background: 'var(--a2)' }} />xG</span>
                    </div>
                  </div>
                  <div className={styles.gcard}>
                    <div className={styles.gt}>Forme récente</div>
                    <div className={styles.gsub}>note moyenne · 5 derniers matchs notés</div>
                    {formeAvg != null ? (
                      <>
                        <div style={{ fontSize: 42, fontWeight: 850, color: ratingColor(formeAvg), marginTop: 6 }}>{formeAvg.toFixed(1)}</div>
                        <div style={{ color: 'var(--t2)', fontSize: 12.5, marginTop: 4 }}>
                          {formeAvg >= 7.3 ? 'En forme ▲' : 'Irrégulier ▬'}
                        </div>
                      </>
                    ) : <div className={styles.pempty}>n/d</div>}
                  </div>
                </div>

                <div className={styles.sect}>Historique match par match</div>
                {sortedDesc.length === 0 ? (
                  <p style={{ color: 'var(--t2)', fontSize: 13 }}>Aucun match récent disponible.</p>
                ) : (
                  <div className={styles.logwrap}>
                    <table className={styles.mlog}>
                      <thead>
                        <tr>
                          <th>Date</th><th>Adversaire</th><th>Min</th><th>B</th><th>PD</th><th>Tirs</th>
                          <th>xG</th><th>xA</th><th>Pass%</th><th>Duels</th><th>Note</th>
                        </tr>
                      </thead>
                      <tbody>
                        {sortedDesc.map((m) => {
                          const passPct = m.total_pass ? (m.accurate_pass ?? 0) / m.total_pass : null
                          return (
                            <tr key={m.event_api_id}>
                              <td>{m.event_date ? new Date(m.event_date).toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' }) : 'n/d'}</td>
                              <td><div className={styles.adv}>{m.is_home == null ? '' : m.is_home ? '🏠' : '✈️'} {m.opponent ?? 'n/d'}</div></td>
                              <td>{m.minutes_played != null ? `${m.minutes_played}'` : 'n/d'}</td>
                              <td>{m.goals ?? 0}</td>
                              <td>{m.goal_assist ?? 0}</td>
                              <td>{m.total_shots ?? 'n/d'}</td>
                              <td>{m.expected_goals != null ? m.expected_goals.toFixed(2) : '—'}</td>
                              <td>{m.expected_assists != null ? m.expected_assists.toFixed(2) : '—'}</td>
                              <td>{passPct != null ? `${(passPct * 100).toFixed(0)}%` : 'n/d'}</td>
                              <td>{m.duel_won ?? 'n/d'}</td>
                              <td>
                                {m.rating != null
                                  ? <span className={styles.rt} style={{ background: ratingBg(m.rating), color: ratingColor(m.rating) }}>{m.rating.toFixed(1)}</span>
                                  : 'n/d'}
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
                </>
              )}

              {tab === 'career' && (
                <>
                <div className={styles.sect}>Carrière</div>
                {careerIsError ? (
                  <p style={{ color: 'var(--t2)', fontSize: 13 }}>Erreur de chargement de l’historique de carrière.</p>
                ) : careerLoading ? (
                  <p style={{ color: 'var(--t2)', fontSize: 13 }}>Chargement…</p>
                ) : !career || career.career.length === 0 ? (
                  <p style={{ color: 'var(--t2)', fontSize: 13 }}>Historique de carrière non disponible pour ce joueur.</p>
                ) : (
                  <table className={styles.pcar}>
                    <thead>
                      <tr><th>Saison</th><th>Compétitions</th><th>M</th><th>Min</th><th>B</th><th>PD</th></tr>
                    </thead>
                    <tbody>
                      {career.career.map((s: CareerSeasonOut) => (
                        <tr key={s.season}>
                          <td>{s.season}</td>
                          <td>{s.competitions.map((c) => c.competition).filter(Boolean).join(', ') || '—'}</td>
                          <td>{s.appearances}</td>
                          <td>{s.minutes.toLocaleString('fr-FR')}</td>
                          <td>{s.goals}</td>
                          <td>{s.assists}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                </>
              )}

              {tab === 'selection' && player.selection_stats && (() => {
                const selCats = buildCategories(player.selection_stats)
                const s = player.selection_stats
                return (
                  <>
                    <div className={styles.sect}>Sélection · {player.national_team_name ?? 'Équipe nationale'}</div>
                    <div className={styles.ptiles}>
                      <Tile value={s.goals} label="Buts" />
                      <Tile value={s.goal_assist} label="Passes d." />
                      <Tile value={s.matches_played} label="Sélections" />
                      <Tile value={s.minutes_played} label="Minutes" thousands />
                      <Tile value={s.avg_rating} label="Note" decimals={1} />
                    </div>
                    <div className={styles.pgrid}>
                      {selCats.map((cat) => (
                        <div className={styles.ppanel} key={cat.key}>
                          <div className={styles.ph}>
                            <div className={styles.ico} style={{ background: `${cat.color}22` }}>{cat.icon}</div>
                            <h3>{cat.title}</h3>
                          </div>
                          <div className={styles.pb}>{cat.rows.map((row) => <Metric key={row.label} row={row} />)}</div>
                        </div>
                      ))}
                    </div>
                  </>
                )
              })()}
              </div>
            </>
          )
        )}
      </div>
    </>
  )
}
