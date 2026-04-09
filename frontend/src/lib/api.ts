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
  status?: string
  error?: string | null
  xg_source?: string | null
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

// ── Match pricing (Top-Down) ────────────────────────────────────

export interface MatchPriceRequest {
  fixture_id: number
  home_xg_override?: number | null
  away_xg_override?: number | null
  home_pen_taker_override?: number | null
  away_pen_taker_override?: number | null
  home_starters?: string[] | null
  away_starters?: string[] | null
}

export interface PlayerAllocationOut {
  player_id: number
  player_name: string
  team: string
  position: string | null
  expected_minutes: number
  is_pen_taker: boolean
  npxg_share: number
  xa_share: number
  lambda_open_play: number
  lambda_penalty: number
  lambda_total: number
  prob_goal: number
  fair_odds_goal: number
  lambda_assist: number
  prob_assist: number
  fair_odds_assist: number
}

export interface MatchPriceResponse {
  fixture_id: number
  home_team: string
  away_team: string
  home_match_xg: number
  away_match_xg: number
  xg_source?: string | null
  home_players: PlayerAllocationOut[]
  away_players: PlayerAllocationOut[]
  home_lineup_players: PlayerAllocationOut[] | null
  away_lineup_players: PlayerAllocationOut[] | null
}

export async function priceMatch(request: MatchPriceRequest): Promise<MatchPriceResponse> {
  const { data } = await api.post('/api/v1/price/match', request)
  return data
}

// ── Recommendations ─────────────────────────────────────────────

export interface RecommendationsApiResponse {
  date: string | null
  count: number
  recommendations: Recommendation[]
  error: string | null
  total: number
  page: number
  page_size: number
  pages: number
}

export async function getRecommendations(params?: {
  date?: string
  market_type?: string
  min_edge?: number
  page?: number
  page_size?: number
}): Promise<RecommendationsApiResponse> {
  const queryParams: Record<string, string | number> = {}
  if (params?.date) queryParams.target_date = params.date
  if (params?.market_type) queryParams.market_type = params.market_type
  if (params?.min_edge != null) queryParams.min_edge = params.min_edge
  if (params?.page != null) queryParams.page = params.page
  if (params?.page_size != null) queryParams.page_size = params.page_size
  const { data } = await api.get('/api/v1/recommendations', { params: queryParams })
  return data
}

export async function getExpiredRecommendations(params?: {
  date?: string
  page?: number
  page_size?: number
}): Promise<RecommendationsApiResponse> {
  const queryParams: Record<string, string | number> = {}
  if (params?.date) queryParams.target_date = params.date
  if (params?.page != null) queryParams.page = params.page
  if (params?.page_size != null) queryParams.page_size = params.page_size
  const { data } = await api.get('/api/v1/recommendations/expired', { params: queryParams })
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
  limit?: number
  upcoming_only?: boolean
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

export async function getAutoflatHistory(): Promise<HistoryResponse> {
  const { data } = await api.get('/api/v1/history/autoflat')
  return data
}

export async function triggerAutoSettle(): Promise<{ settled: number }> {
  const { data } = await api.post('/api/v1/history/settle')
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
  // Convert period-based config to simulate endpoint params (date ranges)
  const now = new Date()
  let min_date: string | undefined
  let max_date: string | undefined

  if (config.period === '12m') {
    const start = new Date(now)
    start.setFullYear(start.getFullYear() - 1)
    min_date = start.toISOString().slice(0, 10)
    max_date = now.toISOString().slice(0, 10)
  } else if (config.period === 'season_2025_26') {
    min_date = '2025-08-01'
    max_date = '2026-06-30'
  } else if (config.period === 'season_2024_25') {
    min_date = '2024-08-01'
    max_date = '2025-06-30'
  } else {
    // default 6m
    const start = new Date(now)
    start.setMonth(start.getMonth() - 6)
    min_date = start.toISOString().slice(0, 10)
    max_date = now.toISOString().slice(0, 10)
  }

  const { data } = await api.post('/api/v1/backtest/simulate', {
    min_date,
    max_date,
    min_edge: config.min_edge,
    stake_method: config.stake_method,
  })
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

// ── xG source config ────────────────────────────────────────────

export async function getXgSource(): Promise<{ mode: 'bzzoiro' | 'model' }> {
  const { data } = await api.get('/api/config/xg-source')
  return data
}

export async function setXgSource(mode: 'bzzoiro' | 'model'): Promise<{ mode: 'bzzoiro' | 'model' }> {
  const { data } = await api.patch('/api/config/xg-source', { mode })
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

// ── Autopilot ────────────────────────────────────────────────────

export interface AutopilotMetrics {
  total_bets: number
  wins: number
  losses: number
  win_rate: number
  roi: number
  sharpe: number
}

export interface AutopilotStatus {
  enabled: boolean
  trained: boolean
  trained_at: string | null
  training_records: number
  steps_trained: number
  mode: 'paper' | 'live'
  metrics: AutopilotMetrics
}

export interface TrainingResult {
  records_used: number
  cumulative_pnl: number
  sharpe: number
  vs_kelly_roi: number
  final_epsilon: number
  duration_s: number
  fine_tune: { decisions_used: number; td_error_mean: number } | null
}

export interface AutopilotDecisionOut {
  recommendation_id: number | null
  player_name: string
  fixture_name: string
  market_type: string
  best_odds: number
  edge: number
  confidence: number
  action: 'skip' | 'half_kelly' | 'kelly' | 'aggressive'
  kelly_fraction: number
  stake: number
  rationale: {
    q_values: Record<string, number>
    top_feature: string
    features: number[]
  }
}

export interface AutopilotPerformance {
  metrics: Record<string, number>
  pnl_curve: { date: string; pnl: number; cumulative: number }[]
  action_distribution: Record<string, number>
  feature_importance: Record<string, number>
  brier_score?: number | null
  calibration_buckets?: { bucket: string; predicted: number; actual: number; count: number }[]
  calibration?: { bucket: string; predicted: number; actual: number; count: number }[]
}

export async function getAutopilotStatus(): Promise<AutopilotStatus> {
  const { data } = await api.get('/api/v1/autopilot/status')
  return data
}

export async function toggleAutopilot(enabled: boolean, mode?: string): Promise<AutopilotStatus> {
  const { data } = await api.post('/api/v1/autopilot/toggle', { enabled, mode: mode ?? 'paper' })
  return data
}

export async function trainAutopilot(params?: {
  min_date?: string
  max_date?: string
  epochs?: number
}): Promise<TrainingResult> {
  const { data } = await api.post('/api/v1/autopilot/train', params ?? {})
  return data
}

export async function getAutopilotToday(): Promise<AutopilotDecisionOut[]> {
  const { data } = await api.get('/api/v1/autopilot/today')
  return data
}

export async function getAutopilotPerformance(): Promise<AutopilotPerformance> {
  const { data } = await api.get('/api/v1/autopilot/performance')
  return data
}

// ── Autopilot Optimization ──────────────────────────────────────

export interface OptimizationResult {
  best_params: Record<string, number | boolean> | null
  best_log_wealth: number
  best_roi: number
  sharpe: number
  dsr: number
  n_features: number
  n_trials: number
  n_folds: number
  records_used: number
  duration_s: number
  completed_at?: string | null
  error?: string | null
  status?: string
}

export interface PlayerSummary {
  player_api_id: number
  name: string
  short_name: string
  position: string
  team_name: string
  nationality: string
  xg_per_90: number | null
  xa_per_90: number | null
  avg_rating: number | null
  shots_on_target_per_90: number | null
  form_xg_5: number | null
  matches_played: number
  minutes_played: number
  season: string
}

export interface RecentMatch {
  event_api_id: number
  event_date: string
  opponent: string
  is_home: boolean
  minutes_played: number
  goals: number
  goal_assist: number
  expected_goals: number | null
  rating: number | null
  shots_on_target: number
  key_pass: number
}

export interface PlayerDetail {
  player_api_id: number
  name: string
  short_name: string
  position: string
  date_of_birth: string | null
  nationality: string
  height: number | null
  jersey_number: number | null
  market_value: number | null
  team_name: string
  season_stats: Record<string, number | null>
  recent_matches: RecentMatch[]
}

export async function getLastOptimization(): Promise<OptimizationResult | null> {
  try {
    const { data } = await api.get('/api/v1/autopilot/optimization')
    if (data.status === 'never_run') return null
    return data
  } catch {
    return null
  }
}

export async function optimizeAutopilot(n_trials?: number): Promise<OptimizationResult> {
  const { data } = await api.post('/api/v1/autopilot/optimize', {
    n_trials: n_trials ?? 100,
  }, {
    timeout: 600_000, // optimization can take up to 10 min on large datasets
  })
  return data
}
