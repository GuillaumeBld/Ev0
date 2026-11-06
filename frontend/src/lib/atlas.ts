/**
 * Data layer for the Atlas page (`/dashboard/atlas`) — map-first player
 * explorer. Follows the same conventions as the rest of the app: plain
 * `fetch` against relative `/api/v1/...` URLs (proxied to the backend by
 * `next.config.js` rewrites — see `frontend/src/app/dashboard/teams/page.tsx`
 * and `frontend/src/app/dashboard/players/page.tsx`), typed response
 * shapes, no fixed `limit=500` — every list call takes real limit/offset.
 */

import type { PlayerSummary, PlayerDetail, PlayerCareerOut, SeasonStatsOut, RecentMatch, CareerSeasonOut } from './api'

export type { PlayerSummary, PlayerDetail, PlayerCareerOut, SeasonStatsOut, RecentMatch, CareerSeasonOut }

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

export interface GeoResponse {
  countries: GeoCountry[]
  international: GeoIntl[]
}

export async function fetchGeo(): Promise<GeoResponse> {
  const res = await fetch('/api/v1/atlas/geo')
  if (!res.ok) throw new Error(`fetchGeo: HTTP ${res.status}`)
  return res.json()
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
