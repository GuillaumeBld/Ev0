'use client'

import { clsx } from 'clsx'
import { type WCShotPoint } from '@/lib/api'

// Pitch dimensions (scaled to SVG viewBox)
// Bzzoiro positions: x = 0..100 (left to right), y = 0..100 (top to bottom)
// We draw the attacking third only (from midfield to goal) in SVG
// Home team attacks right→left (x > 50), away attacks left→right (x < 50)
// But shotmap x/y are normalised so that x=0 = attacker's goal mouth
// x=100 = attacker's own half → we flip x so shots go left to right visually

const W = 340
const H = 220
const GOAL_W = 36
const GOAL_LINE = W - 10  // right edge = goal line
const HALF_LINE = 0       // left edge = midfield

function shotToSvg(shot: WCShotPoint): { cx: number; cy: number } {
  // pos.x: 0 = goal mouth, 100 = halfway line — flip so goal is on right
  const cx = HALF_LINE + (1 - shot.x / 100) * (GOAL_LINE - HALF_LINE)
  const cy = (shot.y / 100) * H
  return { cx, cy }
}

function xgToRadius(xg: number): number {
  return 4 + Math.sqrt(xg) * 18
}

const TYPE_COLOR: Record<string, { fill: string; stroke: string }> = {
  goal:    { fill: '#22c55e', stroke: '#16a34a' },
  saved:   { fill: '#3b82f6', stroke: '#1d4ed8' },
  blocked: { fill: '#f59e0b', stroke: '#b45309' },
  miss:    { fill: '#6b7280', stroke: '#4b5563' },
}

function shotColor(type: string) {
  return TYPE_COLOR[type] ?? TYPE_COLOR.miss
}

interface ShotMapProps {
  shots: WCShotPoint[]
  homeTeam: string
  awayTeam: string
  side: 'home' | 'away' | 'both'
}

export function ShotMap({ shots, homeTeam, awayTeam, side }: ShotMapProps) {
  const visible = shots.filter((s) => {
    if (side === 'home') return s.is_home
    if (side === 'away') return !s.is_home
    return true
  })

  return (
    <div className="w-full">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-auto"
        style={{ maxHeight: 200 }}
      >
        {/* Pitch background */}
        <rect x={0} y={0} width={W} height={H} fill="#1a2e1a" rx={4} />

        {/* Penalty area */}
        <rect x={W - 110} y={H / 2 - 66} width={110} height={132}
          fill="none" stroke="#2d5016" strokeWidth={1} />

        {/* Six-yard box */}
        <rect x={W - 42} y={H / 2 - 27} width={42} height={54}
          fill="none" stroke="#2d5016" strokeWidth={1} />

        {/* Goal */}
        <rect x={GOAL_LINE} y={H / 2 - GOAL_W / 2} width={8} height={GOAL_W}
          fill="#374151" stroke="#4b5563" strokeWidth={1} />

        {/* Penalty spot */}
        <circle cx={W - 82} cy={H / 2} r={2} fill="#2d5016" />

        {/* Shots */}
        {visible.map((s, i) => {
          const { cx, cy } = shotToSvg(s)
          const r = xgToRadius(s.xg)
          const { fill, stroke } = shotColor(s.type)
          const isHome = s.is_home
          return (
            <g key={i}>
              <circle
                cx={cx} cy={cy} r={r}
                fill={fill}
                fillOpacity={0.55}
                stroke={side === 'both' ? (isHome ? '#60a5fa' : '#f87171') : stroke}
                strokeWidth={side === 'both' ? 1.5 : 1}
                strokeOpacity={0.8}
              >
                <title>
                  {s.type.toUpperCase()} • xG {s.xg.toFixed(3)} • {s.minute}'
                  {s.body ? ` • ${s.body}` : ''}
                  {s.sit ? ` • ${s.sit}` : ''}
                </title>
              </circle>
              {s.type === 'goal' && (
                <text x={cx} y={cy + 1} textAnchor="middle" dominantBaseline="middle"
                  fontSize={7} fill="white" fontWeight="bold">G</text>
              )}
            </g>
          )
        })}

        {/* Legend */}
        {[
          { type: 'goal',    label: 'But' },
          { type: 'saved',   label: 'Arrêté' },
          { type: 'blocked', label: 'Bloqué' },
          { type: 'miss',    label: 'Raté' },
        ].map(({ type, label }, i) => {
          const { fill } = shotColor(type)
          return (
            <g key={type} transform={`translate(${8 + i * 82}, ${H - 16})`}>
              <circle cx={5} cy={5} r={5} fill={fill} fillOpacity={0.7} />
              <text x={14} y={9} fontSize={9} fill="#9ca3af">{label}</text>
            </g>
          )
        })}
      </svg>

      {/* Side toggle label */}
      {side === 'both' && (
        <div className="flex justify-center gap-4 text-[10px] mt-1">
          <span className="flex items-center gap-1">
            <span className="inline-block w-2.5 h-2.5 rounded-full border-2 border-blue-400 bg-transparent" />
            <span className="text-blue-400">{homeTeam}</span>
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block w-2.5 h-2.5 rounded-full border-2 border-red-400 bg-transparent" />
            <span className="text-red-400">{awayTeam}</span>
          </span>
        </div>
      )}
    </div>
  )
}
