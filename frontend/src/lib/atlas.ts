/**
 * Data layer for the Atlas page (`/dashboard/atlas`) — map-first player
 * explorer. Follows the same conventions as the rest of the app: plain
 * `fetch` against relative `/api/v1/...` URLs (proxied to the backend by
 * `next.config.js` rewrites — see `frontend/src/app/dashboard/teams/page.tsx`
 * and `frontend/src/app/dashboard/players/page.tsx`), typed response
 * shapes, no fixed `limit=500` — every list call takes real limit/offset.
 */

import type {
  PlayerSummary,
  PlayerDetail,
  PlayerCareerOut,
  SeasonStatsOut,
  RecentMatch as ApiRecentMatch,
  CareerSeasonOut,
} from './api'

export type { PlayerSummary, PlayerDetail, PlayerCareerOut, SeasonStatsOut, CareerSeasonOut }

/**
 * `RecentMatch` étendu avec `league_api_id` — déjà renvoyé par le backend
 * (voir `RecentMatch.league_api_id` dans `backend/app/api/players.py`, livré
 * par la Task 2 du plan de refonte) mais pas encore typé dans `api.ts`. On
 * l'étend ici plutôt que d'y toucher : `api.ts` est hors scope de cette tâche
 * (commits limités à atlas.ts / PlayerDetail.tsx / atlas.module.css).
 */
export interface RecentMatch extends ApiRecentMatch {
  league_api_id: number | null
}

// ── Atlas geo tree (GET /atlas/geo) ─────────────────────────────────────────

export interface GeoLeague {
  league_api_id: number
  name: string
  tier: number | string
  kind: string
  /** 'full' = couverture complète des stats avancées, 'part' = partielle. */
  coverage: 'full' | 'part'
  club_count: number
}

export interface GeoCountry {
  /** ISO2 — matche les `id="c-XX"` du SVG monde et les clés de BBOX. */
  code: string
  name: string
  /** EUR | SAM | NAM | ASI | AFR | OCE */
  continent: string
  leagues: GeoLeague[]
}

export interface GeoIntl {
  league_api_id: number
  name: string
  org: string
  kind: string
}

/** Ensemble "mode Clubs" de la carte Atlas (switch Clubs/Sélections) :
 *  `countries` (championnats suivis) PLUS les pays ayant au moins un club
 *  en phase de ligue d'une coupe d'Europe / participant Libertadores, même
 *  sans championnat domestique suivi (`leagues` alors vide). Voir
 *  `GeoOut.countries_clubs` dans `backend/app/api/atlas.py`. */
export interface ClubCountry extends GeoCountry {
  has_cup_clubs: boolean
}

/** Ensemble "mode Sélections" de la carte Atlas : les nations ayant une
 *  équipe nationale avec des stats de sélection (les 48 du Mondial — voir
 *  `GeoOut.countries_selections`). `national_team_api_id` alimente
 *  `fetchSelectionPlayers`. */
export interface SelectionCountry {
  /** ISO2 — matche les `id="c-XX"` du SVG monde et les clés de BBOX. */
  code: string
  name: string
  /** EUR | SAM | NAM | ASI | AFR | OCE */
  continent: string
  national_team_api_id: number
}

export interface GeoResponse {
  countries: GeoCountry[]
  international: GeoIntl[]
  countries_clubs: ClubCountry[]
  countries_selections: SelectionCountry[]
}

export async function fetchGeo(): Promise<GeoResponse> {
  const res = await fetch('/api/v1/atlas/geo')
  if (!res.ok) throw new Error(`fetchGeo: HTTP ${res.status}`)
  return res.json()
}

// ── WorldMap — forme générique d'un pays allumé, indépendante du mode ──────
// `WorldMap` ne connaît pas la différence Clubs/Sélections : la page lui
// passe l'ensemble du mode courant déjà normalisé dans cette forme (code
// correspond toujours à un `id="c-XX"` du SVG, `detail` est le texte
// secondaire de l'infobulle — ex. "2 championnats" ou "Sélection nationale").

export interface MapCountry {
  code: string
  name: string
  continent: string
  detail: string
}

// ── Canonical teams (GET /canonical-teams) ──────────────────────────────────

export interface CanonicalTeam {
  id: number
  name_fr: string
  name_en: string | null
  api_football_id: number | null
  bzz_team_id: number | null
  sofascore_team_id: number | null
  aliases: string[]
  /** Engagement courant. Null si le club n'a pas (encore) d'engagement renseigné. */
  league_api_id: number | null
  country: string | null
  /** Écusson footylogos.com déjà vérifié 200 côté backend. Null = non résolu,
   *  jamais une URL devinée — le front retombe sur api_football_id puis un
   *  placeholder (voir `ClubCrest` dans `components/atlas`). */
  crest_url: string | null
}

/** L'endpoint backend n'a pas de filtre serveur par ligue : il renvoie tous
 *  les clubs. On le met en cache (react-query, clé `['atlas-canonical-teams']`)
 *  côté appelant et on filtre côté client — un seul aller-retour réseau pour
 *  toute la session plutôt qu'un par championnat ouvert. */
export async function fetchCanonicalTeams(): Promise<CanonicalTeam[]> {
  const res = await fetch('/api/v1/canonical-teams')
  if (!res.ok) throw new Error(`fetchCanonicalTeams: HTTP ${res.status}`)
  return res.json()
}

/** Clubs d'une ligue donnée — convenience wrapper autour de
 *  `fetchCanonicalTeams`, pour les appelants qui n'ont pas besoin du cache
 *  react-query (ex. appel one-off). Préférer `fetchCanonicalTeams` + `useMemo`
 *  dans un composant pour éviter de re-fetcher toute la table à chaque clic. */
export async function fetchClubs(leagueId: number): Promise<CanonicalTeam[]> {
  const all = await fetchCanonicalTeams()
  return all.filter((t) => t.league_api_id === leagueId)
}

// ── Players (GET /players) ──────────────────────────────────────────────────

export type PlayerSortField =
  | 'name' | 'goals' | 'goal_assist' | 'xg_per_90' | 'xa_per_90'
  | 'avg_rating' | 'shots_on_target_per_90' | 'minutes_played' | 'matches_played'

export interface FetchPlayersParams {
  league_api_id?: number | null
  team_api_id?: number | null
  position?: 'G' | 'D' | 'M' | 'F' | ''
  /** Recherche serveur par nom (sous-chaîne, insensible à la casse). */
  q?: string
  season?: string | null
  sort_by?: PlayerSortField | string
  sort_order?: 'asc' | 'desc'
  limit?: number
  offset?: number
}

export async function fetchPlayers(params: FetchPlayersParams = {}): Promise<PlayerSummary[]> {
  const qs = new URLSearchParams()
  if (params.league_api_id != null) qs.set('league_api_id', String(params.league_api_id))
  if (params.team_api_id != null) qs.set('team_api_id', String(params.team_api_id))
  if (params.position) qs.set('position', params.position)
  if (params.q) qs.set('q', params.q)
  if (params.season) qs.set('season', params.season)
  qs.set('sort_by', params.sort_by ?? 'avg_rating')
  qs.set('sort_order', params.sort_order ?? 'desc')
  qs.set('limit', String(params.limit ?? 24))
  qs.set('offset', String(params.offset ?? 0))

  const res = await fetch(`/api/v1/players?${qs}`)
  if (!res.ok) throw new Error(`fetchPlayers: HTTP ${res.status}`)
  return res.json()
}

// ── Selection players (GET /players/selection) ──────────────────────────────

export interface FetchSelectionParams {
  national_team_api_id?: number | null
  league_api_id?: number | null
  position?: 'G' | 'D' | 'M' | 'F' | ''
  /** Recherche serveur par nom (sous-chaîne, insensible à la casse). */
  q?: string
  season?: string | null
  sort_by?: PlayerSortField | string
  sort_order?: 'asc' | 'desc'
  limit?: number
  offset?: number
}

export async function fetchSelectionPlayers(params: FetchSelectionParams = {}): Promise<PlayerSummary[]> {
  const qs = new URLSearchParams()
  if (params.national_team_api_id != null) qs.set('national_team_api_id', String(params.national_team_api_id))
  if (params.league_api_id != null) qs.set('league_api_id', String(params.league_api_id))
  if (params.position) qs.set('position', params.position)
  if (params.q) qs.set('q', params.q)
  if (params.season) qs.set('season', params.season)
  qs.set('sort_by', params.sort_by ?? 'avg_rating')
  qs.set('sort_order', params.sort_order ?? 'desc')
  qs.set('limit', String(params.limit ?? 100))
  qs.set('offset', String(params.offset ?? 0))

  const res = await fetch(`/api/v1/players/selection?${qs}`)
  if (!res.ok) throw new Error(`fetchSelectionPlayers: HTTP ${res.status}`)
  return res.json()
}

export async function fetchPlayer(playerApiId: number): Promise<PlayerDetail> {
  const res = await fetch(`/api/v1/players/${playerApiId}`)
  if (!res.ok) throw new Error(`fetchPlayer: HTTP ${res.status}`)
  return res.json()
}

export async function fetchCareer(playerApiId: number): Promise<PlayerCareerOut> {
  const res = await fetch(`/api/v1/players/${playerApiId}/career`)
  if (!res.ok) throw new Error(`fetchCareer: HTTP ${res.status}`)
  return res.json()
}

// ── Player breakdown — stats par (saison × compétition) ────────────────────
// Consommé par les sélecteurs Saison/Compétition de la fiche joueur (voir
// `PlayerDetail.tsx`) : `bzz_player_match_stats` groupé par (saison, ligue),
// avec les champs absents de `SeasonStatsOut` (dispossessed, possession_lost,
// was_fouled, total_clearance, aerial_lost) nécessaires aux 8 catégories de
// la maquette `docs/superpowers/fiche-joueur-reference-mockup.html`.

/** Une ligne (saison, compétition) agrégée depuis `bzz_player_match_stats`.
 *  `kind` ∈ domestic_1 | domestic_2 | world | uefa_club | selection — sert à
 *  distinguer club de sélection côté front (la fiche n'a pas de notion de
 *  "kind" ailleurs, c'est l'unique endpoint qui l'expose). */
export interface PlayerBreakdownRow {
  season: string
  league_api_id: number
  league_name: string
  kind: 'domestic_1' | 'domestic_2' | 'world' | 'uefa_club' | 'selection' | string
  matches_played: number | null
  minutes_played: number | null
  starts: number | null
  goals: number | null
  goal_assist: number | null
  expected_goals: number | null
  expected_assists: number | null
  total_shots: number | null
  shots_on_target: number | null
  key_pass: number | null
  total_pass: number | null
  accurate_pass: number | null
  total_cross: number | null
  accurate_cross: number | null
  total_long_balls: number | null
  accurate_long_balls: number | null
  duel_won: number | null
  duel_lost: number | null
  aerial_won: number | null
  aerial_lost: number | null
  total_tackle: number | null
  won_tackle: number | null
  interception: number | null
  ball_recovery: number | null
  total_clearance: number | null
  dispossessed: number | null
  possession_lost: number | null
  was_fouled: number | null
  fouls: number | null
  yellow_card: number | null
  red_card: number | null
  avg_rating: number | null
}

export interface PlayerBreakdownOut {
  rows: PlayerBreakdownRow[]
}

export async function fetchPlayerBreakdown(playerApiId: number): Promise<PlayerBreakdownOut> {
  const res = await fetch(`/api/v1/players/${playerApiId}/breakdown`)
  if (!res.ok) throw new Error(`fetchPlayerBreakdown: HTTP ${res.status}`)
  return res.json()
}

// ── Player match odds — cotes closing Betclic buteur/passeur ───────────────
// Alimentera le journal match par match exhaustif (Task 5) ; typé ici pour
// que la data layer soit prête, conformément à `task-4-brief.md`.

export interface PlayerMatchOddsRow {
  event_api_id: number
  /** Cote de closing Betclic, marché buteur. `null` = non disponible (hors
   *  période de collecte ou pas de cote trouvée pour ce joueur/fixture). */
  buteur: number | null
  /** Cote de closing Betclic, marché passeur décisif. */
  passeur: number | null
}

export interface PlayerMatchOddsOut {
  odds: PlayerMatchOddsRow[]
}

export async function fetchPlayerMatchOdds(playerApiId: number): Promise<PlayerMatchOddsOut> {
  const res = await fetch(`/api/v1/players/${playerApiId}/match-odds`)
  if (!res.ok) throw new Error(`fetchPlayerMatchOdds: HTTP ${res.status}`)
  return res.json()
}
