'use client'

import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronUp, Check, X, Loader2 } from 'lucide-react'
import { clsx } from 'clsx'
import { patchRecommendation } from '@/lib/api'
import { LineupDisplay, LineupData } from '@/components/lineups/LineupDisplay'

interface Recommendation {
  id: number
  player: string
  team: string
  opponent: string
  market: string
  fairOdds: number
  bestOdds: number
  bookmaker: string
  edge: number
  confidence: number
  kickoff: string
  explanation?: Record<string, any>
  status?: 'pending' | 'approved' | 'rejected'
  lineup?: LineupData | null
  xg_source?: string | null
}

function XgSourceBadge({ source }: { source: string | null | undefined }) {
  if (!source) {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-500">
        xG · indisponible
      </span>
    );
  }
  const isOddsPortal = source === "oddsportal";
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
        isOddsPortal
          ? "bg-green-100 text-green-700"
          : "bg-orange-100 text-orange-700"
      }`}
    >
      {isOddsPortal
        ? "xG · OddsPortal"
        : `xG · ${source.charAt(0).toUpperCase() + source.slice(1)} (fallback)`}
    </span>
  );
}

interface RecommendationCardProps {
  recommendation: Recommendation
}

export function RecommendationCard({ recommendation: rec }: RecommendationCardProps) {
  const [expanded, setExpanded] = useState(false)
  const [status, setStatus] = useState<'pending' | 'approved' | 'rejected'>(
    (rec.status as 'pending' | 'approved' | 'rejected') ?? 'pending'
  )
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: (newStatus: 'approved' | 'rejected') =>
      patchRecommendation(rec.id, { status: newStatus }),
    onSuccess: (_data, newStatus) => {
      setStatus(newStatus)
      queryClient.invalidateQueries({ queryKey: ['history'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard-stats'] })
      queryClient.invalidateQueries({ queryKey: ['recommendations'] })
    },
  })

  const handleApprove = () => mutation.mutate('approved')
  const handleReject = () => mutation.mutate('rejected')
  const handleCancel = () => {
    // Reset to pending via API
    patchRecommendation(rec.id, { status: 'pending' as any }).then(() => {
      setStatus('pending')
      queryClient.invalidateQueries({ queryKey: ['history'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard-stats'] })
    })
  }

  const edgePercent = (rec.edge * 100).toFixed(1)
  const confidencePercent = (rec.confidence * 100).toFixed(0)

  const kickoffDate = new Date(rec.kickoff)
  const timeStr = rec.kickoff
    ? `${String(kickoffDate.getUTCHours()).padStart(2, '0')}:${String(kickoffDate.getUTCMinutes()).padStart(2, '0')} UTC`
    : '--:--'

  return (
    <div className={clsx(
      'bg-gray-800 rounded-xl overflow-hidden transition-all',
      status === 'approved' && 'ring-2 ring-green-500',
      status === 'rejected' && 'opacity-50'
    )}>
      {/* Main content */}
      <div className="p-4 sm:p-5">
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <span className={clsx(
                'px-2 py-0.5 rounded text-xs font-medium',
                rec.market === 'goalscorer'
                  ? 'bg-orange-500/20 text-orange-400'
                  : 'bg-blue-500/20 text-blue-400'
              )}>
                {rec.market === 'goalscorer' ? 'Buteur' : 'Passeur'}
              </span>
              <XgSourceBadge source={rec.xg_source} />
              <span className="text-xs text-gray-500">{timeStr}</span>
            </div>
            <h3 className="text-lg font-semibold text-white mt-1">
              {rec.player}
            </h3>
            <p className="text-sm text-gray-400">
              {rec.team} vs {rec.opponent}
            </p>
          </div>

          {/* Edge badge */}
          <div className="text-right">
            <div className="text-xl sm:text-2xl font-bold text-green-400">
              +{edgePercent}%
            </div>
            <div className="text-xs text-gray-500">edge</div>
          </div>
        </div>

        {/* Odds comparison */}
        <div className="mt-4 flex items-center gap-4">
          <div className="flex-1 bg-gray-700/50 rounded-lg p-3">
            <div className="text-xs text-gray-500">Fair Odds</div>
            <div className="text-lg font-semibold text-white">{rec.fairOdds.toFixed(2)}</div>
          </div>
          <div className="text-gray-500">&rarr;</div>
          <div className="flex-1 bg-green-500/10 rounded-lg p-3 border border-green-500/30">
            <div className="text-xs text-green-400">{rec.bookmaker}</div>
            <div className="text-lg font-semibold text-green-400">{rec.bestOdds.toFixed(2)}</div>
          </div>
        </div>

        {/* Confidence bar */}
        <div className="mt-4">
          <div className="flex justify-between text-xs text-gray-500 mb-1">
            <span>Confiance</span>
            <span>{confidencePercent}%</span>
          </div>
          <div className="h-1.5 bg-gray-700 rounded-full overflow-hidden">
            <div
              className="h-full bg-brand-500 rounded-full transition-all"
              style={{ width: `${confidencePercent}%` }}
            />
          </div>
        </div>

        {/* Actions */}
        {status === 'pending' && (
          <div className="mt-4 flex gap-2">
            <button
              onClick={handleApprove}
              disabled={mutation.isPending}
              className="flex-1 flex items-center justify-center gap-2 py-2 bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white rounded-lg transition-colors"
            >
              {mutation.isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Check className="w-4 h-4" />
              )}
              Approuver
            </button>
            <button
              onClick={handleReject}
              disabled={mutation.isPending}
              className="flex-1 flex items-center justify-center gap-2 py-2 bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-gray-300 rounded-lg transition-colors"
            >
              {mutation.isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <X className="w-4 h-4" />
              )}
              Rejeter
            </button>
          </div>
        )}

        {mutation.isError && (
          <p className="mt-2 text-xs text-red-400">
            Erreur: {mutation.error instanceof Error ? mutation.error.message : 'Echec de la mise a jour'}
          </p>
        )}

        {status !== 'pending' && (
          <div className="mt-4 flex items-center justify-between">
            <span className={clsx(
              'text-sm font-medium',
              status === 'approved' ? 'text-green-400' : 'text-gray-500'
            )}>
              {status === 'approved' ? 'Approuve' : 'Rejete'}
            </span>
            <button
              onClick={handleCancel}
              className="text-xs text-gray-500 hover:text-gray-300"
            >
              Annuler
            </button>
          </div>
        )}
      </div>

      {/* Expand toggle */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full px-5 py-2 flex items-center justify-center gap-1 text-sm text-gray-500 hover:text-gray-300 hover:bg-gray-700/50 transition-colors"
      >
        {expanded ? (
          <>Moins de details <ChevronUp className="w-4 h-4" /></>
        ) : (
          <>Plus de details <ChevronDown className="w-4 h-4" /></>
        )}
      </button>

      {/* Expanded details */}
      {expanded && (
        <div className="px-5 pb-5 border-t border-gray-700 pt-4 space-y-4">
          {rec.lineup && (
            <div>
              <p className="text-xs font-medium text-gray-500 mb-2 uppercase tracking-wide">Compo {rec.team}</p>
              <LineupDisplay lineup={rec.lineup} />
            </div>
          )}
          <div className="text-sm text-gray-400 space-y-2">
            {rec.explanation?.inputs ? (() => {
              const inputs = rec.explanation.inputs
              const calc = rec.explanation.calculation || {}
              const isGoalscorer = rec.market === 'goalscorer' || rec.market === 'Buteur'
              return (
                <>
                  <p>
                    <strong className="text-gray-300">Calcul:</strong>{' '}
                    {calc.formula || 'Poisson based on xG/90, opponent and form adjustments'}
                  </p>
                  <p>
                    <strong className="text-gray-300">Inputs:</strong>{' '}
                    {isGoalscorer
                      ? `xG/90 = ${inputs.xg_per_90?.toFixed(2) ?? '—'}, mins attendues = ${inputs.expected_minutes?.toFixed(0) ?? '—'}, forme = ${inputs.form_factor?.toFixed(2) ?? '—'}`
                      : `xA/90 = ${inputs.xa_per_90?.toFixed(2) ?? '—'}, mins attendues = ${inputs.expected_minutes?.toFixed(0) ?? '—'}, forme = ${inputs.form_factor?.toFixed(2) ?? '—'}`
                    }
                  </p>
                  <p>
                    <strong className="text-gray-300">Lambda:</strong>{' '}
                    {calc.adjusted_lambda?.toFixed(3) ?? '—'} &rarr; {isGoalscorer ? 'P(score)' : 'P(assist)'} = {rec.fairOdds > 0 ? `${(100 / rec.fairOdds).toFixed(1)}%` : '—'}
                  </p>
                  {rec.explanation.interpretation && (
                    <p className="italic text-gray-500">{rec.explanation.interpretation}</p>
                  )}
                </>
              )
            })() : (
              <p>Aucun detail de calcul disponible.</p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
