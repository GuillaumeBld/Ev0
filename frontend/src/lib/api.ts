import axios from 'axios'
import { getSession } from 'next-auth/react'

export const api = axios.create({
  baseURL: '',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Add auth token to requests
api.interceptors.request.use(async (config) => {
  const session = await getSession()
  if (session) {
    // Add session token if using JWT auth on backend
    // config.headers.Authorization = `Bearer ${session.accessToken}`
  }
  return config
})

// Handle 429 rate limit: retry once after delay
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const { config, response } = error
    if (response?.status === 429 && !config._retried) {
      config._retried = true
      const retryAfter = parseInt(response.headers['retry-after'] || '5', 10)
      await new Promise((r) => setTimeout(r, retryAfter * 1000))
      return api(config)
    }
    return Promise.reject(error)
  }
)

// Types
export interface Recommendation {
  id: string
  fixture_id: string
  fixture_name: string
  kickoff_utc: string
  player_name: string
  team: string
  market_type: 'goalscorer' | 'assist'
  fair_odds: number
  best_bookmaker: string
  best_odds: number
  edge: number
  classification: 'VALUE' | 'NO_VALUE' | 'AVOID'
  confidence: number
  explanation: Record<string, any>
  error?: string | null
}

export interface PriceRequest {
  player_id: string
  fixture_id: string
  xg_per_90?: number
  xa_per_90?: number
  expected_minutes?: number
  // ... other fields
}

export interface PriceResponse {
  player_id: string
  fixture_id: string
  market_type: string
  lambda_intensity: number
  probability: number
  fair_odds: number
  explanation: Record<string, any>
}

// API functions
export async function getRecommendations(params?: {
  date?: string
  market_type?: string
  min_edge?: number
}) {
  const queryParams: Record<string, string | number> = {}
  if (params?.date) queryParams.target_date = params.date
  if (params?.market_type) queryParams.market_type = params.market_type
  if (params?.min_edge != null) queryParams.min_edge = params.min_edge
  const { data } = await api.get('/api/v1/recommendations', { params: queryParams })
  return data
}

export async function getRecommendation(id: string) {
  const { data } = await api.get(`/api/v1/recommendations/${id}`)
  return data
}

export async function priceGoalscorer(request: PriceRequest): Promise<PriceResponse> {
  const { data } = await api.post('/api/v1/price/goalscorer', request)
  return data
}

export async function priceAssist(request: PriceRequest): Promise<PriceResponse> {
  const { data } = await api.post('/api/v1/price/assist', request)
  return data
}

export async function getHealth() {
  const { data } = await api.get('/health')
  return data
}

// ── Fixtures ────────────────────────────────────────────────────

export interface OddsSnapshotOut {
  id: number
  player_name: string
  market_type: string
  bookmaker: string
  odds: number
  implied_probability: number
  snapshot_utc: string
}

export interface FixtureOut {
  id: number
  external_id: string
  league: string
  season: string
  matchweek: number | null
  home_team: string
  away_team: string
  kickoff_utc: string
  status: string
  home_score: number | null
  away_score: number | null
  odds_count: number
  odds: OddsSnapshotOut[]
}

export interface FixturesResponse {
  count: number
  fixtures: FixtureOut[]
}

export interface FixtureCreateData {
  date: string
  time: string
  home_team: string
  away_team: string
  league: string
  season?: string
}

export async function getFixtures(params?: {
  league?: string
  status?: string
  from_date?: string
  to_date?: string
}): Promise<FixturesResponse> {
  const { data } = await api.get('/api/v1/fixtures', { params })
  return data
}

export async function createFixture(body: FixtureCreateData): Promise<FixtureOut> {
  const { data } = await api.post('/api/v1/fixtures', body)
  return data
}

export async function deleteFixture(id: number): Promise<void> {
  await api.delete(`/api/v1/fixtures/${id}`)
}

export async function getFixtureOdds(id: number): Promise<OddsSnapshotOut[]> {
  const { data } = await api.get(`/api/v1/fixtures/${id}/odds`)
  return data
}

export interface OddsCreateData {
  player_name: string
  market_type: string
  bookmaker: string
  odds: number
}

export async function createOdds(fixtureId: number, body: OddsCreateData): Promise<OddsSnapshotOut> {
  const { data } = await api.post(`/api/v1/fixtures/${fixtureId}/odds`, body)
  return data
}

// ── History & Stats ─────────────────────────────────────────────

export interface HistoryItem {
  id: number
  date: string
  fixture_name: string
  player_name: string
  market_type: string
  best_odds: number
  edge: number
  best_bookmaker: string
  status: string
  result: string | null
  pnl: number | null
  stake: number | null
}

export interface HistoryResponse {
  count: number
  bets: HistoryItem[]
}

export interface StatsResponse {
  total_bets: number
  wins: number
  losses: number
  pending: number
  total_pnl: number
  win_rate: number
  roi: number
}

export async function getHistory(params?: {
  status?: string
}): Promise<HistoryResponse> {
  const { data } = await api.get('/api/v1/history', { params })
  return data
}

export async function getStats(): Promise<StatsResponse> {
  const { data } = await api.get('/api/v1/stats')
  return data
}

export interface BreakdownItem {
  label: string
  bets: number
  wins: number
  losses: number
  pnl: number
  roi: number
}

export interface PnlPoint {
  date: string
  pnl: number
  cumulative: number
}

export interface StatsBreakdownResponse {
  by_market: BreakdownItem[]
  by_league: BreakdownItem[]
  pnl_trend: PnlPoint[]
}

export async function getStatsBreakdown(): Promise<StatsBreakdownResponse> {
  const { data } = await api.get('/api/v1/stats/breakdown')
  return data
}

// ── Backtest ────────────────────────────────────────────────────

export interface BacktestConfig {
  period: string
  min_edge: number
  stake_method: string
  markets: string
}

export interface BacktestResults {
  roi: number
  brierScore: number
  winRate: number
  wins: number
  losses: number
  totalBets: number
  pnlCurve: { date: string; cumPnl: number }[]
  calibration: { predicted: number; actual: number; count: number }[]
  edgeDistribution: { bucket: string; count: number; roi: number }[]
}

export async function runBacktest(config: BacktestConfig): Promise<BacktestResults> {
  const { data } = await api.post('/api/v1/backtest', config)
  return data
}

// ── Recommendation Actions ──────────────────────────────────────

export interface RecommendationUpdateBody {
  status?: 'approved' | 'rejected' | 'executed'
  result?: 'won' | 'lost' | 'void' | 'push'
  stake?: number
  operator_notes?: string
}

export interface RecommendationUpdateResponse {
  id: number
  status: string
  result: string | null
  pnl: number | null
  decided_utc: string | null
  settled_utc: string | null
}

export async function patchRecommendation(
  id: string | number,
  body: RecommendationUpdateBody,
): Promise<RecommendationUpdateResponse> {
  const { data } = await api.patch(`/api/v1/recommendations/${id}`, body)
  return data
}

// ── Bankroll ────────────────────────────────────────────────────

export interface BankrollTransaction {
  id: number
  entry_type: string
  amount: number
  balance_after: number
  recommendation_id: number | null
  stake: number | null
  notes: string | null
  transacted_utc: string
}

export interface BankrollResponse {
  balance: number
  total_deposited: number
  total_withdrawn: number
  total_staked: number
  total_won: number
  transactions: BankrollTransaction[]
}

export async function getBankroll(): Promise<BankrollResponse> {
  const { data } = await api.get('/api/v1/bankroll')
  return data
}

export async function depositBankroll(amount: number, notes?: string): Promise<BankrollTransaction> {
  const { data } = await api.post('/api/v1/bankroll/deposit', { amount, notes })
  return data
}

export async function withdrawBankroll(amount: number, notes?: string): Promise<BankrollTransaction> {
  const { data } = await api.post('/api/v1/bankroll/withdraw', { amount, notes })
  return data
}

// ── Settings ────────────────────────────────────────────────────

export async function getSettings(): Promise<Record<string, string>> {
  const { data } = await api.get('/api/v1/settings')
  return data
}

export async function saveSettings(settings: Record<string, string>): Promise<Record<string, string>> {
  const { data } = await api.put('/api/v1/settings', { settings })
  return data
}
