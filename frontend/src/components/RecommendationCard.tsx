'use client'

import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronUp, Check, X, Loader2 } from 'lucide-react'
import { clsx } from 'clsx'
import { patchRecommendation } from '@/lib/api'
import { LineupDisplay, LineupData } from '@/components/lineups/LineupDisplay'
import { XgBadge } from './XgBadge'

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
  is_pen_taker?: boolean
}

function XgSourceBadge({ source }: { source: string | null | undefined }) {
  // New sources (bzzoiro / model) — delegate to XgBadge
  if (source === 'bzzoiro' || source === 'model') {
    return <XgBadge source={source} />
  }
  if (!source) {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-[var(--ev-hover)] text-ev-t4 border border-ev-bd">
        xG · indisponible
      </span>
    )
  }
  // Legacy sources (oddsportal, understat, etc.)
  const isOddsPortal = source === 'oddsportal'
  return (
    <span
      className={clsx(
        'inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium border',
        isOddsPortal
          ? 'bg-emerald-500/[0.08] text-emerald-500 border-emerald-500/20'
          : 'bg-amber-500/[0.08] text-amber-500 border-amber-500/20'
      )}
    >
      {isOddsPortal
        ? 'xG · OddsPortal'
        : `xG · ${source.charAt(0).toUpperCase() + source.slice(1)} (fallback)`}
    </span>
  )
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

  // Semantic edge coloring
  const edgeColor =
    rec.edge >= 0.15 ? 'text-amber-400' :
    rec.edge >= 0.08 ? 'text-emerald-400' :
    'text-emerald-500/80'

  const edgeBorderColor =
    status === 'approved' ? 'border-l-emerald-500' :
    status === 'rejected' ? 'border-l-white/[0.04]' :
    rec.edge >= 0.15 ? 'border-l-amber-500/70' :
    rec.edge >= 0.08 ? 'border-l-emerald-500/60' :
    'border-l-emerald-500/30'

  // Semantic confidence coloring
  const confidenceColor =
    rec.confidence >= 0.7 ? 'text-amber-400' :
    rec.confidence >= 0.5 ? 'text-emerald-400' :
    'text-ev-t4'

  const confidenceBgColor =
    rec.confidence >= 0.7 ? 'bg-amber-500' :
    rec.confidence >= 0.5 ? 'bg-emerald-500' :
    'bg-slate-700'

  return (
    <div className={clsx(
      'ev0-rec-card ev0-card-enter bg-ev-surface border border-ev-bd border-l-2 rounded-xl overflow-hidden transition-all',
      edgeBorderColor,
      status === 'rejected' && 'opacity-40 grayscale',
    )}>
      {/* Main content */}
      <div className="p-4 sm:p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className={clsx(
                'inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold border uppercase tracking-wide',
                rec.market === 'goalscorer'
                  ? 'bg-orange-500/[0.08] text-orange-400 border-orange-500/20'
                  : 'bg-sky-500/[0.08] text-sky-400 border-sky-500/20'
              )}>
                {rec.market === 'goalscorer' ? 'Buteur' : 'Passeur'}
              </span>
              <XgSourceBadge source={rec.xg_source} />
              <span className="text-[10px] text-ev-t5 font-mono">{timeStr}</span>
            </div>
            <h3 className="text-base font-semibold text-white mt-2 leading-tight flex items-center gap-1.5">
              {rec.player}
              {rec.is_pen_taker && (
                <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-400 border border-amber-500/30 tracking-wide">
                  PEN
                </span>
              )}
            </h3>
            <p className="text-xs text-ev-t4 mt-0.5">
              {rec.team} <span className="text-ev-t5">vs</span> {rec.opponent}
            </p>
          </div>

          {/* Edge badge */}
          <div className="text-right shrink-0">
            <div className={clsx('text-2xl font-mono font-bold tabular-nums leading-none', edgeColor)}>
              +{edgePercent}%
            </div>
            <div className="text-[10px] text-ev-t5 mt-1 uppercase tracking-widest">edge</div>
          </div>
        </div>

        {/* Odds comparison */}
        <div className="mt-4 flex items-center gap-3">
          <div className="flex-1 bg-ev-surface2 border border-ev-bd2 rounded-lg p-3">
            <div className="text-[10px] text-ev-t4 uppercase tracking-widest mb-1">Fair Odds</div>
            <div className="text-lg font-mono font-semibold text-ev-t2 tabular-nums">
              {rec.fairOdds.toFixed(2)}
            </div>
          </div>
          <div className="text-ev-t5 text-xs">→</div>
          <div className="flex-1 bg-emerald-500/[0.06] border border-emerald-500/20 rounded-lg p-3">
            <div className="text-[10px] text-emerald-600 uppercase tracking-widest mb-1">{rec.bookmaker}</div>
            <div className="text-lg font-mono font-semibold text-emerald-400 tabular-nums">
              {rec.bestOdds.toFixed(2)}
            </div>
          </div>
        </div>

        {/* Confidence bar */}
        <div className="mt-4">
          <div className="flex justify-between items-center mb-2">
            <span className="text-[10px] text-ev-t4 uppercase tracking-widest">Confiance</span>
            <span className={clsx('text-xs font-mono font-medium', confidenceColor)}>{confidencePercent}%</span>
          </div>
          <div className="h-px bg-white/[0.06] rounded-full overflow-hidden">
            <div
              className={clsx('h-full rounded-full ev0-confidence-bar', confidenceBgColor)}
              style={{ width: `${confidencePercent}%` }}
            />
          </div>
        </div>

        {/* Actions */}
        {status === 'pending' && (
          <div className="mt-5 flex gap-2">
            <button
              onClick={handleApprove}
              disabled={mutation.isPending}
              className="ev0-btn-approve flex-1 flex items-center justify-center gap-1.5 py-2 bg-emerald-500/[0.08] hover:bg-emerald-500/[0.15] disabled:opacity-40 text-emerald-400 border border-emerald-500/25 rounded-full text-sm font-medium transition-colors duration-200"
            >
              {mutation.isPending ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Check className="w-3.5 h-3.5" />
              )}
              Approuver
            </button>
            <button
              onClick={handleReject}
              disabled={mutation.isPending}
              className="ev0-btn-reject flex-1 flex items-center justify-center gap-1.5 py-2 bg-transparent hover:bg-rose-500/[0.08] disabled:opacity-40 text-ev-t3 hover:text-rose-400 border border-ev-bd hover:border-rose-500/25 rounded-full text-sm font-medium transition-all duration-200"
            >
              {mutation.isPending ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <X className="w-3.5 h-3.5" />
              )}
              Rejeter
            </button>
          </div>
        )}

        {mutation.isError && (
          <p className="mt-2 text-xs text-rose-400">
            Erreur: {mutation.error instanceof Error ? mutation.error.message : 'Echec de la mise à jour'}
          </p>
        )}

        {status !== 'pending' && (
          <div className="mt-4 flex items-center justify-between">
            <span className={clsx(
              'text-xs font-medium uppercase tracking-widest',
              status === 'approved' ? 'text-emerald-500' : 'text-ev-t4'
            )}>
              {status === 'approved' ? 'Approuvé' : 'Rejeté'}
            </span>
            <button
              onClick={handleCancel}
              className="text-[11px] text-ev-t4 hover:text-ev-t3 transition-colors"
            >
              Annuler
            </button>
          </div>
        )}
      </div>

      {/* Expand toggle */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full px-5 py-2.5 flex items-center justify-center gap-1.5 text-[11px] text-ev-t5 hover:text-ev-t3 border-t border-ev-bd2 hover:bg-[var(--ev-hover)] transition-colors"
      >
        {expanded ? (
          <>Moins de détails <ChevronUp className="w-3 h-3" /></>
        ) : (
          <>Plus de détails <ChevronDown className="w-3 h-3" /></>
        )}
      </button>

      {/* Expanded details */}
      {expanded && (
        <div className="px-5 pb-5 border-t border-ev-bd2 pt-4 space-y-4">
          {rec.lineup && (
            <div>
              <p className="text-[10px] font-semibold text-ev-t4 mb-2 uppercase tracking-[0.14em]">Compo {rec.team}</p>
              <LineupDisplay lineup={rec.lineup} />
            </div>
          )}
          <div className="text-xs text-ev-t4 space-y-2">
            {rec.explanation?.inputs ? (() => {
              const inputs = rec.explanation.inputs
              const calc = rec.explanation.calculation || {}
              const isGoalscorer = rec.market === 'goalscorer' || rec.market === 'Buteur'
              return (
                <>
                  <p>
                    <strong className="text-ev-t3 font-medium">Calcul:</strong>{' '}
                    {calc.formula || 'Poisson based on xG/90, opponent and form adjustments'}
                  </p>
                  <p>
                    <strong className="text-ev-t3 font-medium">Inputs:</strong>{' '}
                    {isGoalscorer
                      ? `xG/90 = ${inputs.xg_per_90?.toFixed(2) ?? '—'}, mins attendues = ${inputs.expected_minutes?.toFixed(0) ?? '—'}, forme = ${inputs.form_factor?.toFixed(2) ?? '—'}`
                      : `xA/90 = ${inputs.xa_per_90?.toFixed(2) ?? '—'}, mins attendues = ${inputs.expected_minutes?.toFixed(0) ?? '—'}, forme = ${inputs.form_factor?.toFixed(2) ?? '—'}`
                    }
                  </p>
                  <p>
                    <strong className="text-ev-t3 font-medium">Lambda:</strong>{' '}
                    {calc.adjusted_lambda?.toFixed(3) ?? '—'} &rarr; {isGoalscorer ? 'P(score)' : 'P(assist)'} = {rec.fairOdds > 0 ? `${(100 / rec.fairOdds).toFixed(1)}%` : '—'}
                  </p>
                  {rec.explanation.interpretation && (
                    <p className="italic text-ev-t5">{rec.explanation.interpretation}</p>
                  )}
                </>
              )
            })() : (
              <p>Aucun détail de calcul disponible.</p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
