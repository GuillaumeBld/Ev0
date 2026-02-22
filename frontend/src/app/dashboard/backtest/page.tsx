'use client'

import { useState, useRef } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar, ScatterChart, Scatter, ZAxis
} from 'recharts'
import { Play, Calendar, TrendingUp, Target, AlertTriangle } from 'lucide-react'
import { runBacktest } from '@/lib/api'
import type { BacktestResults } from '@/lib/api'

export default function BacktestPage() {
  const [isRunning, setIsRunning] = useState(false)
  const [results, setResults] = useState<BacktestResults | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Config refs
  const periodRef = useRef<HTMLSelectElement>(null)
  const edgeRef = useRef<HTMLSelectElement>(null)
  const stakeRef = useRef<HTMLSelectElement>(null)
  const marketsRef = useRef<HTMLSelectElement>(null)

  const handleRunBacktest = async () => {
    setIsRunning(true)
    setError(null)
    try {
      // Read config from selects
      const periodVal = periodRef.current?.value || '6 derniers mois'
      const edgeVal = edgeRef.current?.value || '5%'
      const stakeVal = stakeRef.current?.value || 'Flat 10€'
      const marketsVal = marketsRef.current?.value || 'Buteur + Passeur'

      // Map display values to API values
      const periodMap: Record<string, string> = {
        '6 derniers mois': '6m',
        '12 derniers mois': '12m',
        'Saison 2023-24': 'season_2023_24',
      }
      const edgeMap: Record<string, number> = {
        '5%': 0.05,
        '8%': 0.08,
        '10%': 0.10,
      }
      const stakeMap: Record<string, string> = {
        'Flat 10€': 'flat_10',
        'Kelly 25%': 'kelly_25',
        'Kelly 10%': 'kelly_10',
      }
      const marketsMap: Record<string, string> = {
        'Buteur + Passeur': 'all',
        'Buteur only': 'goalscorer',
        'Passeur only': 'assist',
      }

      const data = await runBacktest({
        period: periodMap[periodVal] || '6m',
        min_edge: edgeMap[edgeVal] || 0.05,
        stake_method: stakeMap[stakeVal] || 'flat_10',
        markets: marketsMap[marketsVal] || 'all',
      })
      setResults(data)
    } catch (err: any) {
      setError(err?.message || 'Erreur lors du backtest')
    } finally {
      setIsRunning(false)
    }
  }

  return (
    <div className="p-4 md:p-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white">Backtest</h1>
          <p className="text-gray-400 mt-1">
            Validation historique du modèle
          </p>
        </div>
        <button
          onClick={handleRunBacktest}
          disabled={isRunning}
          className="flex items-center gap-2 px-4 py-2 bg-brand-600 hover:bg-brand-700 disabled:bg-gray-600 text-white rounded-lg transition-colors"
        >
          <Play className="w-4 h-4" />
          {isRunning ? 'En cours...' : 'Lancer Backtest'}
        </button>
      </div>

      {/* Config Panel */}
      <div className="bg-gray-800 rounded-xl p-6 mb-6">
        <h2 className="text-lg font-semibold text-white mb-4">Configuration</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <label className="text-sm text-gray-400">Période</label>
            <select ref={periodRef} className="w-full mt-1 bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white">
              <option>6 derniers mois</option>
              <option>12 derniers mois</option>
              <option>Saison 2023-24</option>
            </select>
          </div>
          <div>
            <label className="text-sm text-gray-400">Edge minimum</label>
            <select ref={edgeRef} className="w-full mt-1 bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white">
              <option>5%</option>
              <option>8%</option>
              <option>10%</option>
            </select>
          </div>
          <div>
            <label className="text-sm text-gray-400">Stake</label>
            <select ref={stakeRef} className="w-full mt-1 bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white">
              <option>Flat 10€</option>
              <option>Kelly 25%</option>
              <option>Kelly 10%</option>
            </select>
          </div>
          <div>
            <label className="text-sm text-gray-400">Marchés</label>
            <select ref={marketsRef} className="w-full mt-1 bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white">
              <option>Buteur + Passeur</option>
              <option>Buteur only</option>
              <option>Passeur only</option>
            </select>
          </div>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-4 mb-6 text-red-400">
          {error}
        </div>
      )}

      {/* Empty state */}
      {!results && !isRunning && !error && (
        <div className="bg-gray-800 rounded-xl p-12 text-center">
          <Target className="w-12 h-12 text-gray-600 mx-auto mb-4" />
          <p className="text-gray-400">Lancez un backtest pour voir les résultats</p>
        </div>
      )}

      {/* Loading */}
      {isRunning && (
        <div className="bg-gray-800 rounded-xl p-12 text-center">
          <div className="w-8 h-8 border-2 border-brand-500 border-t-transparent rounded-full animate-spin mx-auto mb-4" />
          <p className="text-gray-400">Calcul en cours...</p>
        </div>
      )}

      {results && !isRunning && (
        <>
          {results.totalBets === 0 ? (
            <div className="bg-gray-800 rounded-xl p-12 text-center">
              <AlertTriangle className="w-12 h-12 text-yellow-500 mx-auto mb-4" />
              <p className="text-gray-400">Aucune donnée historique pour cette configuration</p>
            </div>
          ) : (
            <>
              {/* Key Metrics */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
                <MetricCard
                  title="ROI"
                  value={`${(results.roi * 100).toFixed(1)}%`}
                  subtitle={results.roi >= 0 ? 'Profitable' : 'En perte'}
                  positive={results.roi >= 0}
                />
                <MetricCard
                  title="Brier Score"
                  value={results.brierScore.toFixed(3)}
                  subtitle={results.brierScore < 0.22 ? 'Bon' : 'À améliorer'}
                  positive={results.brierScore < 0.22}
                />
                <MetricCard
                  title="Win Rate"
                  value={`${(results.winRate * 100).toFixed(0)}%`}
                  subtitle={`${results.wins}W / ${results.losses}L`}
                  positive={results.winRate > 0.35}
                />
                <MetricCard
                  title="Sample Size"
                  value={results.totalBets.toString()}
                  subtitle="paris analysés"
                  positive={results.totalBets >= 500}
                />
              </div>

              {/* Charts Row */}
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
                {/* P&L Curve */}
                <div className="bg-gray-800 rounded-xl p-6">
                  <h3 className="text-lg font-semibold text-white mb-4">
                    Courbe P&L
                  </h3>
                  <ResponsiveContainer width="100%" height={250}>
                    <LineChart data={results.pnlCurve}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                      <XAxis dataKey="date" stroke="#9CA3AF" fontSize={12} />
                      <YAxis stroke="#9CA3AF" fontSize={12} />
                      <Tooltip
                        contentStyle={{ backgroundColor: '#1F2937', border: 'none' }}
                        labelStyle={{ color: '#9CA3AF' }}
                      />
                      <Line
                        type="monotone"
                        dataKey="cumPnl"
                        stroke="#22C55E"
                        strokeWidth={2}
                        dot={false}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>

                {/* Calibration Plot */}
                <div className="bg-gray-800 rounded-xl p-6">
                  <h3 className="text-lg font-semibold text-white mb-4">
                    Calibration
                  </h3>
                  <ResponsiveContainer width="100%" height={250}>
                    <ScatterChart>
                      <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                      <XAxis
                        dataKey="predicted"
                        name="Prédit"
                        stroke="#9CA3AF"
                        fontSize={12}
                        domain={[0, 1]}
                      />
                      <YAxis
                        dataKey="actual"
                        name="Réel"
                        stroke="#9CA3AF"
                        fontSize={12}
                        domain={[0, 1]}
                      />
                      <ZAxis dataKey="count" range={[50, 400]} />
                      <Tooltip
                        contentStyle={{ backgroundColor: '#1F2937', border: 'none' }}
                      />
                      <Scatter
                        data={results.calibration}
                        fill="#8B5CF6"
                      />
                      {/* Perfect calibration line */}
                      <Line
                        type="monotone"
                        dataKey="predicted"
                        stroke="#4B5563"
                        strokeDasharray="5 5"
                      />
                    </ScatterChart>
                  </ResponsiveContainer>
                  <p className="text-xs text-gray-500 mt-2 text-center">
                    Points proches de la diagonale = bien calibré
                  </p>
                </div>
              </div>

              {/* Edge Distribution */}
              <div className="bg-gray-800 rounded-xl p-6">
                <h3 className="text-lg font-semibold text-white mb-4">
                  Distribution par Edge
                </h3>
                <ResponsiveContainer width="100%" height={200}>
                  <BarChart data={results.edgeDistribution}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                    <XAxis dataKey="bucket" stroke="#9CA3AF" fontSize={12} />
                    <YAxis stroke="#9CA3AF" fontSize={12} />
                    <Tooltip
                      contentStyle={{ backgroundColor: '#1F2937', border: 'none' }}
                    />
                    <Bar dataKey="count" fill="#3B82F6" radius={[4, 4, 0, 0]} />
                    <Bar dataKey="roi" fill="#22C55E" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </>
          )}
        </>
      )}
    </div>
  )
}

interface MetricCardProps {
  title: string
  value: string
  subtitle: string
  positive: boolean
}

function MetricCard({ title, value, subtitle, positive }: MetricCardProps) {
  return (
    <div className="bg-gray-800 rounded-xl p-5">
      <p className="text-sm text-gray-400">{title}</p>
      <p className={`text-2xl font-bold mt-1 ${positive ? 'text-green-400' : 'text-red-400'}`}>
        {value}
      </p>
      <p className="text-xs text-gray-500 mt-1">{subtitle}</p>
    </div>
  )
}
