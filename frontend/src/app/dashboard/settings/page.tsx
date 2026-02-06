'use client'

import { useState } from 'react'
import { Save, Key, Bell, Sliders, Database } from 'lucide-react'

export default function SettingsPage() {
  const [saved, setSaved] = useState(false)

  const handleSave = () => {
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  return (
    <div className="p-8 max-w-4xl">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-white">Paramètres</h1>
        <p className="text-gray-400 mt-1">Configuration du système Ev0</p>
      </div>

      <div className="space-y-6">
        {/* API Keys */}
        <SettingsSection
          icon={Key}
          title="Clés API"
          description="Configuration des accès aux services externes"
        >
          <div className="space-y-4">
            <InputField
              label="The Odds API Key"
              type="password"
              placeholder="••••••••••••••••"
              helper="https://the-odds-api.com pour obtenir une clé"
            />
            <InputField
              label="FBref User Agent"
              type="text"
              defaultValue="Ev0-Bot/1.0"
              helper="Identifiant pour les requêtes FBref"
            />
          </div>
        </SettingsSection>

        {/* Strategy Settings */}
        <SettingsSection
          icon={Sliders}
          title="Stratégie"
          description="Paramètres de sélection et de mise"
        >
          <div className="grid grid-cols-2 gap-4">
            <InputField
              label="Edge minimum"
              type="number"
              defaultValue="5"
              suffix="%"
            />
            <InputField
              label="Confidence minimum"
              type="number"
              defaultValue="60"
              suffix="%"
            />
            <InputField
              label="Cote minimum"
              type="number"
              defaultValue="1.5"
            />
            <InputField
              label="Cote maximum"
              type="number"
              defaultValue="10"
            />
            <SelectField
              label="Méthode de mise"
              options={[
                { value: 'flat', label: 'Flat (fixe)' },
                { value: 'kelly25', label: 'Kelly 25%' },
                { value: 'kelly10', label: 'Kelly 10%' },
              ]}
            />
            <InputField
              label="Mise flat"
              type="number"
              defaultValue="10"
              suffix="€"
            />
            <InputField
              label="Max par match"
              type="number"
              defaultValue="50"
              suffix="€"
            />
            <InputField
              label="Max par jour"
              type="number"
              defaultValue="200"
              suffix="€"
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
              description="Notification quand un pick avec edge > 10% est détecté"
              defaultChecked={true}
            />
            <ToggleField
              label="Rapport quotidien"
              description="Résumé des performances envoyé chaque soir"
              defaultChecked={true}
            />
            <ToggleField
              label="Alerte qualité données"
              description="Notification si une source de données est en erreur"
              defaultChecked={false}
            />
            <InputField
              label="Email pour les alertes"
              type="email"
              placeholder="votre@email.com"
            />
          </div>
        </SettingsSection>

        {/* Data Settings */}
        <SettingsSection
          icon={Database}
          title="Données"
          description="Paramètres d'ingestion et de stockage"
        >
          <div className="grid grid-cols-2 gap-4">
            <SelectField
              label="Fréquence sync fixtures"
              options={[
                { value: 'daily', label: 'Quotidien (06:00)' },
                { value: 'twice', label: '2x par jour' },
                { value: 'manual', label: 'Manuel uniquement' },
              ]}
            />
            <SelectField
              label="Fréquence sync odds"
              options={[
                { value: '15min', label: 'Toutes les 15 min' },
                { value: '30min', label: 'Toutes les 30 min' },
                { value: '1h', label: 'Toutes les heures' },
              ]}
            />
            <SelectField
              label="Rétention historique"
              options={[
                { value: '6m', label: '6 mois' },
                { value: '1y', label: '1 an' },
                { value: '2y', label: '2 ans' },
              ]}
            />
            <SelectField
              label="Ligues actives"
              options={[
                { value: 'both', label: 'Ligue 1 + Premier League' },
                { value: 'ligue1', label: 'Ligue 1 uniquement' },
                { value: 'pl', label: 'Premier League uniquement' },
              ]}
            />
          </div>
        </SettingsSection>

        {/* Save Button */}
        <div className="flex justify-end pt-4">
          <button
            onClick={handleSave}
            className="flex items-center gap-2 px-6 py-2 bg-brand-600 hover:bg-brand-700 text-white rounded-lg transition-colors"
          >
            <Save className="w-4 h-4" />
            {saved ? 'Sauvegardé !' : 'Sauvegarder'}
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
  children 
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
  defaultValue,
  suffix,
  helper,
}: { 
  label: string
  type: string
  placeholder?: string
  defaultValue?: string
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
          defaultValue={defaultValue}
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
  options,
}: { 
  label: string
  options: { value: string; label: string }[]
}) {
  return (
    <div>
      <label className="block text-sm text-gray-300 mb-1">{label}</label>
      <select className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-brand-500">
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
  defaultChecked,
}: { 
  label: string
  description: string
  defaultChecked?: boolean
}) {
  const [checked, setChecked] = useState(defaultChecked || false)

  return (
    <div className="flex items-center justify-between py-2">
      <div>
        <p className="text-sm text-white">{label}</p>
        <p className="text-xs text-gray-500">{description}</p>
      </div>
      <button
        onClick={() => setChecked(!checked)}
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
