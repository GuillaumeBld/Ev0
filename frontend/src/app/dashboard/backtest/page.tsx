'use client'

import { useState } from 'react'
import { 
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar, ScatterChart, Scatter, ZAxis
} from 'recharts'
import { Play, Calendar, TrendingUp, Target, AlertTriangle } from 'lucide-react'

export default function BacktestPage() {
  const [isRunning, setIsRunning] = useState(false)
  const [results, setResults] = useState<BacktestResults | null>(mockResults)

  const handleRunBacktest = async () => {
    setIsRunning(true)
    // Simulate backtest
    await new Promise(r => setTimeout(r, 2000))
    setResults(mockResults)
    setIsRunning(false)
  }

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
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
            <select className="w-full mt-1 bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white">
              <option>6 derniers mois</option>
              <option>12 derniers mois</option>
              <option>Saison 2023-24</option>
            </select>
          </div>
          <div>
            <label className="text-sm text-gray-400">Edge minimum</label>
            <select className="w-full mt-1 bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white">
              <option>5%</option>
              <option>8%</option>
              <option>10%</option>
            </select>
          </div>
          <div>
            <label className="text-sm text-gray-400">Stake</label>
            <select className="w-full mt-1 bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white">
              <option>Flat 10€</option>
              <option>Kelly 25%</option>
              <option>Kelly 10%</option>
            </select>
          </div>
          <div>
            <label className="text-sm text-gray-400">Marchés</label>
            <select className="w-full mt-1 bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white">
              <option>Buteur + Passeur</option>
              <option>Buteur only</option>
              <option>Passeur only</option>
            </select>
          </div>
        </div>
      </div>

      {results && (
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

interface BacktestResults {
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

const mockResults: BacktestResults = {
  roi: 0.082,
  brierScore: 0.198,
  winRate: 0.38,
  wins: 285,
  losses: 465,
  totalBets: 750,
  pnlCurve: [
    { date: 'Sep', cumPnl: 0 },
    { date: 'Oct', cumPnl: 150 },
    { date: 'Nov', cumPnl: 80 },
    { date: 'Dec', cumPnl: 220 },
    { date: 'Jan', cumPnl: 180 },
    { date: 'Feb', cumPnl: 320 },
  ],
  calibration: [
    { predicted: 0.15, actual: 0.14, count: 120 },
    { predicted: 0.25, actual: 0.23, count: 200 },
    { predicted: 0.35, actual: 0.36, count: 250 },
    { predicted: 0.45, actual: 0.42, count: 150 },
    { predicted: 0.55, actual: 0.58, count: 30 },
  ],
  edgeDistribution: [
    { bucket: '5-8%', count: 320, roi: 3.2 },
    { bucket: '8-12%', count: 250, roi: 7.5 },
    { bucket: '12-15%', count: 120, roi: 11.2 },
    { bucket: '15%+', count: 60, roi: 18.5 },
  ],
}
