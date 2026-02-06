import axios from 'axios'
import { getSession } from 'next-auth/react'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export const api = axios.create({
  baseURL: API_URL,
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

// Types
export interface Recommendation {
  id: string
  fixture_id: string
  fixture_name: string
  kickoff_utc: string
  player_id: string
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
  const { data } = await api.get('/api/v1/recommendations', { params })
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
