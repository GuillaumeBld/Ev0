'use client'

import { useState } from 'react'
import { ChevronDown, ChevronUp, ExternalLink, Check, X } from 'lucide-react'
import { clsx } from 'clsx'

interface Recommendation {
  id: string
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
}

interface RecommendationCardProps {
  recommendation: Recommendation
}

export function RecommendationCard({ recommendation: rec }: RecommendationCardProps) {
  const [expanded, setExpanded] = useState(false)
  const [status, setStatus] = useState<'pending' | 'approved' | 'rejected'>('pending')

  const edgePercent = (rec.edge * 100).toFixed(1)
  const confidencePercent = (rec.confidence * 100).toFixed(0)

  const kickoffDate = new Date(rec.kickoff)
  const timeStr = kickoffDate.toLocaleTimeString('fr-FR', { 
    hour: '2-digit', 
    minute: '2-digit' 
  })

  return (
    <div className={clsx(
      'bg-gray-800 rounded-xl overflow-hidden transition-all',
      status === 'approved' && 'ring-2 ring-green-500',
      status === 'rejected' && 'opacity-50'
    )}>
      {/* Main content */}
      <div className="p-5">
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-2">
              <span className={clsx(
                'px-2 py-0.5 rounded text-xs font-medium',
                rec.market === 'Buteur' 
                  ? 'bg-orange-500/20 text-orange-400' 
                  : 'bg-blue-500/20 text-blue-400'
              )}>
                {rec.market === 'Buteur' ? '🎯' : '🅰️'} {rec.market}
              </span>
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
            <div className="text-2xl font-bold text-green-400">
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
          <div className="text-gray-500">→</div>
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
              onClick={() => setStatus('approved')}
              className="flex-1 flex items-center justify-center gap-2 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg transition-colors"
            >
              <Check className="w-4 h-4" />
              Approuver
            </button>
            <button
              onClick={() => setStatus('rejected')}
              className="flex-1 flex items-center justify-center gap-2 py-2 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded-lg transition-colors"
            >
              <X className="w-4 h-4" />
              Rejeter
            </button>
          </div>
        )}

        {status !== 'pending' && (
          <div className="mt-4 flex items-center justify-between">
            <span className={clsx(
              'text-sm font-medium',
              status === 'approved' ? 'text-green-400' : 'text-gray-500'
            )}>
              {status === 'approved' ? '✓ Approuvé' : '✗ Rejeté'}
            </span>
            <button
              onClick={() => setStatus('pending')}
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
          <>Moins de détails <ChevronUp className="w-4 h-4" /></>
        ) : (
          <>Plus de détails <ChevronDown className="w-4 h-4" /></>
        )}
      </button>

      {/* Expanded details */}
      {expanded && (
        <div className="px-5 pb-5 border-t border-gray-700 pt-4">
          <div className="text-sm text-gray-400 space-y-2">
            <p><strong className="text-gray-300">Calcul:</strong> Poisson λ basé sur xG/90, ajustements adversaire et forme</p>
            <p><strong className="text-gray-300">Inputs:</strong> xG/90 = 0.52, mins attendues = 85, forme = 1.08</p>
            <p><strong className="text-gray-300">Lambda:</strong> 0.47 → P(score) = 37.5%</p>
          </div>
        </div>
      )}
    </div>
  )
}
