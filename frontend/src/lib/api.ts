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
  bet_type?: 'goal' | 'assist'
  supersub_market_type?: 'standard' | 'supersub'
  fair_odds: number
  best_bookmaker: string
  best_odds: number
  edge: number
  classification: 'VALUE' | 'NO_VALUE' | 'AVOID'
  confidence: number
  explanation: Record<string, any>
  status?: string
  decided_utc?: string | null
  error?: string | null
  xg_source?: string | null
  is_pen_taker?: boolean
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
  home_corners_per_match?: number | null
  away_corners_per_match?: number | null
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
  // Supersub fields (may be absent for non-sub players)
  p_goal_supersub?: number
  fair_odds_goal_supersub?: number
  p_assist_supersub?: number
  fair_odds_assist_supersub?: number
  p_sub?: number
  avg_sub_time?: number
  sub_premium_goal?: number
  sub_premium_assist?: number
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
  last_scraped_at?: string | null
  p00?: number
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
  status?: string
  page?: number
  page_size?: number
}): Promise<RecommendationsApiResponse> {
  const queryParams: Record<string, string | number> = {}
  if (params?.date) queryParams.target_date = params.date
  if (params?.market_type) queryParams.market_type = params.market_type
  if (params?.min_edge != null) queryParams.min_edge = params.min_edge
  if (params?.status) queryParams.status_filter = params.status
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
  home_team_id: string | null
  away_team_id: string | null
  home_api_football_id: number | null
  away_api_football_id: number | null
  kickoff_utc: string
  status: string
  home_score: number | null
  away_score: number | null
  odds_count: number
  goalscorer_count: number
  assist_count: number
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
  fair_odds: number | null
  edge: number
  best_bookmaker: string
  status: string
  result: string | null
  pnl: number | null
  stake: number | null
  decided_utc: string | null
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

// ── Players (Bzzoiro) ────────────────────────────────────────────

export interface BzzLeague {
  api_id: number
  name: string
}

export interface BzzTeam {
  api_id: number
  name: string
}

export interface PlayerSummary {
  player_api_id: number
  name: string
  short_name: string | null
  position: string | null
  team_name: string | null
  nationality: string | null
  goals: number | null
  goal_assist: number | null
  xg_per_90: number | null
  xa_per_90: number | null
  avg_rating: number | null
  shots_on_target_per_90: number | null
  form_xg_5: number | null
  matches_played: number | null
  minutes_played: number | null
  season: string
}

export interface SeasonStatsOut {
  season: string
  league_api_id: number | null
  matches_played: number | null
  minutes_played: number | null
  starts: number | null
  goals: number | null
  goal_assist: number | null
  total_shots: number | null
  shots_on_target: number | null
  key_pass: number | null
  total_cross: number | null
  accurate_cross: number | null
  total_pass: number | null
  accurate_pass: number | null
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
  yellow_card: number | null
  red_card: number | null
  saves: number | null
  expected_goals: number | null
  expected_assists: number | null
  xg_per_90: number | null
  xa_per_90: number | null
  shots_per_90: number | null
  shots_on_target_per_90: number | null
  key_pass_per_90: number | null
  accurate_cross_per_90: number | null
  recoveries_per_90: number | null
  tackles_per_90: number | null
  interceptions_per_90: number | null
  shot_accuracy: number | null
  xg_per_shot: number | null
  finishing_delta: number | null
  xa_delta: number | null
  pass_completion: number | null
  long_ball_accuracy: number | null
  cross_accuracy: number | null
  duel_win_rate: number | null
  aerial_win_rate: number | null
  tackle_success_rate: number | null
  avg_rating: number | null
  avg_minutes_per_match: number | null
  starts_pct: number | null
  form_xg_5: number | null
  form_rating_5: number | null
  form_goals_5: number | null
  form_assists_5: number | null
  rating_trend: number | null
}

export interface RecentMatch {
  event_api_id: number
  event_date: string | null
  opponent: string | null
  is_home: boolean | null
  minutes_played: number | null
  rating: number | null
  touches: number | null
  goals: number | null
  goal_assist: number | null
  expected_goals: number | null
  expected_assists: number | null
  total_shots: number | null
  shots_on_target: number | null
  total_pass: number | null
  accurate_pass: number | null
  key_pass: number | null
  total_long_balls: number | null
  accurate_long_balls: number | null
  total_cross: number | null
  accurate_cross: number | null
  duel_won: number | null
  duel_lost: number | null
  aerial_won: number | null
  aerial_lost: number | null
  total_tackle: number | null
  won_tackle: number | null
  total_clearance: number | null
  interception: number | null
  ball_recovery: number | null
  yellow_card: number | null
  red_card: number | null
  fouls: number | null
  was_fouled: number | null
  dispossessed: number | null
  possession_lost: number | null
  saves: number | null
  goals_conceded: number | null
  shot_accuracy: number | null
  pass_completion: number | null
  duel_win_rate: number | null
  xg_per_shot: number | null
  finishing_delta: number | null
  xa_delta: number | null
  long_ball_accuracy: number | null
  cross_accuracy: number | null
  aerial_win_rate: number | null
  tackle_success_rate: number | null
}

export interface PlayerDetail {
  player_api_id: number
  name: string
  short_name: string | null
  position: string | null
  date_of_birth: string | null
  nationality: string | null
  height: number | null
  jersey_number: number | null
  market_value: number | null
  team_name: string | null
  season_stats: SeasonStatsOut | null
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

export interface PenTakerResponse {
  fixture_id: number
  home_pen_taker_id: number | null
  away_pen_taker_id: number | null
}

export async function getPenTakers(fixtureId: number): Promise<PenTakerResponse> {
  const { data } = await api.get(`/api/v1/pen-takers/${fixtureId}`)
  return data
}

export async function setPenTakers(
  fixtureId: number,
  homeId: number | null,
  awayId: number | null,
): Promise<PenTakerResponse> {
  const { data } = await api.post('/api/v1/pen-takers', {
    fixture_id: fixtureId,
    home_pen_taker_id: homeId,
    away_pen_taker_id: awayId,
  })
  return data
}

export interface WCNation {
  nation: string
  group_letter: string
  flag_emoji: string | null
  player_count: number
}

export interface WCPlayer {
  player_name: string
  nation: string | null
  group_letter: string | null
  club: string | null
  position: string
  shirt_number: number | null
  matches_played: number | null
  minutes_played: number | null
  goals: number | null
  assists: number | null
  xg: number | null
  xa: number | null
  xg_per90: number | null
  xa_per90: number | null
  avg_rating: number | null
  saves: number | null
  form_goals_5: number | null
  form_xg_5: number | null
  form_rating_5: number | null
  // ScoutingStats enrichment
  sc_rating: number | null
  key_passes_p90: number | null
  tackles_p90: number | null
  dribbles_p90: number | null
  dribble_success_rate: number | null
  shots_on_target_p90: number | null
  pass_accuracy: number | null
  player_image: string | null
  detailed_position: string | null
  sc_appearances: number | null
}

export interface WCPlayersPage {
  players: WCPlayer[]
  total: number
  page: number
  page_size: number
}

export interface WCSquad {
  nation: string
  group_letter: string
  flag_emoji: string | null
  gk: WCPlayer[]
  def_: WCPlayer[]
  mid: WCPlayer[]
  fwd: WCPlayer[]
}

// ── WC2026 Lineups ─────────────────────────────────────────────────────────

export interface WCLineupPlayer {
  player_name: string
  position: string        // GK / DEF / MID / FWD
  line_index: number      // 0=GK row, 1=DEF line, 2+=next lines
  slot_index: number      // left-to-right order in the line
  is_starter: boolean
  role: 'starter' | 'sub_planned' | 'sub_tactical' | 'reserve'
  expected_minutes: number
  shirt_number?: number | null
}

export interface WCLineup {
  nation: string
  context: string
  formation: string
  source: 'manual' | 'rotowire' | 'bzzoiro'
  players: WCLineupPlayer[]
}

export interface WCNationStatus {
  nation: string
  group_letter: string
  flag_emoji: string | null
  complete: boolean
  starters_count: number
}

export interface WCSquadPlayer {
  player_name: string
  position: string
  shirt_number: number | null
}

export interface WCNationLineups {
  nation: string
  flag_emoji: string | null
  squad: WCSquadPlayer[]
  lineups: Record<string, WCLineup>
}

export async function getWCLineupNations(): Promise<WCNationStatus[]> {
  const { data } = await api.get('/api/v1/wc2026/lineups')
  return data
}

export async function getWCNationLineups(nation: string): Promise<WCNationLineups> {
  const { data } = await api.get(`/api/v1/wc2026/lineups/${encodeURIComponent(nation)}`)
  return data
}

export async function upsertWCLineup(
  nation: string,
  context: string,
  body: { formation: string; players: WCLineupPlayer[] },
): Promise<WCLineup> {
  const { data } = await api.put(
    `/api/v1/wc2026/lineups/${encodeURIComponent(nation)}/${context}`,
    body,
  )
  return data
}

export async function syncRotowireLineups(): Promise<{
  seeded: number
  skipped_manual: number
  no_match: number
}> {
  const { data } = await api.post('/api/v1/wc2026/lineups/sync-rotowire')
  return data
}

export async function syncBzzoiroLineups(): Promise<{
  synced: number
  total_matches: number
}> {
  const { data } = await api.post('/api/v1/wc2026/lineups/sync-from-matches')
  return data
}

export async function fillSubsLineups(): Promise<{
  added_total: number
  by_nation: Record<string, number>
}> {
  const { data } = await api.post('/api/v1/wc2026/lineups/fill-subs')
  return data
}

// ── WC2026 Tournament Pricing ────────────────────────────────────────────────

export interface WCPlayerPricing {
  nation: string
  player_name: string
  position: string | null
  lambda_goals: number
  lambda_assists: number
  lambda_remaining_goals: number | null
  lambda_remaining_assists: number | null
  // WC actual tournament stats
  wc_goals: number | null
  wc_assists: number | null
  wc_minutes: number | null
  wc_xg_per_90: number | null
  prior_xg_p90: number | null
  blended_xg_p90: number | null
  // cuts — goals
  p_1g: number | null
  p_2g: number | null
  p_3g: number | null
  p_4g: number | null
  fair_1g: number | null
  fair_2g: number | null
  fair_3g: number | null
  fair_4g: number | null
  // cuts — assists
  p_1a: number | null
  p_2a: number | null
  p_3a: number | null
  fair_1a: number | null
  fair_2a: number | null
  fair_3a: number | null
  // outrights
  p_top_scorer: number | null
  p_top_assister: number | null
  fair_top_scorer: number | null
  fair_top_assister: number | null
  // bookmaker edge
  bk_top_scorer: number | null
  bk_top_scorer_unibet: number | null
  bk_top_scorer_betclic: number | null
  bk_top_scorer_pmu: number | null
  bk_top_assister: number | null
  bk_top_assister_unibet: number | null
  bk_top_assister_betclic: number | null
  bk_top_assister_pmu: number | null
  edge_top_scorer: number | null
  edge_top_assister: number | null
}

export interface WCComputeResult {
  players_computed: number
  nations_computed: number
  duration_s: number
}

export async function computeWCPricing(): Promise<WCComputeResult> {
  const { data } = await api.post('/api/v1/wc2026/pricing/compute')
  return data
}

export async function getWCPricingPlayers(params?: {
  nation?: string
  position?: string
  min_lambda?: number
}): Promise<WCPlayerPricing[]> {
  const { data } = await api.get('/api/v1/wc2026/pricing/players', { params })
  return data
}

// ── WC2026 Nation outright odds ──────────────────────────────────────────────

export interface BookmakerOddEntry {
  odds: number | null
  is_active: boolean
  last_seen_at: string | null
  odds_changed_at: string | null
  republished_at: string | null
}

export interface MarketOdds {
  unibet: BookmakerOddEntry
  pmu: BookmakerOddEntry
  betclic: BookmakerOddEntry
}

export interface WCNationOdds {
  nation: string
  group_letter: string | null
  flag_emoji: string | null
  winner: MarketOdds
  top4: MarketOdds
  top8: MarketOdds
  group_stage: MarketOdds
}

export interface SyncOddsResult {
  bookmaker: string
  scraped: number
  deactivated: number
  duration_s: number
  note: string | null
}

export async function getWCNationsOdds(): Promise<WCNationOdds[]> {
  const { data } = await api.get('/api/v1/wc2026/pricing/nations')
  return data
}

export async function syncWCOdds(bookmaker: string): Promise<SyncOddsResult> {
  const { data } = await api.post(
    `/api/v1/wc2026/pricing/sync-odds?bookmaker=${bookmaker}`,
    null,
    { timeout: 120_000 },
  )
  return data
}

// ── WC2026 Stats / Rankings ──────────────────────────────────────────────────

export interface WCPlayerRanking {
  player_name: string
  nation: string | null
  flag_emoji: string | null
  position: string | null   // GK | DEF | MID | FWD
  matches: number
  minutes: number
  goals: number
  assists: number
  xg: number
  xa: number
  shots: number
  shots_on_target: number
  finishing_delta: number   // goals - xg
  creation_delta: number    // assists - xa
  xg_per_90: number | null
  xa_per_90: number | null
  xg_tournoi: number | null    // xG BM attendus pour tout le tournoi (équipe)
  xg_left: number | null      // xg_tournoi - xG cumulé équipe en tournoi
  xa_left: number | null      // (xg_tournoi × 0.7) - xA cumulé équipe en tournoi
  team_xg_share: number | null // % du xG tournoi généré par ce joueur
}

export async function getWCRankings(): Promise<WCPlayerRanking[]> {
  const { data } = await api.get('/api/v1/wc2026/stats/rankings')
  return data
}

// ── WC2026 Match Center ──────────────────────────────────────────────────────

export interface WCMatchListItem {
  bzz_id: number
  home_team: string
  away_team: string
  home_team_id: number | null
  away_team_id: number | null
  event_date: string | null
  status: string | null
  period: string | null
  round_number: number | null
  group_name: string | null
  home_score: number | null
  away_score: number | null
  home_score_ht: number | null
  away_score_ht: number | null
  home_xg: number | null
  away_xg: number | null
  has_detail: boolean
}

export interface WCShotPoint {
  x: number
  y: number
  xg: number
  type: string
  body: string | null
  sit: string | null
  player_id: number | null
  is_home: boolean
  minute: number | null
}

export interface WCXgMinute {
  m: number
  xg_home: number
  xg_away: number
  cum_home: number
  cum_away: number
}

export interface WCIncident {
  type: string
  minute: number | null
  added_time: number | null
  is_home: boolean | null
  player: string | null
  player_id: number | null
  assist: string | null
  is_own_goal: boolean | null
  card_type: string | null
  player_in: string | null
  player_out: string | null
  player_in_id: number | null
  player_out_id: number | null
  text: string | null
  home_score: number | null
  away_score: number | null
  length: number | null
}

export interface WCTeamStats {
  shots: number | null
  shots_on_target: number | null
  possession: number | null
  corners: number | null
  fouls: number | null
  yellow_cards: number | null
  red_cards: number | null
  passes: number | null
  pass_accuracy: number | null
  offsides: number | null
}

export interface WCPlayerStat {
  player_id: number
  player_name: string | null
  team_id: number | null
  is_home: boolean | null
  minutes_played: number | null
  rating: number | null
  goals: number | null
  goal_assist: number | null
  expected_goals: number | null
  expected_assists: number | null
  total_shots: number | null
  shots_on_target: number | null
  key_pass: number | null
  total_pass: number | null
  accurate_pass: number | null
  duel_won: number | null
  duel_lost: number | null
  yellow_card: number | null
  red_card: number | null
}

export interface WCMatchLineupPlayer {
  id: number
  name: string
  short_name: string | null
  position: string   // G | D | M | F
  jersey_number: number | null
}

export interface WCTeamLineup {
  team_id: number | null
  formation: string | null
  players: WCMatchLineupPlayer[]
  substitutes: WCMatchLineupPlayer[]
}

export interface WCMatchDetail {
  bzz_id: number
  home_team: string
  away_team: string
  home_team_id: number | null
  away_team_id: number | null
  event_date: string | null
  status: string | null
  period: string | null
  round_number: number | null
  group_name: string | null
  home_score: number | null
  away_score: number | null
  home_score_ht: number | null
  away_score_ht: number | null
  home_xg: number | null
  away_xg: number | null
  incidents: WCIncident[]
  shotmap: WCShotPoint[]
  xg_per_minute: WCXgMinute[]
  home_stats: WCTeamStats
  away_stats: WCTeamStats
  player_stats: WCPlayerStat[]
  home_lineup: WCTeamLineup | null
  away_lineup: WCTeamLineup | null
}

export async function getWCMatches(): Promise<WCMatchListItem[]> {
  const { data } = await api.get('/api/v1/wc2026/matches')
  return data
}

export async function getWCMatchDetail(bzzId: number): Promise<WCMatchDetail> {
  const { data } = await api.get(`/api/v1/wc2026/matches/${bzzId}`, { timeout: 30_000 })
  return data
}

export interface SyncStatsResult {
  synced: number
  skipped: number
  errors: string[]
}

export async function syncWCStats(): Promise<SyncStatsResult> {
  const { data } = await api.post('/api/v1/wc2026/matches/sync-stats', {}, { timeout: 120_000 })
  return data
}
