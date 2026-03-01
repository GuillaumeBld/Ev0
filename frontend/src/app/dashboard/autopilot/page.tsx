'use client'

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Brain, Play, ToggleLeft, ToggleRight, AlertCircle, TrendingUp, Target, BarChart3, Zap } from 'lucide-react'
import { clsx } from 'clsx'
import {
  getAutopilotStatus,
  getAutopilotToday,
  getAutopilotPerformance,
  toggleAutopilot,
  trainAutopilot,
} from '@/lib/api'
import type { AutopilotDecisionOut } from '@/lib/api'

const ACTION_COLORS: Record<string, string> = {
  skip: 'text-gray-400',
  half_kelly: 'text-yellow-400',
  kelly: 'text-green-400',
  aggressive: 'text-brand-400',
}

const ACTION_LABELS: Record<string, string> = {
  skip: 'Passer',
  half_kelly: '½ Kelly',
  kelly: 'Kelly',
  aggressive: 'Agressif',
}

export default function AutopilotPage() {
  const queryClient = useQueryClient()
  const [trainingResult, setTrainingResult] = useState<any>(null)
  const [trainingError, setTrainingError] = useState<string | null>(null)

  const { data: status } = useQuery({
    queryKey: ['autopilot-status'],
    queryFn: getAutopilotStatus,
    refetchInterval: 30_000,
  })

  const { data: todayDecisions, isLoading: todayLoading } = useQuery({
    queryKey: ['autopilot-today'],
    queryFn: getAutopilotToday,
    enabled: status?.trained === true,
  })

  const { data: performance } = useQuery({
    queryKey: ['autopilot-performance'],
    queryFn: getAutopilotPerformance,
  })

  const toggleMutation = useMutation({
    mutationFn: ({ enabled, mode }: { enabled: boolean; mode: string }) =>
      toggleAutopilot(enabled, mode),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['autopilot-status'] }),
  })

  const [isTraining, setIsTraining] = useState(false)

  const handleTrain = async () => {
    setIsTraining(true)
    setTrainingError(null)
    setTrainingResult(null)
    try {
      const result = await trainAutopilot({ epochs: 3 })
      setTrainingResult(result)
      queryClient.invalidateQueries({ queryKey: ['autopilot-status'] })
      queryClient.invalidateQueries({ queryKey: ['autopilot-today'] })
      queryClient.invalidateQueries({ queryKey: ['autopilot-performance'] })
    } catch (err: any) {
      setTrainingError(err?.response?.data?.detail || err?.message || 'Erreur entraînement')
    } finally {
      setIsTraining(false)
    }
  }

  const enabled = status?.enabled ?? false
  const mode = status?.mode ?? 'paper'
  const metrics = status?.metrics

  return (
    <div className="p-4 md:p-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 mb-8">
        <div className="flex items-center gap-3">
          <Brain className="w-8 h-8 text-brand-500" />
          <div>
            <h1 className="text-3xl font-bold text-white">Autopilot</h1>
            <p className="text-gray-400 mt-0.5">Agent RL • Paper trading automatique</p>
          </div>
        </div>

        {/* Toggle */}
        <div className="flex items-center gap-3">
          <span className="text-sm text-gray-400">
            Mode: <span className={mode === 'live' ? 'text-red-400 font-medium' : 'text-yellow-400 font-medium'}>
              {mode === 'live' ? 'Live' : 'Paper'}
            </span>
          </span>
          <button
            onClick={() => toggleMutation.mutate({ enabled: !enabled, mode })}
            disabled={toggleMutation.isPending}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-white text-sm font-medium transition-colors"
          >
            {enabled
              ? <ToggleRight className="w-5 h-5 text-green-400" />
              : <ToggleLeft className="w-5 h-5 text-gray-400" />}
            {enabled ? 'Activé' : 'Désactivé'}
          </button>
        </div>
      </div>

      {/* Status Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <StatCard
          title="Paris (30j)"
          value={metrics ? metrics.total_bets.toString() : '—'}
          subtitle={metrics ? `${metrics.wins}W / ${metrics.losses}L` : 'aucune donnée'}
          icon={Target}
          color="brand"
        />
        <StatCard
          title="Win Rate"
          value={metrics && metrics.total_bets > 0 ? `${(metrics.win_rate * 100).toFixed(1)}%` : '—'}
          subtitle="taux de réussite"
          icon={TrendingUp}
          color="green"
        />
        <StatCard
          title="ROI"
          value={metrics && metrics.total_bets > 0 ? `${(metrics.roi * 100).toFixed(1)}%` : '—'}
          subtitle="retour sur mise"
          icon={BarChart3}
          color="blue"
        />
        <StatCard
          title="Sharpe"
          value={metrics && metrics.total_bets > 0 ? metrics.sharpe.toFixed(2) : '—'}
          subtitle="risque ajusté"
          icon={Zap}
          color="purple"
        />
      </div>

      {/* Training Section */}
      <div className="bg-gray-800 rounded-xl p-6 mb-8">
        <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <Brain className="w-5 h-5" />
          Entraînement
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-4 text-sm">
          <div>
            <p className="text-gray-400">Statut</p>
            <p className={clsx('font-medium mt-0.5', status?.trained ? 'text-green-400' : 'text-yellow-400')}>
              {status?.trained ? 'Entraîné' : 'Pas encore entraîné'}
            </p>
          </div>
          <div>
            <p className="text-gray-400">Dernier entraînement</p>
            <p className="text-white mt-0.5">
              {status?.trained_at
                ? new Date(status.trained_at).toLocaleDateString('fr-FR', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
                : '—'}
            </p>
          </div>
          <div>
            <p className="text-gray-400">Étapes d'entraînement</p>
            <p className="text-white mt-0.5">{status?.steps_trained?.toLocaleString() ?? '—'}</p>
          </div>
        </div>

        <button
          onClick={handleTrain}
          disabled={isTraining}
          className="flex items-center gap-2 px-5 py-2.5 bg-brand-600 hover:bg-brand-700 disabled:bg-brand-800 disabled:opacity-60 text-white text-sm font-medium rounded-lg transition-colors"
        >
          <Play className="w-4 h-4" />
          {isTraining ? 'Entraînement…' : 'Entraîner le modèle'}
        </button>

        {trainingResult && (
          <div className="mt-4 p-4 bg-gray-700/50 rounded-lg text-sm">
            <p className="text-green-400 font-medium mb-2">Entraînement terminé</p>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-gray-300">
              <div><span className="text-gray-400">Records: </span>{trainingResult.records_used}</div>
              <div><span className="text-gray-400">P&L: </span>{trainingResult.cumulative_pnl > 0 ? '+' : ''}{trainingResult.cumulative_pnl.toFixed(2)}€</div>
              <div><span className="text-gray-400">Sharpe: </span>{trainingResult.sharpe.toFixed(2)}</div>
              <div><span className="text-gray-400">vs Kelly: </span>{(trainingResult.vs_kelly_roi * 100).toFixed(1)}%</div>
            </div>
            {trainingResult.fine_tune?.decisions_used > 0 && (
              <p className="text-gray-400 mt-2 text-xs">
                Fine-tuned sur {trainingResult.fine_tune.decisions_used} vrais paris (TD error: {trainingResult.fine_tune.td_error_mean.toFixed(4)})
              </p>
            )}
          </div>
        )}

        {trainingError && (
          <div className="mt-4 flex items-center gap-2 text-red-400 text-sm">
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            {trainingError}
          </div>
        )}
      </div>

      {/* Today's Decisions */}
      <div className="bg-gray-800 rounded-xl p-6 mb-8">
        <h2 className="text-lg font-semibold text-white mb-4">Décisions du jour</h2>

        {!status?.trained ? (
          <p className="text-gray-500 text-sm">Entraînez l'agent pour voir ses décisions.</p>
        ) : todayLoading ? (
          <div className="space-y-2">
            {[1, 2].map(i => <div key={i} className="h-10 bg-gray-700 rounded animate-pulse" />)}
          </div>
        ) : !todayDecisions || todayDecisions.length === 0 ? (
          <p className="text-gray-500 text-sm">Aucune recommandation VALUE en attente aujourd'hui.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-500 text-xs border-b border-gray-700">
                  <th className="text-left pb-2 font-medium">Joueur</th>
                  <th className="text-left pb-2 font-medium hidden sm:table-cell">Match</th>
                  <th className="text-left pb-2 font-medium">Marché</th>
                  <th className="text-right pb-2 font-medium">Cote</th>
                  <th className="text-right pb-2 font-medium">Edge</th>
                  <th className="text-right pb-2 font-medium">Action</th>
                  <th className="text-right pb-2 font-medium">Mise</th>
                </tr>
              </thead>
              <tbody>
                {todayDecisions.map((d, i) => (
                  <DecisionRow key={i} decision={d} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* P&L Chart + Feature Importance */}
      {performance && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
          {/* P&L Chart */}
          {performance.pnl_curve.length > 0 && (
            <div className="bg-gray-800 rounded-xl p-6">
              <h3 className="text-sm font-medium text-gray-400 mb-4">Courbe P&L Autopilot</h3>
              <MiniPnlChart data={performance.pnl_curve} />
            </div>
          )}

          {/* Feature Importance */}
          {Object.keys(performance.feature_importance).length > 0 && (
            <div className="bg-gray-800 rounded-xl p-6">
              <h3 className="text-sm font-medium text-gray-400 mb-4">Importance des features</h3>
              <FeatureImportanceChart data={performance.feature_importance} />
            </div>
          )}
        </div>
      )}

      {/* Action Distribution */}
      {performance && Object.keys(performance.action_distribution).length > 0 && (
        <div className="bg-gray-800 rounded-xl p-6">
          <h3 className="text-sm font-medium text-gray-400 mb-4">Distribution des actions</h3>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            {Object.entries(performance.action_distribution).map(([action, count]) => (
              <div key={action} className="text-center">
                <p className={clsx('text-2xl font-bold', ACTION_COLORS[action] ?? 'text-white')}>{count}</p>
                <p className="text-xs text-gray-400 mt-1">{ACTION_LABELS[action] ?? action}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Sub-components ───────────────────────────────────────────────

function StatCard({
  title, value, subtitle, icon: Icon, color,
}: {
  title: string
  value: string
  subtitle: string
  icon: any
  color: 'brand' | 'green' | 'blue' | 'purple'
}) {
  const colorClasses = {
    brand: 'bg-brand-500/10 text-brand-500',
    green: 'bg-green-500/10 text-green-500',
    blue: 'bg-blue-500/10 text-blue-500',
    purple: 'bg-purple-500/10 text-purple-500',
  }
  return (
    <div className="bg-gray-800 rounded-xl p-5">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs text-gray-400">{title}</p>
          <p className="text-xl font-bold text-white mt-1">{value}</p>
          <p className="text-xs text-gray-500 mt-0.5">{subtitle}</p>
        </div>
        <div className={`p-2.5 rounded-lg ${colorClasses[color]}`}>
          <Icon className="w-5 h-5" />
        </div>
      </div>
    </div>
  )
}

function DecisionRow({ decision }: { decision: AutopilotDecisionOut }) {
  const actionColor = ACTION_COLORS[decision.action] ?? 'text-white'
  const actionLabel = ACTION_LABELS[decision.action] ?? decision.action

  return (
    <tr className="border-t border-gray-700/50">
      <td className="py-2 text-white font-medium">{decision.player_name}</td>
      <td className="py-2 text-gray-400 hidden sm:table-cell text-xs">{decision.fixture_name}</td>
      <td className="py-2 text-gray-400 capitalize">{decision.market_type}</td>
      <td className="py-2 text-right text-gray-300">{decision.best_odds.toFixed(2)}</td>
      <td className="py-2 text-right text-green-400">{(decision.edge * 100).toFixed(1)}%</td>
      <td className={clsx('py-2 text-right font-semibold', actionColor)}>{actionLabel}</td>
      <td className="py-2 text-right text-gray-300">
        {decision.stake > 0 ? `${decision.stake.toFixed(2)}€` : '—'}
      </td>
    </tr>
  )
}

function MiniPnlChart({ data }: { data: { date: string; pnl: number; cumulative: number }[] }) {
  if (data.length === 0) return null

  const values = data.map(d => d.cumulative)
  const max = Math.max(...values, 0)
  const min = Math.min(...values, 0)
  const range = max - min || 1
  const height = 100
  const width = 100

  const points = data.map((d, i) => {
    const x = data.length === 1 ? 50 : (i / (data.length - 1)) * width
    const y = height - ((d.cumulative - min) / range) * height
    return `${x},${y}`
  })

  const zeroY = height - ((0 - min) / range) * height
  const last = data[data.length - 1]

  return (
    <div>
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full" preserveAspectRatio="none" style={{ height: '100px' }}>
        <line x1="0" y1={zeroY} x2={width} y2={zeroY} stroke="#374151" strokeWidth="0.3" strokeDasharray="2,2" />
        <polyline
          points={points.join(' ')}
          fill="none"
          stroke={last.cumulative >= 0 ? '#4ade80' : '#f87171'}
          strokeWidth="1.5"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      <div className="flex justify-between mt-2 text-xs text-gray-500">
        <span>{data[0].date}</span>
        <span className={clsx('font-medium', last.cumulative >= 0 ? 'text-green-400' : 'text-red-400')}>
          {last.cumulative >= 0 ? '+' : ''}{last.cumulative.toFixed(2)} EUR
        </span>
        <span>{last.date}</span>
      </div>
    </div>
  )
}

function FeatureImportanceChart({ data }: { data: Record<string, number> }) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1])
  const maxVal = entries[0]?.[1] || 1

  const FEATURE_LABELS: Record<string, string> = {
    edge: 'Edge',
    confidence: 'Confiance',
    implied_prob: 'Prob. implicite',
    fair_prob: 'Prob. juste',
    lambda: 'Lambda (Poisson)',
    mins_ratio: 'Minutes / 90',
    is_goalscorer: 'Marché buteur',
    is_premier_league: 'Premier League',
    is_forward: 'Attaquant',
    bias: 'Biais',
  }

  return (
    <div className="space-y-2">
      {entries.map(([name, val]) => (
        <div key={name} className="flex items-center gap-3">
          <span className="text-xs text-gray-400 w-32 flex-shrink-0 truncate">
            {FEATURE_LABELS[name] ?? name}
          </span>
          <div className="flex-1 bg-gray-700 rounded-full h-2">
            <div
              className="bg-brand-500 h-2 rounded-full"
              style={{ width: `${(val / maxVal) * 100}%` }}
            />
          </div>
          <span className="text-xs text-gray-400 w-12 text-right">{val.toFixed(3)}</span>
        </div>
      ))}
    </div>
  )
}
