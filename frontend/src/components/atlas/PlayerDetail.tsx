'use client'

import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { clsx } from 'clsx'
import { ArrowLeft } from 'lucide-react'
import styles from './atlas.module.css'
import { ClubCrest } from './ClubCrest'
import { ratingColor, ratingBg } from './PlayerCard'
import { MiniChart, BarsGX, Radar, type GxBar, type RadarAxis } from './charts'
import { fetchPlayer, fetchCareer, fetchPlayerBreakdown, fetchPlayerMatchOdds } from '@/lib/atlas'
import type { SeasonStatsOut, RecentMatch, CareerSeasonOut, PlayerBreakdownRow } from '@/lib/atlas'

/**
 * Fiche joueur plein écran ("grimoire") — portée depuis la maquette VALIDÉE
 * `docs/superpowers/fiche-joueur-reference-mockup.html` : **2 onglets**
 * Club / Sélection (plus de 3e onglet « Carrière », illisible — son contenu
 * est intégré en bas de l'onglet Club), avec des sélecteurs **Saison** +
 * **Compétition** qui recalculent en direct tuiles, 8 catégories colorées et
 * graphiques (fonctions `renderClub`/`renderSel`, `clubPanels`/`selPanels`,
 * `distRows`, `metric`, `p90`, `clubRadarAxes` de la maquette).
 *
 * Câblée à `GET /players/{id}` (season_stats/recent_matches/selection_stats),
 * `GET /players/{id}/career`, `GET /players/{id}/breakdown` (stats réelles
 * par saison × compétition) et `GET /players/{id}/match-odds` (closing
 * Betclic buteur/passeur par match).
 *
 * Task 5 (ce fichier, complet) : bannière `.banner` en tête de chaque onglet
 * (totaux toute-carrière, depuis la somme des lignes `/breakdown` du
 * périmètre), journal match par match exhaustif 21 colonnes + colonnes
 * `Cote B`/`Cote PD` rouges (`MatchLogTable`, filtré par onglet — club vs
 * sélection, et par saison/compétition sélectionnée côté club), et tables
 * "Parcours" (`.tbl`) en bas de chaque onglet : club = `/career` saison par
 * saison (historique complet, Transfermarkt — plus long que `/breakdown` qui
 * ne couvre que les saisons avec stats de match détaillées) ; sélection =
 * `/breakdown` (kind=selection) agrégé par compétition.
 */

// ── Lignes de métrique génériques (réutilisées club + sélection) ───────────

interface MetricRow {
  label: string
  value: number | null
  max?: number
  pct?: boolean
  decimals?: number
  /** Préfixe "+" quand positif — pour les deltas (Buts − xG, etc.), comme
   *  `o.sign` dans `fmt()` de la maquette. */
  sign?: boolean
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

/** Division sûre — jamais de NaN/Infinity ; `null` si le dénominateur est
 *  manquant ou nul. Reprend `sd()` de la maquette. */
function sd(a: number | null | undefined, b: number | null | undefined): number | null {
  if (a == null || b == null || b === 0) return null
  return a / b
}

function fmt(value: number | null, opts: { pct?: boolean; decimals?: number; sign?: boolean } = {}): string {
  if (value == null) return 'n/d'
  if (opts.pct) return `${(value * 100).toFixed(opts.decimals ?? 0)}%`
  const sign = opts.sign && value > 0 ? '+' : ''
  return `${sign}${value.toFixed(opts.decimals ?? 0)}`
}

/** Âge en années révolues depuis une date ISO de naissance. `null` si la date
 *  est absente ou illisible — jamais un 0/âge trompeur dans le hero. */
function computeAge(iso: string | null | undefined): number | null {
  if (!iso) return null
  const dob = new Date(iso)
  if (Number.isNaN(dob.getTime())) return null
  const now = new Date()
  let age = now.getFullYear() - dob.getFullYear()
  const m = now.getMonth() - dob.getMonth()
  if (m < 0 || (m === 0 && now.getDate() < dob.getDate())) age--
  return age >= 0 ? age : null
}

/** Valeur marchande en millions d'euros (« 212 M€ ») : 0 décimale au-delà de
 *  100 M€, sinon 1 décimale. `null` si absente/≤0 — l'élément est alors omis
 *  du hero, pas affiché « n/d ». */
function formatMarketValue(value: number | null | undefined): string | null {
  if (value == null || value <= 0) return null
  const millions = value / 1_000_000
  const rounded = millions >= 100 ? Math.round(millions) : Math.round(millions * 10) / 10
  return `${rounded.toLocaleString('fr-FR')} M€`
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

// ── Agrégat (saison, compétition) — alimente tuiles / 8 catégories / radar ──
// Mêmes noms de champs courts que la maquette (g/a/xg/xa/sh/sot/kp/tp/ap/…)
// pour pouvoir comparer les formules 1:1 avec `clubPanels`/`selPanels`/
// `distRows`/`clubRadarAxes` du fichier de référence.

interface AggRow {
  m: number | null; min: number | null
  g: number | null; a: number | null
  xg: number | null; xa: number | null
  sh: number | null; sot: number | null; kp: number | null
  tp: number | null; ap: number | null
  tc: number | null; ac: number | null
  tlb: number | null; alb: number | null
  dw: number | null; dl: number | null
  aw: number | null; al: number | null
  disp: number | null; plost: number | null
  cj: number | null; rj: number | null
  fc: number | null; wf: number | null
  note: number | null
}

const EMPTY_AGG: AggRow = {
  m: null, min: null, g: null, a: null, xg: null, xa: null, sh: null, sot: null, kp: null,
  tp: null, ap: null, tc: null, ac: null, tlb: null, alb: null, dw: null, dl: null, aw: null, al: null,
  disp: null, plost: null, cj: null, rj: null, fc: null, wf: null, note: null,
}

function rowToAgg(r: PlayerBreakdownRow): AggRow {
  return {
    m: r.matches_played, min: r.minutes_played,
    g: r.goals, a: r.goal_assist,
    xg: r.expected_goals, xa: r.expected_assists,
    sh: r.total_shots, sot: r.shots_on_target, kp: r.key_pass,
    tp: r.total_pass, ap: r.accurate_pass,
    tc: r.total_cross, ac: r.accurate_cross,
    tlb: r.total_long_balls, alb: r.accurate_long_balls,
    dw: r.duel_won, dl: r.duel_lost,
    aw: r.aerial_won, al: r.aerial_lost,
    disp: r.dispossessed, plost: r.possession_lost,
    cj: r.yellow_card, rj: r.red_card,
    fc: r.fouls, wf: r.was_fouled,
    note: r.avg_rating,
  }
}

/** Repli quand `/breakdown` ne renvoie aucune ligne pour ce joueur (ex.
 *  championnat sans stats de match avancées indexées) : on reconstruit un
 *  agrégat depuis `SeasonStatsOut`, qui n'a pas les 5 champs propres à
 *  `/breakdown` (total_clearance/dispossessed/possession_lost/was_fouled/
 *  fouls côté sélection) — ils restent `null` → "n/d", jamais une valeur
 *  inventée. */
function seasonStatsToAgg(s: SeasonStatsOut): AggRow {
  return {
    m: s.matches_played, min: s.minutes_played,
    g: s.goals, a: s.goal_assist,
    xg: s.expected_goals, xa: s.expected_assists,
    sh: s.total_shots, sot: s.shots_on_target, kp: s.key_pass,
    tp: s.total_pass, ap: s.accurate_pass,
    tc: s.total_cross, ac: s.accurate_cross,
    tlb: s.total_long_balls, alb: s.accurate_long_balls,
    dw: s.duel_won, dl: s.duel_lost,
    aw: s.aerial_won, al: s.aerial_lost,
    disp: null, plost: null,
    cj: s.yellow_card, rj: s.red_card,
    fc: null, wf: null,
    note: s.avg_rating,
  }
}

const AGG_SUM_KEYS: (keyof AggRow)[] = [
  'm', 'min', 'g', 'a', 'xg', 'xa', 'sh', 'sot', 'kp', 'tp', 'ap', 'tc', 'ac', 'tlb', 'alb',
  'dw', 'dl', 'aw', 'al', 'disp', 'plost', 'cj', 'rj', 'fc', 'wf',
]

/** "Toutes" = somme des lignes de la saison (ou de la compétition), comme
 *  `renderClub`/`renderSel` de la maquette. `note` est une moyenne pondérée
 *  par `matches_played` (reconstituer une moyenne exacte à partir de moyennes
 *  par ligue n'est possible qu'ainsi, faute d'avoir la somme brute des notes
 *  par match ici — le détail match par match, lui, utilise la vraie note de
 *  chaque match). */
function sumAgg(rows: AggRow[]): AggRow {
  if (rows.length === 0) return EMPTY_AGG
  const out: AggRow = { ...EMPTY_AGG }
  for (const key of AGG_SUM_KEYS) {
    const values = rows.map((r) => r[key]).filter((v): v is number => v != null)
    out[key] = values.length ? values.reduce((a, b) => a + b, 0) : null
  }
  const withNote = rows.filter((r) => r.note != null && r.m != null && r.m > 0)
  const weight = withNote.reduce((s, r) => s + (r.m as number), 0)
  out.note = weight > 0
    ? withNote.reduce((s, r) => s + (r.note as number) * (r.m as number), 0) / weight
    : null
  return out
}

function R(label: string, value: number | null | undefined, max?: number, opts: { pct?: boolean; decimals?: number; sign?: boolean } = {}): MetricRow {
  return { label, value: value ?? null, max, ...opts }
}

/** Distribution = passes / centres / longues passes, exactement `distRows()`
 *  de la maquette. */
function distRows(d: AggRow): MetricRow[] {
  return [
    R('Passes réussies', d.ap),
    R('Passes tentées', d.tp),
    R('Précision des passes', sd(d.ap, d.tp), 100, { pct: true }),
    R('Centres réussis', d.ac),
    R('Centres tentés', d.tc),
    R('Précision des centres', sd(d.ac, d.tc), 100, { pct: true }),
    R('Longues passes réussies', d.alb),
    R('Longues passes tentées', d.tlb),
    R('Précision longues passes', sd(d.alb, d.tlb), 100, { pct: true }),
  ]
}

/**
 * Les **8 catégories colorées** de la maquette (`clubPanels`/`selPanels`) :
 * Finition (rouge), Création (bleu), Distribution (ambre), Duels au sol
 * (teal), Jeu aérien (bleu clair), Pertes de balle (orange), Discipline (or,
 * + fautes subies), Temps de jeu (vert). Les échelles des barres (`max`)
 * diffèrent club/sélection, comme dans la maquette.
 */
function buildAggCategories(d: AggRow, scope: 'club' | 'selection'): Category[] {
  const isClub = scope === 'club'
  const goalsPer90 = per90(d.g, d.min)
  const assistsPer90 = per90(d.a, d.min)
  const kpPer90 = per90(d.kp, d.min)
  const dr = sd(d.dw, (d.dw ?? 0) + (d.dl ?? 0))
  const ar = sd(d.aw, (d.aw ?? 0) + (d.al ?? 0))
  const goalsMinusXg = d.g != null && d.xg != null ? d.g - d.xg : null
  const assistsMinusXa = d.a != null && d.xa != null ? d.a - d.xa : null

  return [
    {
      key: 'fin', title: 'Finition', icon: '⚽', color: 'var(--crit)',
      rows: [
        R('Buts', d.g, isClub ? 56 : 22),
        R('Buts / 90', goalsPer90, 1.4, { decimals: 2 }),
        R('xG', d.xg, isClub ? 46 : 8, { decimals: 1 }),
        R('Buts − xG', goalsMinusXg, undefined, { decimals: 1, sign: true }),
        R('xG par tir', sd(d.xg, d.sh), 0.4, { decimals: 2 }),
        R('Tirs', d.sh, isClub ? 250 : 45),
        R('Tirs cadrés', d.sot, isClub ? 130 : 25),
        R('Précision des tirs', sd(d.sot, d.sh), 100, { pct: true }),
      ],
    },
    {
      key: 'cre', title: 'Création', icon: '🎯', color: 'var(--a2)',
      rows: [
        R('Passes décisives', d.a, isClub ? 28 : 14),
        R('Passes déc. / 90', assistsPer90, 1, { decimals: 2 }),
        R('xA', d.xa, isClub ? 12 : 6, { decimals: 1 }),
        R('Passes déc. − xA', assistsMinusXa, undefined, { decimals: 1, sign: true }),
        R('Passes clés', d.kp, isClub ? 130 : 20),
        R('Passes clés / 90', kpPer90, 4, { decimals: 1 }),
      ],
    },
    { key: 'dis', title: 'Distribution', icon: '🔀', color: 'var(--a)', rows: distRows(d) },
    {
      key: 'duel', title: 'Duels au sol', icon: '💪', color: '#56a6a0',
      rows: [
        R('Duels gagnés', d.dw, isClub ? 300 : 40),
        R('Taux de réussite des duels', dr, 100, { pct: true }),
      ],
    },
    {
      key: 'air', title: 'Jeu aérien', icon: '🔼', color: '#7e9bd4',
      rows: [
        R('Duels aériens gagnés', d.aw, isClub ? 80 : 10),
        R('Duels aériens perdus', d.al, isClub ? 80 : 10),
        R('Taux de réussite aérien', ar, 100, { pct: true }),
      ],
    },
    {
      key: 'pert', title: 'Pertes de balle', icon: '🔻', color: '#cf8a5a',
      rows: [
        R('Dépossédé', d.disp, isClub ? 80 : 20),
        R('Ballons perdus', d.plost, isClub ? 600 : 130),
      ],
    },
    {
      key: 'dsc', title: 'Discipline', icon: '🟨', color: '#d7a93a',
      rows: [
        R('Cartons jaunes', d.cj, isClub ? 12 : 6),
        R('Cartons rouges', d.rj, 3),
        R('Fautes commises', d.fc, isClub ? 40 : 12),
        R('Fautes subies', d.wf, isClub ? 60 : 12),
      ],
    },
    {
      key: 'jeu', title: 'Temps de jeu', icon: '⏱️', color: 'var(--good)',
      rows: [
        R(isClub ? 'Matchs joués' : 'Sélections (matchs)', d.m, isClub ? 60 : 22),
        R('Minutes jouées', d.min, isClub ? 5000 : 2000),
        R('Note moyenne', d.note, 10, { decimals: 1 }),
      ],
    },
  ]
}

/** Radar 6 axes, exactement `clubRadarAxes`/`selRadarAxes` de la maquette :
 *  Finition = buts/90 (poids fort) + xG/90 (poids modéré) ; Création =
 *  passes déc./90 + passes clés/90 ; Passes = % de passes réussies ;
 *  Volume = tirs/90 ; Duels = % de duels gagnés ; Activité = minutes jouées
 *  rapportées aux minutes théoriques (matchs × 90). */
function buildAggRadar(d: AggRow): RadarAxis[] {
  const clamp = (v: number) => Math.max(0, Math.min(100, v))
  const goalsPer90 = per90(d.g, d.min) ?? 0
  const xgPer90 = per90(d.xg, d.min) ?? 0
  const assistsPer90 = per90(d.a, d.min) ?? 0
  const kpPer90 = per90(d.kp, d.min) ?? 0
  const shPer90 = per90(d.sh, d.min) ?? 0
  const passPct = sd(d.ap, d.tp) ?? 0
  const dr = sd(d.dw, (d.dw ?? 0) + (d.dl ?? 0)) ?? 0
  const activite = d.m && d.m > 0 ? ((d.min ?? 0) / (d.m * 90)) * 100 : 0
  return [
    { label: 'Finition', value: clamp(goalsPer90 * 55 + xgPer90 * 25) },
    { label: 'Création', value: clamp(assistsPer90 * 65 + kpPer90 * 7) },
    { label: 'Passes', value: clamp(passPct * 100) },
    { label: 'Volume', value: clamp(shPer90 * 11) },
    { label: 'Duels', value: clamp(dr * 100) },
    { label: 'Activité', value: clamp(activite) },
  ]
}

/** Champs "avancés" dont l'absence déclenche le badge "couverture partielle"
 *  dans le hero — en-dessous des Big 5, ces métriques (xG/xA, duels…)
 *  manquent souvent alors que les stats basiques (buts, minutes) existent. */
const ADVANCED_FIELDS: (keyof SeasonStatsOut)[] = [
  'expected_goals', 'expected_assists', 'key_pass_per_90',
  'tackles_per_90', 'interceptions_per_90', 'duel_win_rate', 'aerial_win_rate',
]

function hasPartialCoverage(s: SeasonStatsOut | null): boolean {
  if (!s) return true
  return ADVANCED_FIELDS.some((f) => s[f] == null)
}

/** Saison sportive (1er août → 31 juillet) d'une date ISO, au même format
 *  `"YYYY-YYYY+1"` que `BreakdownRow.season` / `SeasonStatsOut.season` (voir
 *  `season_service.py`) — sert à filtrer `recent_matches` par la saison
 *  sélectionnée dans l'onglet Club. */
function seasonOfDate(iso: string): string | null {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  const y = d.getFullYear()
  const m = d.getMonth() + 1
  const start = m >= 8 ? y : y - 1
  return `${start}-${start + 1}`
}

/**
 * Libellés de compétition par `league_api_id`, pour la colonne "Compét." du
 * journal — repli statique (mirroring `LEAGUE_REGISTRY` côté backend, voir
 * `backend/app/ingestion/bzzoiro/league_registry.py`) utilisé seulement si
 * `/breakdown` n'a pas de ligne pour cette ligue sur ce joueur (ex. repli
 * `season_stats` sans détail par compétition). Quand une ligne `/breakdown`
 * existe pour cette ligue, son `league_name` réel (table `leagues`) est
 * préféré — plus précis que ce mini-dictionnaire.
 */
const STATIC_LEAGUE_LABELS: Record<number, string> = {
  1: 'Premier League', 2: 'Liga Portugal', 3: 'La Liga', 4: 'Serie A', 5: 'Bundesliga', 6: 'Ligue 1',
  7: 'C1', 8: 'C2', 83: 'C3', 90: 'Supercoupe UEFA',
  32: 'Libertadores', 33: 'Sudamericana',
  27: 'Coupe du monde', 58: 'Qualifs CM', 59: 'Qualifs CM', 62: 'Qualifs CM', 63: 'Qualifs CM',
  64: 'Nations League', 65: 'Nations League',
  66: 'Euro 2024', 67: 'Copa América 2024',
}

function competitionLabel(leagueApiId: number | null, byId: Map<number, string>): string {
  if (leagueApiId == null) return '—'
  return byId.get(leagueApiId) ?? STATIC_LEAGUE_LABELS[leagueApiId] ?? String(leagueApiId)
}

/** Cote de closing Betclic (buteur ou passeur) pour une cellule du journal —
 *  `null`/absent ⇒ `—`, jamais une valeur inventée (les cotes ne sont
 *  collectées que depuis juillet 2026, voir `fetchPlayerMatchOdds`). */
function oddLabel(value: number | null | undefined): string {
  return value == null ? '—' : value.toFixed(2)
}

/** Une ligne `/breakdown` (ou son repli `SeasonStatsOut`) normalisée pour
 *  les sélecteurs Saison/Compétition. */
interface SelectorRow {
  season: string
  league_api_id: number
  league_name: string
  agg: AggRow
}

/**
 * Dérive, à partir de `recent_matches` :
 *  - `sortedDesc` : historique match par match, du plus récent au plus ancien.
 *  - `ratings` : notes en ordre chronologique croissant, pour la courbe.
 *  - `bars` : buts vs xG agrégés par mois calendaire (triés chronologiquement).
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

function formAverage(ratings: number[]): number | null {
  const last5 = ratings.slice(-5)
  return last5.length ? last5.reduce((a, b) => a + b, 0) / last5.length : null
}

type MatchOdds = { buteur: number | null; passeur: number | null }

/**
 * Journal match par match exhaustif — 21 colonnes + `Cote B`/`Cote PD`,
 * port 1:1 de `clubLogRows`/`selLogRows`/`oddCells` de la maquette
 * (`docs/superpowers/fiche-joueur-reference-mockup.html`). Partagé entre
 * l'onglet Club et l'onglet Sélection : seules les `matches` passées en prop
 * changent (déjà filtrées par onglet + sélecteurs par l'appelant).
 */
function MatchLogTable({
  matches,
  leagueNameById,
  oddsByEvent,
}: {
  matches: RecentMatch[]
  leagueNameById: Map<number, string>
  oddsByEvent: Map<number, MatchOdds>
}) {
  if (matches.length === 0) {
    return <p style={{ color: 'var(--t2)', fontSize: 13 }}>Aucun match disponible pour cette sélection.</p>
  }
  return (
    <div className={styles.logwrap}>
      <table className={styles.tbl}>
        <thead>
          <tr>
            <th>Date</th><th>Adversaire</th><th>Compét.</th><th>Min</th><th>Note</th><th>B</th><th>PD</th>
            <th>xG</th><th>xA</th><th>Tirs</th><th>Cadr</th><th>P.clés</th><th>%Pass</th><th>Ctr</th>
            <th>Duels</th><th>Tacl</th><th>Int</th><th>Récup</th><th>Touch</th><th>CJ</th><th>Fts</th>
            <th className={styles.odh}>Cote B</th><th className={styles.odh}>Cote PD</th>
          </tr>
        </thead>
        <tbody>
          {matches.map((m) => {
            const passPct = sd(m.accurate_pass, m.total_pass)
            const odds = oddsByEvent.get(m.event_api_id)
            return (
              <tr key={m.event_api_id}>
                <td>{m.event_date ? new Date(m.event_date).toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' }) : 'n/d'}</td>
                <td><div className={styles.adv}>{m.is_home == null ? '' : m.is_home ? '🏠' : '✈️'} {m.opponent ?? 'n/d'}</div></td>
                <td className={styles.cp}>{competitionLabel(m.league_api_id, leagueNameById)}</td>
                <td>{m.minutes_played != null ? `${m.minutes_played}'` : 'n/d'}</td>
                <td>
                  {m.rating != null
                    ? <span className={styles.rt} style={{ background: ratingBg(m.rating), color: ratingColor(m.rating) }}>{m.rating.toFixed(1)}</span>
                    : 'n/d'}
                </td>
                <td>{m.goals ?? 0}</td>
                <td>{m.goal_assist ?? 0}</td>
                <td>{m.expected_goals != null ? m.expected_goals.toFixed(2) : '—'}</td>
                <td>{m.expected_assists != null ? m.expected_assists.toFixed(2) : '—'}</td>
                <td>{m.total_shots ?? 'n/d'}</td>
                <td>{m.shots_on_target ?? 'n/d'}</td>
                <td>{m.key_pass ?? 'n/d'}</td>
                <td>{passPct != null ? `${Math.round(passPct * 100)}%` : 'n/d'}</td>
                <td>{m.total_cross ?? 'n/d'}</td>
                <td>{m.duel_won ?? 'n/d'}</td>
                <td>{m.total_tackle ?? 'n/d'}</td>
                <td>{m.interception ?? 'n/d'}</td>
                <td>{m.ball_recovery ?? 'n/d'}</td>
                <td>{m.touches ?? 'n/d'}</td>
                <td>{m.yellow_card ?? 'n/d'}</td>
                <td>{m.fouls ?? 'n/d'}</td>
                <td className={styles.odd}>{oddLabel(odds?.buteur)}</td>
                <td className={styles.odd}>{oddLabel(odds?.passeur)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
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

  const { data: breakdown } = useQuery({
    queryKey: ['atlas-player-breakdown', playerApiId],
    queryFn: () => fetchPlayerBreakdown(playerApiId as number),
    enabled: open,
  })

  const { data: matchOdds } = useQuery({
    queryKey: ['atlas-player-match-odds', playerApiId],
    queryFn: () => fetchPlayerMatchOdds(playerApiId as number),
    enabled: open,
  })

  const [tab, setTab] = useState<'club' | 'selection'>('club')
  const [clubSeason, setClubSeason] = useState<string | null>(null)
  const [clubComp, setClubComp] = useState<string>('all')
  const [selComp, setSelComp] = useState<string>('all')

  const hasSelection = !!player?.selection_stats
  // `PlayerDetail.recent_matches` est typé depuis `api.ts` (sans
  // `league_api_id`, voir le commentaire sur `RecentMatch` dans
  // `lib/atlas.ts`) alors que le backend renvoie bien ce champ depuis la
  // Task 2 du plan de refonte — cast documenté, pas une donnée inventée.
  const matches = useMemo(() => (player?.recent_matches ?? []) as RecentMatch[], [player?.recent_matches])

  // ── Normalisation /breakdown → lignes de sélecteur (avec repli season_stats) ──
  const clubRowsNorm = useMemo<SelectorRow[]>(() => {
    const rows = (breakdown?.rows ?? []).filter((r) => r.kind !== 'selection')
    if (rows.length > 0) {
      return rows.map((r) => ({ season: r.season, league_api_id: r.league_api_id, league_name: r.league_name, agg: rowToAgg(r) }))
    }
    if (player?.season_stats) {
      const s = player.season_stats
      return [{ season: s.season, league_api_id: s.league_api_id ?? -1, league_name: 'Toutes compétitions', agg: seasonStatsToAgg(s) }]
    }
    return []
  }, [breakdown, player?.season_stats])

  const selRowsNorm = useMemo<SelectorRow[]>(() => {
    const rows = (breakdown?.rows ?? []).filter((r) => r.kind === 'selection')
    if (rows.length > 0) {
      return rows.map((r) => ({ season: r.season, league_api_id: r.league_api_id, league_name: r.league_name, agg: rowToAgg(r) }))
    }
    if (player?.selection_stats) {
      const s = player.selection_stats
      return [{ season: s.season, league_api_id: s.league_api_id ?? -1, league_name: 'Toutes compétitions', agg: seasonStatsToAgg(s) }]
    }
    return []
  }, [breakdown, player?.selection_stats])

  // ── Journal : cotes Betclic par event + libellés de compétition ─────────
  const oddsByEvent = useMemo(() => {
    const map = new Map<number, MatchOdds>()
    for (const o of matchOdds?.odds ?? []) map.set(o.event_api_id, { buteur: o.buteur, passeur: o.passeur })
    return map
  }, [matchOdds])

  const leagueNameById = useMemo(() => {
    const map = new Map<number, string>()
    for (const r of breakdown?.rows ?? []) map.set(r.league_api_id, r.league_name)
    return map
  }, [breakdown])

  // ── Bannières : totaux toute-carrière (somme de TOUTES les lignes
  // breakdown du périmètre, indépendamment du sélecteur saison/compétition
  // affiché plus bas) ──────────────────────────────────────────────────────
  const selCareerAgg = useMemo(() => sumAgg(selRowsNorm.map((r) => r.agg)), [selRowsNorm])

  /** Parcours en sélection par compétition (table `.tbl` en bas de l'onglet
   *  Sélection) : les lignes `/breakdown` (kind=selection) sommées par
   *  `league_api_id` sur toutes les saisons, triées par nombre de sélections
   *  décroissant. */
  const selByCompetition = useMemo(() => {
    const byLeague = new Map<number, { name: string; rows: AggRow[] }>()
    for (const r of selRowsNorm) {
      const bucket = byLeague.get(r.league_api_id) ?? { name: r.league_name, rows: [] }
      bucket.rows.push(r.agg)
      byLeague.set(r.league_api_id, bucket)
    }
    return Array.from(byLeague.entries())
      .map(([league_api_id, v]) => ({ league_api_id, league_name: v.name, agg: sumAgg(v.rows) }))
      .sort((a, b) => (b.agg.m ?? 0) - (a.agg.m ?? 0))
  }, [selRowsNorm])

  // ── Onglet Club : sélecteurs Saison + Compétition ───────────────────────
  const seasons = useMemo(
    () => Array.from(new Set(clubRowsNorm.map((r) => r.season))).sort().reverse(),
    [clubRowsNorm],
  )
  const effectiveSeason = (clubSeason && seasons.includes(clubSeason) ? clubSeason : seasons[0]) ?? null

  const rowsForSeason = useMemo(
    () => clubRowsNorm.filter((r) => r.season === effectiveSeason),
    [clubRowsNorm, effectiveSeason],
  )
  const leaguesForSeason = useMemo(() => {
    const map = new Map<number, string>()
    for (const r of rowsForSeason) map.set(r.league_api_id, r.league_name)
    return Array.from(map.entries()).map(([league_api_id, league_name]) => ({ league_api_id, league_name }))
  }, [rowsForSeason])
  // Pas de sélecteur de compétition pour une unique ligne de repli synthétique
  // ("Toutes compétitions") : rien à choisir, un seul bouton "Toutes" ferait
  // doublon avec son propre libellé.
  const showClubCompSelector = leaguesForSeason.length > 0
    && !(leaguesForSeason.length === 1 && leaguesForSeason[0].league_name === 'Toutes compétitions')

  const effectiveClubComp = clubComp === 'all' || leaguesForSeason.some((l) => String(l.league_api_id) === clubComp)
    ? clubComp
    : 'all'

  const clubAgg = useMemo(() => {
    if (effectiveClubComp === 'all') return sumAgg(rowsForSeason.map((r) => r.agg))
    const found = rowsForSeason.find((r) => String(r.league_api_id) === effectiveClubComp)
    return found ? found.agg : sumAgg(rowsForSeason.map((r) => r.agg))
  }, [rowsForSeason, effectiveClubComp])

  const clubLeagueIdsForSeason = useMemo(() => new Set(leaguesForSeason.map((l) => l.league_api_id)), [leaguesForSeason])
  const clubMatches = useMemo(() => {
    if (!effectiveSeason) return []
    return matches.filter((m) => {
      if (!m.event_date || m.league_api_id == null) return false
      if (seasonOfDate(m.event_date) !== effectiveSeason) return false
      if (effectiveClubComp !== 'all') return String(m.league_api_id) === effectiveClubComp
      return clubLeagueIdsForSeason.has(m.league_api_id)
    })
  }, [matches, effectiveSeason, effectiveClubComp, clubLeagueIdsForSeason])

  // ── Onglet Sélection : sélecteur Compétition (pas de saison) ────────────
  const selLeagues = useMemo(() => {
    const map = new Map<number, string>()
    for (const r of selRowsNorm) map.set(r.league_api_id, r.league_name)
    return Array.from(map.entries()).map(([league_api_id, league_name]) => ({ league_api_id, league_name }))
  }, [selRowsNorm])
  const showSelCompSelector = selLeagues.length > 0
    && !(selLeagues.length === 1 && selLeagues[0].league_name === 'Toutes compétitions')

  const effectiveSelComp = selComp === 'all' || selLeagues.some((l) => String(l.league_api_id) === selComp)
    ? selComp
    : 'all'

  const selAgg = useMemo(() => {
    const rows = effectiveSelComp === 'all' ? selRowsNorm : selRowsNorm.filter((r) => String(r.league_api_id) === effectiveSelComp)
    return sumAgg(rows.map((r) => r.agg))
  }, [selRowsNorm, effectiveSelComp])

  const selLeagueIds = useMemo(() => new Set(selRowsNorm.map((r) => r.league_api_id)), [selRowsNorm])
  const selMatches = useMemo(() => {
    return matches.filter((m) => {
      if (m.league_api_id == null) return false
      if (effectiveSelComp !== 'all') return String(m.league_api_id) === effectiveSelComp
      return selLeagueIds.has(m.league_api_id)
    })
  }, [matches, effectiveSelComp, selLeagueIds])

  const clubSeries = useMatchSeries(clubMatches)
  const selSeries = useMatchSeries(selMatches)

  const clubCategories = useMemo(() => buildAggCategories(clubAgg, 'club'), [clubAgg])
  const clubRadar = useMemo(() => buildAggRadar(clubAgg), [clubAgg])
  const selCategories = useMemo(() => buildAggCategories(selAgg, 'selection'), [selAgg])
  const selRadar = useMemo(() => buildAggRadar(selAgg), [selAgg])

  const clubFormeAvg = formAverage(clubSeries.ratings)
  const selFormeAvg = formAverage(selSeries.ratings)

  /** Totaux "Total club" du bas de la table Parcours — somme de toutes les
   *  saisons `/career` (Transfermarkt), pas seulement celles couvertes par
   *  `/breakdown` (voir le commentaire de tête de fichier). */
  const careerTotals = useMemo(() => {
    const seasons = career?.career ?? []
    return seasons.reduce(
      (acc, s) => ({
        appearances: acc.appearances + s.appearances,
        minutes: acc.minutes + s.minutes,
        goals: acc.goals + s.goals,
        assists: acc.assists + s.assists,
      }),
      { appearances: 0, minutes: 0, goals: 0, assists: 0 },
    )
  }, [career])

  const partial = hasPartialCoverage(player?.season_stats ?? null)

  const clubCompLabel = effectiveClubComp === 'all'
    ? 'toutes compétitions'
    : (leaguesForSeason.find((l) => String(l.league_api_id) === effectiveClubComp)?.league_name ?? 'toutes compétitions')
  const selCompLabel = effectiveSelComp === 'all'
    ? 'toutes compétitions'
    : (selLeagues.find((l) => String(l.league_api_id) === effectiveSelComp)?.league_name ?? 'toutes compétitions')

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
                      {player.team_name && <span>{player.team_name}</span>}
                      {player.nationality && <span>· {player.nationality}</span>}
                      {computeAge(player.date_of_birth) != null && <span>· <b>{computeAge(player.date_of_birth)} ans</b></span>}
                      {player.height != null && <span>· {player.height} cm</span>}
                      {player.jersey_number != null && <span>· N°{player.jersey_number}</span>}
                      {formatMarketValue(player.market_value) && <span>· <b style={{ color: 'var(--a)' }}>{formatMarketValue(player.market_value)}</b></span>}
                      {partial && <span className={styles.napill}>couverture partielle</span>}
                    </div>
                    <div className={styles.ptiles} style={{ marginLeft: 0, marginTop: 10 }}>
                      <Tile value={careerTotals.appearances} label="Matchs carrière" />
                      <Tile value={careerTotals.goals} label="Buts carrière" />
                      <Tile value={careerTotals.assists} label="Passes déc." />
                    </div>
                  </div>
                </div>
                <div style={{ marginLeft: 'auto' }}>
                  <Ring note={player.season_stats?.avg_rating ?? null} size={82} />
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
              </div>

              <div className={styles.pdwrap}>
                {tab === 'club' && (
                  <>
                    <div className={styles.banner}>
                      <div className={styles.fg}>🏟️</div>
                      <div>
                        <h3>Carrière en club</h3>
                        <div className={styles.bs}>{player.team_name ?? 'Club actuel'} · toutes compétitions de club</div>
                      </div>
                      <div className={styles.ptiles}>
                        <Tile value={careerTotals.appearances} label="Matchs" />
                        <Tile value={careerTotals.goals} label="Buts" />
                        <Tile value={careerTotals.assists} label="Passes d." />
                        <Tile value={careerTotals.minutes} label="Minutes" thousands />
                      </div>
                    </div>

                    <div className={styles.sect}>
                      <span>Détail des stats</span>
                      {seasons.length > 0 && (
                        <>
                          <span className={styles.wlbl}>Saison</span>
                          <div className={styles.seg}>
                            {seasons.map((s) => (
                              <button
                                key={s}
                                className={clsx(s === effectiveSeason && styles.on)}
                                onClick={() => { setClubSeason(s); setClubComp('all') }}
                              >
                                {s}
                              </button>
                            ))}
                          </div>
                        </>
                      )}
                      {showClubCompSelector && (
                        <>
                          <span className={styles.wlbl}>Compétition</span>
                          <div className={styles.seg}>
                            <button className={clsx(effectiveClubComp === 'all' && styles.on)} onClick={() => setClubComp('all')}>Toutes</button>
                            {leaguesForSeason.map((l) => (
                              <button
                                key={l.league_api_id}
                                className={clsx(String(l.league_api_id) === effectiveClubComp && styles.on)}
                                onClick={() => setClubComp(String(l.league_api_id))}
                              >
                                {l.league_name}
                              </button>
                            ))}
                          </div>
                        </>
                      )}
                    </div>

                    {clubRowsNorm.length === 0 ? (
                      <p style={{ color: 'var(--t2)', fontSize: 13 }}>Données avancées non disponibles pour ce championnat.</p>
                    ) : (
                      <>
                        <div className={styles.selsum}>
                          <Tile value={clubAgg.g} label="Buts" />
                          <Tile value={clubAgg.a} label="Passes d." />
                          <Tile value={clubAgg.xg} label="xG" decimals={1} />
                          <Tile value={clubAgg.m} label="Matchs" />
                          <Tile value={clubAgg.min} label="Minutes" thousands />
                          <Tile value={clubAgg.note} label="Note" decimals={1} />
                        </div>

                        <div className={styles.pgrid}>
                          {clubCategories.map((cat) => (
                            <div className={styles.ppanel} key={cat.key}>
                              <div className={styles.ph}>
                                <div className={styles.ico} style={{ background: `color-mix(in srgb, ${cat.color} 18%, transparent)` }}>{cat.icon}</div>
                                <h3>{cat.title}</h3>
                              </div>
                              <div className={styles.pb}>
                                {cat.rows.map((row) => <Metric key={row.label} row={row} />)}
                              </div>
                            </div>
                          ))}
                        </div>

                        <div className={styles.sect}>
                          <span>Graphiques</span>
                          <span className={styles.wlbl}>{effectiveSeason} · {clubCompLabel}</span>
                        </div>
                        <div className={styles.gcards}>
                          <div className={styles.gcard}>
                            <div className={styles.gt}>Évolution de la note</div>
                            <div className={styles.gsub}>{clubSeries.ratings.length} matchs notés</div>
                            <MiniChart values={clubSeries.ratings} />
                          </div>
                          <div className={styles.gcard}>
                            <div className={styles.gt}>Profil du joueur</div>
                            <div className={styles.gsub}>radar de performance</div>
                            <Radar axes={clubRadar} />
                          </div>
                        </div>
                        <div className={styles.gcards} style={{ marginTop: 13 }}>
                          <div className={styles.gcard}>
                            <div className={styles.gt}>Buts vs xG par mois</div>
                            <div className={styles.gsub}>rendement offensif</div>
                            <BarsGX data={clubSeries.bars} />
                            <div className={styles.lgnd}>
                              <span><i style={{ background: 'var(--a)' }} />Buts</span>
                              <span><i style={{ background: 'var(--a2)' }} />xG</span>
                            </div>
                          </div>
                          <div className={styles.gcard}>
                            <div className={styles.gt}>Forme récente</div>
                            <div className={styles.gsub}>note moyenne · 5 derniers matchs</div>
                            {clubFormeAvg != null ? (
                              <>
                                <div style={{ fontSize: 42, fontWeight: 850, color: ratingColor(clubFormeAvg), marginTop: 6 }}>{clubFormeAvg.toFixed(1)}</div>
                                <div style={{ color: 'var(--t2)', fontSize: 12.5, marginTop: 4 }}>
                                  {clubFormeAvg >= 7.3 ? 'En forme ▲' : 'Irrégulier ▬'}
                                </div>
                              </>
                            ) : <div className={styles.pempty}>n/d</div>}
                          </div>
                        </div>
                      </>
                    )}

                    <div className={styles.sect}>
                      <span>Historique match par match</span>
                      <span className={styles.wlbl}>{clubSeries.sortedDesc.length} matchs</span>
                    </div>
                    <MatchLogTable matches={clubSeries.sortedDesc} leagueNameById={leagueNameById} oddsByEvent={oddsByEvent} />
                    <div className={styles.note}>
                      <b style={{ color: 'var(--crit)' }}>Cote B / Cote PD</b> = cote de closing Betclic (buteur / passeur) de ce match. <b>—</b> = non disponible (les cotes ne sont collectées que depuis juillet 2026).
                    </div>

                    <div className={styles.sect}>Parcours en club — saison par saison</div>
                    {careerIsError ? (
                      <p style={{ color: 'var(--t2)', fontSize: 13 }}>Erreur de chargement de l’historique de carrière.</p>
                    ) : careerLoading ? (
                      <p style={{ color: 'var(--t2)', fontSize: 13 }}>Chargement…</p>
                    ) : !career || career.career.length === 0 ? (
                      <p style={{ color: 'var(--t2)', fontSize: 13 }}>Historique de carrière non disponible pour ce joueur.</p>
                    ) : (
                      <div className={styles.logwrap}>
                        <table className={styles.tbl} style={{ minWidth: 720 }}>
                          <thead>
                            <tr><th>Saison</th><th>Compétitions</th><th>M</th><th>Min</th><th>Buts</th><th>PD</th></tr>
                          </thead>
                          <tbody>
                            {career.career.map((s: CareerSeasonOut) => (
                              <tr key={s.season}>
                                <td>{s.season}</td>
                                <td className={styles.cp}>{s.competitions.map((c) => c.competition).filter(Boolean).join(', ') || '—'}</td>
                                <td>{s.appearances}</td>
                                <td>{s.minutes.toLocaleString('fr-FR')}</td>
                                <td>{s.goals}</td>
                                <td>{s.assists}</td>
                              </tr>
                            ))}
                          </tbody>
                          <tfoot>
                            <tr>
                              <td>Total club</td>
                              <td></td>
                              <td>{careerTotals.appearances}</td>
                              <td>{careerTotals.minutes.toLocaleString('fr-FR')}</td>
                              <td>{careerTotals.goals}</td>
                              <td>{careerTotals.assists}</td>
                            </tr>
                          </tfoot>
                        </table>
                      </div>
                    )}
                  </>
                )}

                {tab === 'selection' && hasSelection && (
                  <>
                    <div className={styles.banner}>
                      <div className={styles.fg}>🌍</div>
                      <div>
                        <h3>Carrière en sélection — {player.national_team_name ?? 'Équipe nationale'}</h3>
                        <div className={styles.bs}>stats en sélection uniquement · compétitions internationales (Coupe du monde, Nations League, qualifs, amicaux)</div>
                        <span className={styles.napill} style={{ marginTop: 8 }}>couverture partielle</span>
                      </div>
                      <div className={styles.ptiles}>
                        <Tile value={selCareerAgg.m} label="Sélections" />
                        <Tile value={selCareerAgg.g} label="Buts" />
                        <Tile value={selCareerAgg.a} label="Passes d." />
                      </div>
                    </div>
                    <div className={styles.note}>
                      Couverture Bzzoiro : Coupe du monde 2026 + Nations League uniquement. Amicaux, qualifications et éditions précédentes à venir (complément Transfermarkt).
                    </div>

                    <div className={styles.sect}>
                      <span>Détail des stats — {player.national_team_name ?? 'Équipe nationale'}</span>
                      {showSelCompSelector && (
                        <>
                          <span className={styles.wlbl}>Compétition</span>
                          <div className={styles.seg}>
                            <button className={clsx(effectiveSelComp === 'all' && styles.on)} onClick={() => setSelComp('all')}>Toutes</button>
                            {selLeagues.map((l) => (
                              <button
                                key={l.league_api_id}
                                className={clsx(String(l.league_api_id) === effectiveSelComp && styles.on)}
                                onClick={() => setSelComp(String(l.league_api_id))}
                              >
                                {l.league_name}
                              </button>
                            ))}
                          </div>
                        </>
                      )}
                    </div>

                    <div className={styles.selsum}>
                      <Tile value={selAgg.g} label="Buts" />
                      <Tile value={selAgg.a} label="Passes d." />
                      <Tile value={selAgg.xg} label="xG" decimals={1} />
                      <Tile value={selAgg.m} label="Sélections" />
                      <Tile value={selAgg.min} label="Minutes" thousands />
                      <Tile value={selAgg.note} label="Note" decimals={1} />
                    </div>

                    <div className={styles.pgrid}>
                      {selCategories.map((cat) => (
                        <div className={styles.ppanel} key={cat.key}>
                          <div className={styles.ph}>
                            <div className={styles.ico} style={{ background: `color-mix(in srgb, ${cat.color} 18%, transparent)` }}>{cat.icon}</div>
                            <h3>{cat.title}</h3>
                          </div>
                          <div className={styles.pb}>{cat.rows.map((row) => <Metric key={row.label} row={row} />)}</div>
                        </div>
                      ))}
                    </div>

                    <div className={styles.sect}>
                      <span>Graphiques</span>
                      <span className={styles.wlbl}>{selCompLabel}</span>
                    </div>
                    <div className={styles.gcards}>
                      <div className={styles.gcard}>
                        <div className={styles.gt}>Évolution de la note</div>
                        <div className={styles.gsub}>{selSeries.ratings.length} matchs notés</div>
                        <MiniChart values={selSeries.ratings} />
                      </div>
                      <div className={styles.gcard}>
                        <div className={styles.gt}>Profil en sélection</div>
                        <div className={styles.gsub}>radar de performance</div>
                        <Radar axes={selRadar} />
                      </div>
                    </div>
                    <div className={styles.gcards} style={{ marginTop: 13 }}>
                      <div className={styles.gcard}>
                        <div className={styles.gt}>Buts vs xG par mois</div>
                        <div className={styles.gsub}>rendement offensif en sélection</div>
                        <BarsGX data={selSeries.bars} />
                        <div className={styles.lgnd}>
                          <span><i style={{ background: 'var(--a)' }} />Buts</span>
                          <span><i style={{ background: 'var(--a2)' }} />xG</span>
                        </div>
                      </div>
                      <div className={styles.gcard}>
                        <div className={styles.gt}>Forme récente</div>
                        <div className={styles.gsub}>note moyenne · 5 derniers matchs</div>
                        {selFormeAvg != null ? (
                          <>
                            <div style={{ fontSize: 42, fontWeight: 850, color: ratingColor(selFormeAvg), marginTop: 6 }}>{selFormeAvg.toFixed(1)}</div>
                            <div style={{ color: 'var(--t2)', fontSize: 12.5, marginTop: 4 }}>
                              {selFormeAvg >= 7.3 ? 'En forme ▲' : 'Irrégulier ▬'}
                            </div>
                          </>
                        ) : <div className={styles.pempty}>n/d</div>}
                      </div>
                    </div>

                    <div className={styles.sect}>
                      <span>Historique match par match</span>
                      <span className={styles.wlbl}>{selSeries.sortedDesc.length} matchs</span>
                    </div>
                    <MatchLogTable matches={selSeries.sortedDesc} leagueNameById={leagueNameById} oddsByEvent={oddsByEvent} />
                    <div className={styles.note}>
                      <b style={{ color: 'var(--crit)' }}>Cote B / Cote PD</b> = cote de closing Betclic (buteur / passeur). Les sélections sont rarement cotées (hors grande compétition) → souvent <b>—</b>.
                    </div>

                    <div className={styles.sect}>Parcours en sélection — par compétition</div>
                    {selByCompetition.length === 0 ? (
                      <p style={{ color: 'var(--t2)', fontSize: 13 }}>Détail par compétition non disponible pour ce joueur.</p>
                    ) : (
                      <div className={styles.logwrap}>
                        <table className={styles.tbl} style={{ minWidth: 640 }}>
                          <thead>
                            <tr><th>Compétition</th><th>M</th><th>Min</th><th>Buts</th><th>PD</th></tr>
                          </thead>
                          <tbody>
                            {selByCompetition.map((c) => (
                              <tr key={c.league_api_id}>
                                <td className={styles.cp}>{c.league_name}</td>
                                <td>{c.agg.m != null ? c.agg.m : 'n/d'}</td>
                                <td>{c.agg.min != null ? c.agg.min.toLocaleString('fr-FR') : 'n/d'}</td>
                                <td>{c.agg.g != null ? c.agg.g : 'n/d'}</td>
                                <td>{c.agg.a != null ? c.agg.a : 'n/d'}</td>
                              </tr>
                            ))}
                          </tbody>
                          <tfoot>
                            <tr>
                              <td>Total sélection</td>
                              <td>{selCareerAgg.m != null ? selCareerAgg.m : 'n/d'}</td>
                              <td>{selCareerAgg.min != null ? selCareerAgg.min.toLocaleString('fr-FR') : 'n/d'}</td>
                              <td>{selCareerAgg.g != null ? selCareerAgg.g : 'n/d'}</td>
                              <td>{selCareerAgg.a != null ? selCareerAgg.a : 'n/d'}</td>
                            </tr>
                          </tfoot>
                        </table>
                      </div>
                    )}
                  </>
                )}
              </div>
            </>
          )
        )}
      </div>
    </>
  )
}
