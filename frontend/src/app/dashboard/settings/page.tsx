'use client'

import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Save, Key, Bell, Sliders, Database, Loader2 } from 'lucide-react'
import { getSettings, saveSettings } from '@/lib/api'

const DEFAULTS: Record<string, string> = {
  odds_api_key: '',
  fbref_user_agent: 'Ev0-Bot/1.0',
  min_edge: '5',
  min_confidence: '60',
  min_odds: '1.5',
  max_odds: '10',
  stake_method: 'flat',
  flat_stake: '10',
  max_per_match: '50',
  max_per_day: '200',
  notify_value_pick: 'true',
  notify_daily_report: 'true',
  notify_data_quality: 'false',
  alert_email: '',
  sync_fixtures_freq: 'daily',
  sync_odds_freq: '1h',
  retention: '1y',
  active_leagues: 'both',
}

export default function SettingsPage() {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<Record<string, string>>(DEFAULTS)
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saved' | 'error'>('idle')

  const { data: savedSettings, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
  })

  // Populate form with saved settings when loaded
  useEffect(() => {
    if (savedSettings) {
      setForm((prev) => ({ ...prev, ...savedSettings }))
    }
  }, [savedSettings])

  const mutation = useMutation({
    mutationFn: () => saveSettings(form),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] })
      setSaveStatus('saved')
      setTimeout(() => setSaveStatus('idle'), 2000)
    },
    onError: () => {
      setSaveStatus('error')
      setTimeout(() => setSaveStatus('idle'), 3000)
    },
  })

  const set = (key: string, value: string) => {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  const handleSave = () => {
    mutation.mutate()
  }

  if (isLoading) {
    return (
      <div className="p-4 md:p-8 max-w-4xl flex items-center justify-center min-h-[50vh]">
        <Loader2 className="w-8 h-8 text-brand-400 animate-spin" />
      </div>
    )
  }

  return (
    <div className="p-4 md:p-8 max-w-4xl">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-white">Parametres</h1>
        <p className="text-gray-400 mt-1">Configuration du systeme Ev0</p>
      </div>

      <div className="space-y-6">
        {/* API Keys */}
        <SettingsSection
          icon={Key}
          title="Cles API"
          description="Configuration des acces aux services externes"
        >
          <div className="space-y-4">
            <InputField
              label="The Odds API Key"
              type="password"
              placeholder="Votre cle API"
              value={form.odds_api_key}
              onChange={(v) => set('odds_api_key', v)}
              helper="https://the-odds-api.com pour obtenir une cle"
            />
            <InputField
              label="FBref User Agent"
              type="text"
              value={form.fbref_user_agent}
              onChange={(v) => set('fbref_user_agent', v)}
              helper="Identifiant pour les requetes FBref"
            />
          </div>
        </SettingsSection>

        {/* Strategy Settings */}
        <SettingsSection
          icon={Sliders}
          title="Strategie"
          description="Parametres de selection et de mise"
        >
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <InputField
              label="Edge minimum"
              type="number"
              value={form.min_edge}
              onChange={(v) => set('min_edge', v)}
              suffix="%"
            />
            <InputField
              label="Confidence minimum"
              type="number"
              value={form.min_confidence}
              onChange={(v) => set('min_confidence', v)}
              suffix="%"
            />
            <InputField
              label="Cote minimum"
              type="number"
              value={form.min_odds}
              onChange={(v) => set('min_odds', v)}
            />
            <InputField
              label="Cote maximum"
              type="number"
              value={form.max_odds}
              onChange={(v) => set('max_odds', v)}
            />
            <SelectField
              label="Methode de mise"
              value={form.stake_method}
              onChange={(v) => set('stake_method', v)}
              options={[
                { value: 'flat', label: 'Flat (fixe)' },
                { value: 'kelly25', label: 'Kelly 25%' },
                { value: 'kelly10', label: 'Kelly 10%' },
              ]}
            />
            <InputField
              label="Mise flat"
              type="number"
              value={form.flat_stake}
              onChange={(v) => set('flat_stake', v)}
              suffix="EUR"
            />
            <InputField
              label="Max par match"
              type="number"
              value={form.max_per_match}
              onChange={(v) => set('max_per_match', v)}
              suffix="EUR"
            />
            <InputField
              label="Max par jour"
              type="number"
              value={form.max_per_day}
              onChange={(v) => set('max_per_day', v)}
              suffix="EUR"
            />
          </div>
        </SettingsSection>

        {/* Notifications */}
        <SettingsSection
          icon={Bell}
          title="Notifications"
          description="Alertes et rapports automatiques"
        >
          <div className="space-y-3">
            <ToggleField
              label="Alerte nouveau pick VALUE"
              description="Notification quand un pick avec edge > 10% est detecte"
              checked={form.notify_value_pick === 'true'}
              onChange={(v) => set('notify_value_pick', v ? 'true' : 'false')}
            />
            <ToggleField
              label="Rapport quotidien"
              description="Resume des performances envoye chaque soir"
              checked={form.notify_daily_report === 'true'}
              onChange={(v) => set('notify_daily_report', v ? 'true' : 'false')}
            />
            <ToggleField
              label="Alerte qualite donnees"
              description="Notification si une source de donnees est en erreur"
              checked={form.notify_data_quality === 'true'}
              onChange={(v) => set('notify_data_quality', v ? 'true' : 'false')}
            />
            <InputField
              label="Email pour les alertes"
              type="email"
              placeholder="votre@email.com"
              value={form.alert_email}
              onChange={(v) => set('alert_email', v)}
            />
          </div>
        </SettingsSection>

        {/* Data Settings */}
        <SettingsSection
          icon={Database}
          title="Donnees"
          description="Parametres d'ingestion et de stockage"
        >
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <SelectField
              label="Frequence sync fixtures"
              value={form.sync_fixtures_freq}
              onChange={(v) => set('sync_fixtures_freq', v)}
              options={[
                { value: 'daily', label: 'Quotidien (06:00)' },
                { value: 'twice', label: '2x par jour' },
                { value: 'manual', label: 'Manuel uniquement' },
              ]}
            />
            <SelectField
              label="Frequence sync odds"
              value={form.sync_odds_freq}
              onChange={(v) => set('sync_odds_freq', v)}
              options={[
                { value: '15min', label: 'Toutes les 15 min' },
                { value: '30min', label: 'Toutes les 30 min' },
                { value: '1h', label: 'Toutes les heures' },
              ]}
            />
            <SelectField
              label="Retention historique"
              value={form.retention}
              onChange={(v) => set('retention', v)}
              options={[
                { value: '6m', label: '6 mois' },
                { value: '1y', label: '1 an' },
                { value: '2y', label: '2 ans' },
              ]}
            />
            <SelectField
              label="Ligues actives"
              value={form.active_leagues}
              onChange={(v) => set('active_leagues', v)}
              options={[
                { value: 'both', label: 'Ligue 1 + Premier League' },
                { value: 'ligue1', label: 'Ligue 1 uniquement' },
                { value: 'pl', label: 'Premier League uniquement' },
              ]}
            />
          </div>
        </SettingsSection>

        {/* Save Button */}
        <div className="flex items-center justify-end gap-3 pt-4">
          {saveStatus === 'error' && (
            <span className="text-sm text-red-400">Erreur lors de la sauvegarde</span>
          )}
          <button
            onClick={handleSave}
            disabled={mutation.isPending}
            className="flex items-center gap-2 px-6 py-2 bg-brand-600 hover:bg-brand-700 disabled:opacity-50 text-white rounded-lg transition-colors"
          >
            {mutation.isPending ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Save className="w-4 h-4" />
            )}
            {saveStatus === 'saved' ? 'Sauvegarde !' : 'Sauvegarder'}
          </button>
        </div>
      </div>
    </div>
  )
}

function SettingsSection({
  icon: Icon,
  title,
  description,
  children,
}: {
  icon: any
  title: string
  description: string
  children: React.ReactNode
}) {
  return (
    <div className="bg-gray-800 rounded-xl p-6">
      <div className="flex items-center gap-3 mb-4">
        <div className="p-2 bg-gray-700 rounded-lg">
          <Icon className="w-5 h-5 text-brand-400" />
        </div>
        <div>
          <h2 className="font-semibold text-white">{title}</h2>
          <p className="text-sm text-gray-400">{description}</p>
        </div>
      </div>
      {children}
    </div>
  )
}

function InputField({
  label,
  type,
  placeholder,
  value,
  onChange,
  suffix,
  helper,
}: {
  label: string
  type: string
  placeholder?: string
  value: string
  onChange: (v: string) => void
  suffix?: string
  helper?: string
}) {
  return (
    <div>
      <label className="block text-sm text-gray-300 mb-1">{label}</label>
      <div className="relative">
        <input
          type={type}
          placeholder={placeholder}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-brand-500"
        />
        {suffix && (
          <span className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400">
            {suffix}
          </span>
        )}
      </div>
      {helper && <p className="text-xs text-gray-500 mt-1">{helper}</p>}
    </div>
  )
}

function SelectField({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  options: { value: string; label: string }[]
}) {
  return (
    <div>
      <label className="block text-sm text-gray-300 mb-1">{label}</label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-brand-500"
      >
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>{opt.label}</option>
        ))}
      </select>
    </div>
  )
}

function ToggleField({
  label,
  description,
  checked,
  onChange,
}: {
  label: string
  description: string
  checked: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <div className="flex items-center justify-between py-2">
      <div>
        <p className="text-sm text-white">{label}</p>
        <p className="text-xs text-gray-500">{description}</p>
      </div>
      <button
        onClick={() => onChange(!checked)}
        className={`relative w-11 h-6 rounded-full transition-colors ${
          checked ? 'bg-brand-600' : 'bg-gray-600'
        }`}
      >
        <span
          className={`absolute top-1 left-1 w-4 h-4 bg-white rounded-full transition-transform ${
            checked ? 'translate-x-5' : 'translate-x-0'
          }`}
        />
      </button>
    </div>
  )
}
