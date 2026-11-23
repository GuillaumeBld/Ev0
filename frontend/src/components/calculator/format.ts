import type { FixturePlayerOdd, PlayerAllocationOut } from '@/lib/api'

/** Books affichés, dans l'ordre des colonnes de la maquette D. */
export const BOOKS = ['betclic', 'unibet', 'pmu'] as const
export type Book = (typeof BOOKS)[number]
export type Market = 'goal' | 'assist'

/** Cotes books d'un joueur : marché → book → cote. */
export type PlayerBookOdds = Record<Market, Partial<Record<Book, number>>>
export type BookOddsByPlayer = Map<number, PlayerBookOdds>

export function indexBookOdds(rows: FixturePlayerOdd[]): BookOddsByPlayer {
  const out: BookOddsByPlayer = new Map()
  for (const r of rows) {
    if (!(BOOKS as readonly string[]).includes(r.bookmaker)) continue
    const cur = out.get(r.player_api_id) ?? { goal: {}, assist: {} }
    cur[r.market][r.bookmaker as Book] = r.odds
    out.set(r.player_api_id, cur)
  }
  return out
}

/** Cote au format français, « — » quand elle n'existe pas. */
export function fmtOdds(o: number | null | undefined): string {
  return o == null ? '—' : o.toFixed(2).replace('.', ',')
}

export function fmtXg(x: number | null | undefined): string {
  return x == null ? '—' : x.toFixed(2).replace('.', ',')
}

/** Cotes Ev0 « avec sub ». Probabilité nulle = pas de cote calculée (la valeur 99 par défaut n'est pas une cote). */
export function ev0Odds(p: PlayerAllocationOut, market: Market): { alpha: number | null; beta: number | null } {
  if (market === 'goal') {
    return {
      alpha: (p.p_goal_supersub ?? 0) > 0 ? p.fair_odds_goal_supersub ?? null : null,
      beta: (p.beta_p_goal_supersub ?? 0) > 0 ? p.beta_fair_odds_goal_supersub ?? null : null,
    }
  }
  return {
    alpha: (p.p_assist_supersub ?? 0) > 0 ? p.fair_odds_assist_supersub ?? null : null,
    beta: (p.beta_p_assist_supersub ?? 0) > 0 ? p.beta_fair_odds_assist_supersub ?? null : null,
  }
}

/** Valeur : la cote book dépasse les DEUX cotes Ev0 de plus de 5 %. */
export function isValue(book: number | null | undefined, alpha: number | null, beta: number | null): boolean {
  if (book == null || alpha == null || beta == null) return false
  return book > Math.max(alpha, beta) * 1.05
}
