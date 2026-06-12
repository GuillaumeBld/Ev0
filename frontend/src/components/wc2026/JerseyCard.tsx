'use client'

import { useState, useRef, useEffect } from 'react'
import { clsx } from 'clsx'

type Role = 'starter' | 'sub_planned' | 'sub_tactical' | 'reserve'

interface JerseyCardProps {
  playerName: string
  shirtNumber: number | null
  expectedMinutes: number
  isSelected: boolean
  role: Role
  onClick: () => void
  onMinutesChange: (minutes: number) => void
  onRoleChange?: (role: Role, defaultMinutes: number) => void
  onRemove?: () => void
  stat?: number | null
  compact?: boolean
}

const ROLE_CYCLE: Role[] = ['sub_planned', 'sub_tactical', 'reserve']
const ROLE_MINUTES: Record<Role, number> = {
  starter: 85, sub_planned: 30, sub_tactical: 12, reserve: 0,
}
const ROLE_LABEL: Record<Role, string> = {
  starter: '85\'', sub_planned: 'Ent.', sub_tactical: 'Tac.', reserve: 'Rés.',
}
const ROLE_COLOR: Record<Role, string> = {
  starter:      'text-gray-400',
  sub_planned:  'text-blue-400',
  sub_tactical: 'text-yellow-500',
  reserve:      'text-gray-600',
}

export function JerseyCard({
  playerName,
  shirtNumber,
  expectedMinutes,
  isSelected,
  role,
  onClick,
  onMinutesChange,
  onRoleChange,
  onRemove,
  stat,
  compact = false,
}: JerseyCardProps) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(String(expectedMinutes))
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => { setDraft(String(expectedMinutes)) }, [expectedMinutes])
  useEffect(() => { if (editing) inputRef.current?.focus() }, [editing])

  function commitEdit() {
    const val = parseInt(draft, 10)
    if (!isNaN(val) && val >= 0 && val <= 120) {
      onMinutesChange(val)
    } else {
      setDraft(String(expectedMinutes))
    }
    setEditing(false)
  }

  function cycleRole(e: React.MouseEvent) {
    e.stopPropagation()
    if (!onRoleChange) return
    const idx = ROLE_CYCLE.indexOf(role as typeof ROLE_CYCLE[number])
    const next = ROLE_CYCLE[(idx + 1) % ROLE_CYCLE.length]
    onRoleChange(next, ROLE_MINUTES[next])
  }

  const displayName = playerName.split(' ').pop() ?? playerName

  return (
    <div
      onClick={onClick}
      className={clsx(
        'group flex flex-col items-center cursor-pointer select-none transition-transform',
        isSelected && 'scale-105',
        compact ? 'w-14' : 'w-16',
      )}
    >
      {/* Jersey shape */}
      <div
        className={clsx(
          'relative flex items-center justify-center rounded-md border-2 transition-colors',
          compact ? 'w-11 h-12' : 'w-13 h-14',
          isSelected
            ? 'border-orange-400 bg-orange-500/20'
            : 'border-gray-600 bg-gray-800 hover:border-gray-400',
        )}
      >
        <span className={clsx('font-bold text-white', compact ? 'text-lg' : 'text-xl')}>
          {shirtNumber ?? '?'}
        </span>
        {onRemove && (
          <button
            onClick={(e) => { e.stopPropagation(); onRemove() }}
            className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full bg-gray-700 border border-gray-600 text-gray-400 hover:bg-red-500/80 hover:border-red-400 hover:text-white flex items-center justify-center text-[10px] leading-none transition-colors opacity-0 group-hover:opacity-100"
          >
            ×
          </button>
        )}
      </div>

      {/* Player name */}
      <span className="mt-0.5 text-[10px] text-gray-300 text-center leading-tight truncate w-full text-center">
        {displayName}
      </span>

      {/* Bottom row: stat / role badge (for subs) / minutes */}
      {stat !== null && stat !== undefined ? (
        <span className="mt-0.5 text-[11px] font-bold text-orange-300">{stat.toFixed(2)}</span>
      ) : compact && onRoleChange ? (
        // Sub bench: role badge (clickable to cycle) + minutes
        <div className="mt-0.5 flex flex-col items-center gap-0">
          <button
            onClick={cycleRole}
            title={`Rôle : ${role} — cliquer pour changer`}
            className={clsx('text-[10px] font-medium leading-none hover:brightness-125 transition-all', ROLE_COLOR[role])}
          >
            {ROLE_LABEL[role]}
          </button>
          <div
            className="flex items-center gap-0.5"
            onClick={(e) => { e.stopPropagation(); setEditing(true) }}
          >
            {editing ? (
              <input
                ref={inputRef}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onBlur={commitEdit}
                onKeyDown={(e) => { if (e.key === 'Enter') commitEdit() }}
                className="w-10 text-[11px] text-center bg-gray-700 border border-orange-400 rounded px-1 py-0 text-white"
              />
            ) : (
              <span className="text-[10px] text-gray-500 hover:text-white cursor-text">
                {expectedMinutes}&apos;
              </span>
            )}
          </div>
        </div>
      ) : (
        <div
          className="mt-0.5 flex items-center gap-0.5"
          onClick={(e) => { e.stopPropagation(); setEditing(true) }}
        >
          {editing ? (
            <input
              ref={inputRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={commitEdit}
              onKeyDown={(e) => { if (e.key === 'Enter') commitEdit() }}
              className="w-10 text-[11px] text-center bg-gray-700 border border-orange-400 rounded px-1 py-0 text-white"
            />
          ) : (
            <span className="text-[11px] text-gray-400 hover:text-white cursor-text">
              {expectedMinutes}&apos;
            </span>
          )}
        </div>
      )}
    </div>
  )
}
