'use client'

import { clsx } from 'clsx'
import { type WCPlayerPricing } from '@/lib/api'
import { FlagImg } from '@/components/FlagImg'

type Mode = 'goals' | 'assists'

interface PricingTableProps {
  players: WCPlayerPricing[]
  mode: Mode
  nationFlags: Record<string, string | null>  // nation → flag_emoji
}

function EdgeBadge({ edge }: { edge: number | null }) {
  if (edge === null) return <span className="text-gray-600">—</span>
  const pct = (edge * 100).toFixed(1)
  return (
    <span className={clsx('font-medium', edge > 0 ? 'text-green-400' : 'text-red-400')}>
      {edge > 0 ? '+' : ''}{pct}%
    </span>
  )
}

function OddsCell({ value }: { value: number | null }) {
  if (!value) return <span className="text-gray-600">—</span>
  return <span>{value.toFixed(2)}</span>
}

export function PricingTable({ players, mode, nationFlags }: PricingTableProps) {
  const isGoals = mode === 'goals'

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs text-gray-300">
        <thead>
          <tr className="border-b border-gray-700 text-gray-500 uppercase tracking-wider">
            <th className="text-left py-2 px-2 font-medium">Joueur</th>
            <th className="text-left py-2 px-2 font-medium">Nat.</th>
            <th className="text-left py-2 px-2 font-medium">Pos</th>
            <th className="text-right py-2 px-2 font-medium">λ</th>
            <th className="text-right py-2 px-2 font-medium">≥1</th>
            <th className="text-right py-2 px-2 font-medium">≥2</th>
            <th className="text-right py-2 px-2 font-medium">≥3</th>
            {isGoals && <th className="text-right py-2 px-2 font-medium">≥4</th>}
            <th className="text-right py-2 px-2 font-medium">
              {isGoals ? 'Top buteur' : 'Top passeur'}
            </th>
            <th className="text-right py-2 px-2 font-medium">BK</th>
            <th className="text-right py-2 px-2 font-medium">Edge</th>
          </tr>
        </thead>
        <tbody>
          {players.map((p) => {
            const lambda     = isGoals ? p.lambda_goals   : p.lambda_assists
            const cut1       = isGoals ? p.fair_1g        : p.fair_1a
            const cut2       = isGoals ? p.fair_2g        : p.fair_2a
            const cut3       = isGoals ? p.fair_3g        : p.fair_3a
            const cut4       = isGoals ? p.fair_4g        : null
            const fairOut    = isGoals ? p.fair_top_scorer   : p.fair_top_assister
            const bkOut      = isGoals ? p.bk_top_scorer     : p.bk_top_assister
            const edgeOut    = isGoals ? p.edge_top_scorer   : p.edge_top_assister
            const flag       = nationFlags[p.nation]

            return (
              <tr key={`${p.nation}-${p.player_name}`} className="border-b border-gray-800 hover:bg-gray-800/40">
                <td className="py-1.5 px-2 font-medium text-white">{p.player_name}</td>
                <td className="py-1.5 px-2">
                  <span className="flex items-center gap-1">
                    <FlagImg emoji={flag} size={14} />
                    <span className="text-gray-400 text-[10px]">{p.nation}</span>
                  </span>
                </td>
                <td className="py-1.5 px-2 text-gray-500">{p.position ?? '—'}</td>
                <td className="py-1.5 px-2 text-right font-mono text-orange-300">{lambda.toFixed(2)}</td>
                <td className="py-1.5 px-2 text-right"><OddsCell value={cut1} /></td>
                <td className="py-1.5 px-2 text-right"><OddsCell value={cut2} /></td>
                <td className="py-1.5 px-2 text-right"><OddsCell value={cut3} /></td>
                {isGoals && <td className="py-1.5 px-2 text-right"><OddsCell value={cut4} /></td>}
                <td className="py-1.5 px-2 text-right"><OddsCell value={fairOut} /></td>
                <td className="py-1.5 px-2 text-right text-gray-400"><OddsCell value={bkOut} /></td>
                <td className="py-1.5 px-2 text-right"><EdgeBadge edge={edgeOut} /></td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {players.length === 0 && (
        <p className="text-center text-gray-600 text-sm py-8">Aucune donnée — clique Recalculer</p>
      )}
    </div>
  )
}
