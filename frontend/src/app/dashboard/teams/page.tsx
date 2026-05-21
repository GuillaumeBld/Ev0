'use client'

import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Search, Check, X, Pencil } from 'lucide-react'
import { clsx } from 'clsx'

interface CanonicalTeam {
  id: number
  name_fr: string
  name_en: string | null
  api_football_id: number | null
  aliases: string[]
}

async function fetchTeams(): Promise<CanonicalTeam[]> {
  const res = await fetch('/api/v1/canonical-teams')
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

async function patchTeam(id: number, body: Partial<Pick<CanonicalTeam, 'name_fr' | 'name_en' | 'api_football_id' | 'aliases'>>) {
  const res = await fetch(`/api/v1/canonical-teams/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

function TeamLogo({ id, name }: { id: number | null; name: string }) {
  const [failed, setFailed] = useState(false)
  if (!id || failed) {
    const initials = name.split(/[\s\-]+/).filter(Boolean).map(w => w[0]).join('').toUpperCase().slice(0, 2)
    return (
      <div className="w-8 h-8 rounded-full bg-gray-700 border border-gray-600 flex items-center justify-center text-xs font-bold text-gray-400 shrink-0">
        {initials}
      </div>
    )
  }
  return (
    <img
      src={`https://media.api-sports.io/football/teams/${id}.png`}
      alt={name}
      className="w-8 h-8 object-contain shrink-0"
      onError={() => setFailed(true)}
    />
  )
}

function InlineEdit({
  value,
  onSave,
  type = 'text',
  placeholder,
}: {
  value: string | number | null
  onSave: (v: string) => void
  type?: 'text' | 'number'
  placeholder?: string
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(String(value ?? ''))

  if (!editing) {
    return (
      <button
        onClick={() => { setDraft(String(value ?? '')); setEditing(true) }}
        className="group flex items-center gap-1 text-left hover:text-white transition-colors"
      >
        <span className={clsx(value == null || value === '' ? 'text-gray-600 italic' : 'text-gray-300')}>
          {value ?? placeholder ?? '—'}
        </span>
        <Pencil className="w-3 h-3 text-gray-600 opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
      </button>
    )
  }

  return (
    <div className="flex items-center gap-1">
      <input
        autoFocus
        type={type}
        value={draft}
        onChange={e => setDraft(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter') { onSave(draft); setEditing(false) }
          if (e.key === 'Escape') setEditing(false)
        }}
        className="w-full bg-gray-700 border border-brand-500 rounded px-2 py-0.5 text-white text-sm focus:outline-none"
      />
      <button onClick={() => { onSave(draft); setEditing(false) }} className="text-green-400 hover:text-green-300">
        <Check className="w-4 h-4" />
      </button>
      <button onClick={() => setEditing(false)} className="text-gray-500 hover:text-gray-300">
        <X className="w-4 h-4" />
      </button>
    </div>
  )
}

export default function TeamsPage() {
  const qc = useQueryClient()
  const { data: teams = [], isLoading, isError } = useQuery({ queryKey: ['canonical-teams'], queryFn: fetchTeams })

  const mutation = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Parameters<typeof patchTeam>[1] }) => patchTeam(id, body),
    onSuccess: (updated: CanonicalTeam) => {
      qc.setQueryData(['canonical-teams'], (prev: CanonicalTeam[] | undefined) =>
        prev?.map(t => t.id === updated.id ? updated : t) ?? [updated]
      )
    },
  })

  const [search, setSearch] = useState('')
  const [filterMissing, setFilterMissing] = useState(false)

  const displayed = useMemo(() => {
    let list = [...teams]
    if (filterMissing) list = list.filter(t => !t.api_football_id)
    if (search.trim()) {
      const q = search.toLowerCase()
      list = list.filter(t =>
        t.name_fr.toLowerCase().includes(q) ||
        (t.name_en ?? '').toLowerCase().includes(q) ||
        t.aliases.some(a => a.toLowerCase().includes(q))
      )
    }
    return list
  }, [teams, search, filterMissing])

  const missingCount = teams.filter(t => !t.api_football_id).length

  return (
    <div className="p-4 md:p-8">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-white">Équipes canoniques</h1>
        <p className="text-gray-400 mt-1 text-sm">
          {teams.length} équipes · source de vérité pour noms, logos et IDs
        </p>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-5 items-center">
        <div className="relative flex-1 min-w-48 max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            placeholder="Rechercher…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full pl-9 pr-4 py-2 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
          />
        </div>
        <button
          onClick={() => setFilterMissing(v => !v)}
          className={clsx(
            'px-3 py-2 rounded-lg text-sm font-medium border transition-colors',
            filterMissing
              ? 'bg-red-500/20 border-red-500/40 text-red-300'
              : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-500'
          )}
        >
          Sans logo ({missingCount})
        </button>
        <span className="text-xs text-gray-500">
          Cliquer sur une cellule pour modifier · Entrée pour valider · Échap pour annuler
        </span>
      </div>

      {isLoading && <p className="text-gray-500 py-10 text-center">Chargement…</p>}
      {isError && <p className="text-red-400 py-10 text-center">Erreur de chargement</p>}

      {!isLoading && !isError && (
        <div className="bg-gray-800 rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-700 text-gray-400 text-xs uppercase tracking-wide">
                  <th className="px-4 py-3 text-left w-10">#</th>
                  <th className="px-3 py-3 text-left w-10">Logo</th>
                  <th className="px-3 py-3 text-left">Nom FR</th>
                  <th className="px-3 py-3 text-left">Nom EN</th>
                  <th className="px-3 py-3 text-left w-32">API-Football ID</th>
                  <th className="px-3 py-3 text-left">Aliases</th>
                </tr>
              </thead>
              <tbody>
                {displayed.map(team => (
                  <tr key={team.id} className="border-b border-gray-700/50 hover:bg-gray-700/30 transition-colors">
                    <td className="px-4 py-3 text-gray-600 tabular-nums">{team.id}</td>
                    <td className="px-3 py-3">
                      <TeamLogo id={team.api_football_id} name={team.name_fr} />
                    </td>
                    <td className="px-3 py-3 font-medium text-white">
                      <InlineEdit
                        value={team.name_fr}
                        placeholder="Nom FR"
                        onSave={v => mutation.mutate({ id: team.id, body: { name_fr: v } })}
                      />
                    </td>
                    <td className="px-3 py-3">
                      <InlineEdit
                        value={team.name_en}
                        placeholder="Nom EN"
                        onSave={v => mutation.mutate({ id: team.id, body: { name_en: v } })}
                      />
                    </td>
                    <td className="px-3 py-3">
                      <InlineEdit
                        value={team.api_football_id}
                        placeholder="ID"
                        type="number"
                        onSave={v => mutation.mutate({ id: team.id, body: { api_football_id: v ? Number(v) : undefined } })}
                      />
                    </td>
                    <td className="px-3 py-3 text-gray-500 text-xs max-w-xs">
                      <div className="flex flex-wrap gap-1">
                        {team.aliases.slice(0, 5).map(a => (
                          <span key={a} className="px-1.5 py-0.5 bg-gray-700 rounded text-gray-400">{a}</span>
                        ))}
                        {team.aliases.length > 5 && (
                          <span className="text-gray-600">+{team.aliases.length - 5}</span>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
                {displayed.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-4 py-10 text-center text-gray-600 italic">
                      Aucune équipe trouvée
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="px-4 py-3 border-t border-gray-700 text-xs text-gray-500 text-right">
            {displayed.length} / {teams.length} équipes
          </div>
        </div>
      )}
    </div>
  )
}
