import type { FixturePlayerOdd, PlayerAllocationOut } from '@/lib/api'

/** Books affichés, dans l'ordre des colonnes de la maquette D. */
export const BOOKS = ['betclic', 'unibet', 'pmu'] as const
export type Book = (typeof BOOKS)[number]
export type Market = 'goal' | 'assist'

/** Cote d'un book. `sansSub` : le book ne propose pas le pari « ou son remplaçant » pour ce joueur. */
export interface BookOdd {
  odds: number
  sansSub: boolean
}

/** Cotes books d'un joueur : marché → book → cote. */
export type PlayerBookOdds = Record<Market, Partial<Record<Book, BookOdd>>>
export type BookOddsByPlayer = Map<number, PlayerBookOdds>

export function indexBookOdds(rows: FixturePlayerOdd[]): BookOddsByPlayer {
  const out: BookOddsByPlayer = new Map()
  for (const r of rows) {
    if (!(BOOKS as readonly string[]).includes(r.bookmaker)) continue
    const cur = out.get(r.player_api_id) ?? { goal: {}, assist: {} }
    cur[r.market][r.bookmaker as Book] = { odds: r.odds, sansSub: r.variant === 'sans_sub' }
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

/** Cotes Ev0 « avec sub » — les seules affichées. Probabilité nulle = pas de cote calculée (la valeur 99 par défaut n'est pas une cote). */
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

/** Cotes Ev0 « sans sub » : jamais affichées, elles servent seulement à juger une cote de book sans sub. */
export function ev0OddsSansSub(p: PlayerAllocationOut, market: Market): { alpha: number | null; beta: number | null } {
  if (market === 'goal') {
    return {
      alpha: (p.prob_goal ?? 0) > 0 ? p.fair_odds_goal ?? null : null,
      beta: (p.beta_prob_goal ?? 0) > 0 ? p.beta_fair_odds_goal ?? null : null,
    }
  }
  return {
    alpha: (p.prob_assist ?? 0) > 0 ? p.fair_odds_assist ?? null : null,
    beta: (p.beta_prob_assist ?? 0) > 0 ? p.beta_fair_odds_assist ?? null : null,
  }
}

/** Valeur : la cote book dépasse de plus de 5 % les cotes Ev0 existantes (Alpha seule si Beta manque). */
export function isValue(book: number | null | undefined, alpha: number | null, beta: number | null): boolean {
  const ev0 = [alpha, beta].filter((o): o is number => o != null)
  if (book == null || ev0.length === 0) return false
  return book > Math.max(...ev0) * 1.05
}
