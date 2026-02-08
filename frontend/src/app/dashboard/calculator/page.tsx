'use client'

import { useState, useEffect } from 'react'
import { Calculator, AlertCircle, Check } from 'lucide-react'
import { clsx } from 'clsx'

interface PricingResult {
  lambda: number
  probability: number
  fairOdds: number
  edge: number | null
  classification: 'VALUE' | 'NO_VALUE' | 'AVOID' | null
}

export default function CalculatorPage() {
  const [marketType, setMarketType] = useState<'goalscorer' | 'assist'>('goalscorer')
  
  // Goalscorer inputs
  const [xgPer90, setXgPer90] = useState(0.5)
  const [expectedMinutes, setExpectedMinutes] = useState(75)
  const [conversionRate, setConversionRate] = useState(1.0)
  const [opponentFactor, setOpponentFactor] = useState(1.0)
  const [formFactor, setFormFactor] = useState(1.0)
  
  // Assist inputs
  const [xaPer90, setXaPer90] = useState(0.2)
  const [creationScore, setCreationScore] = useState(1.0)
  const [teammateFinishing, setTeammateFinishing] = useState(1.0)
  
  // Market odds for comparison
  const [marketOdds, setMarketOdds] = useState<number | null>(null)
  
  // Results
  const [result, setResult] = useState<PricingResult | null>(null)

  // Calculate on input change
  useEffect(() => {
    let lambda: number
    
    if (marketType === 'goalscorer') {
      lambda = (xgPer90 * (expectedMinutes / 90)) * conversionRate * opponentFactor * formFactor
    } else {
      lambda = (xaPer90 * (expectedMinutes / 90)) * creationScore * teammateFinishing * opponentFactor * formFactor
    }
    
    lambda = Math.max(0.001, Math.min(lambda, 3.0))
    const probability = 1 - Math.exp(-lambda)
    const fairOdds = 1 / probability
    
    let edge: number | null = null
    let classification: PricingResult['classification'] = null
    
    if (marketOdds && marketOdds > 1) {
      edge = (marketOdds / fairOdds) - 1
      if (edge >= 0.10) classification = 'VALUE'
      else if (edge >= 0.05) classification = 'VALUE'
      else if (edge >= 0) classification = 'NO_VALUE'
      else classification = 'AVOID'
    }
    
    setResult({
      lambda: Number(lambda.toFixed(4)),
      probability: Number(probability.toFixed(4)),
      fairOdds: Number(fairOdds.toFixed(2)),
      edge,
      classification,
    })
  }, [marketType, xgPer90, expectedMinutes, conversionRate, opponentFactor, formFactor, xaPer90, creationScore, teammateFinishing, marketOdds])

  return (
    <div className="p-8 max-w-4xl">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-white flex items-center gap-2">
          <Calculator className="w-6 h-6" />
          Calculateur de Pricing
        </h1>
        <p className="text-gray-400 mt-1">
          Calcule la probabilité et cote fair value via le modèle Poisson
        </p>
      </div>

      {/* Market Type Toggle */}
      <div className="flex gap-2 mb-6">
        <button
          onClick={() => setMarketType('goalscorer')}
          className={clsx(
            'flex-1 py-3 rounded-lg font-medium transition-colors',
            marketType === 'goalscorer'
              ? 'bg-orange-500 text-white'
              : 'bg-gray-800 text-gray-400 hover:text-white'
          )}
        >
          🎯 Buteur
        </button>
        <button
          onClick={() => setMarketType('assist')}
          className={clsx(
            'flex-1 py-3 rounded-lg font-medium transition-colors',
            marketType === 'assist'
              ? 'bg-blue-500 text-white'
              : 'bg-gray-800 text-gray-400 hover:text-white'
          )}
        >
          🅰️ Passeur
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Inputs */}
        <div className="bg-gray-800 rounded-xl p-6">
          <h2 className="text-lg font-semibold text-white mb-4">Paramètres</h2>
          
          <div className="space-y-4">
            {marketType === 'goalscorer' ? (
              <>
                <SliderInput
                  label="xG/90"
                  value={xgPer90}
                  onChange={setXgPer90}
                  min={0}
                  max={1.5}
                  step={0.01}
                  helper="Expected Goals par 90 minutes"
                />
                <SliderInput
                  label="Minutes attendues"
                  value={expectedMinutes}
                  onChange={setExpectedMinutes}
                  min={0}
                  max={90}
                  step={5}
                  helper="Minutes que le joueur va jouer"
                />
                <SliderInput
                  label="Conversion rate"
                  value={conversionRate}
                  onChange={setConversionRate}
                  min={0.5}
                  max={1.5}
                  step={0.05}
                  helper="Buts réels / xG (1.0 = moyenne)"
                />
              </>
            ) : (
              <>
                <SliderInput
                  label="xA/90"
                  value={xaPer90}
                  onChange={setXaPer90}
                  min={0}
                  max={0.8}
                  step={0.01}
                  helper="Expected Assists par 90 minutes"
                />
                <SliderInput
                  label="Minutes attendues"
                  value={expectedMinutes}
                  onChange={setExpectedMinutes}
                  min={0}
                  max={90}
                  step={5}
                  helper="Minutes que le joueur va jouer"
                />
                <SliderInput
                  label="Score de création"
                  value={creationScore}
                  onChange={setCreationScore}
                  min={0.5}
                  max={2.0}
                  step={0.1}
                  helper="Qualité créative vs moyenne ligue"
                />
                <SliderInput
                  label="Finishing coéquipiers"
                  value={teammateFinishing}
                  onChange={setTeammateFinishing}
                  min={0.7}
                  max={1.3}
                  step={0.05}
                  helper="Qualité de finition de équipe"
                />
              </>
            )}

            {/* Common factors */}
            <div className="border-t border-gray-700 pt-4">
              <SliderInput
                label="Facteur adversaire"
                value={opponentFactor}
                onChange={setOpponentFactor}
                min={0.7}
                max={1.4}
                step={0.05}
                helper="Plus de 1 = défense faible"
              />
              <SliderInput
                label="Facteur forme"
                value={formFactor}
                onChange={setFormFactor}
                min={0.7}
                max={1.3}
                step={0.05}
                helper="Forme récente du joueur"
              />
            </div>

            {/* Market Odds */}
            <div className="border-t border-gray-700 pt-4">
              <label className="block text-sm text-gray-400 mb-2">
                Cote bookmaker (optionnel)
              </label>
              <input
                type="number"
                step="0.05"
                placeholder="Ex: 2.50"
                value={marketOdds || ''}
                onChange={(e) => setMarketOdds(e.target.value ? Number(e.target.value) : null)}
                className="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-3 text-white text-lg"
              />
              <p className="text-xs text-gray-500 mt-1">
                Entre la cote du bookmaker pour calculer edge
              </p>
            </div>
          </div>
        </div>

        {/* Results */}
        <div className="space-y-4">
          {/* Main Result Card */}
          <div className="bg-gray-800 rounded-xl p-6">
            <h2 className="text-lg font-semibold text-white mb-4">Résultat</h2>
            
            {result && (
              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <ResultBox
                    label="Lambda (λ)"
                    value={result.lambda.toString()}
                    helper="Intensité Poisson"
                  />
                  <ResultBox
                    label="Probabilité"
                    value={`${(result.probability * 100).toFixed(1)}%`}
                    helper="P(événement >= 1)"
                  />
                </div>

                <div className="bg-gray-700/50 rounded-xl p-4 text-center">
                  <p className="text-sm text-gray-400 mb-1">Cote Fair Value</p>
                  <p className="text-4xl font-bold text-white">
                    {result.fairOdds.toFixed(2)}
                  </p>
                </div>

                {/* Edge comparison */}
                {result.edge !== null && (
                  <div className={clsx(
                    'rounded-xl p-4',
                    result.classification === 'VALUE' ? 'bg-green-500/10 border border-green-500/30' :
                    result.classification === 'AVOID' ? 'bg-red-500/10 border border-red-500/30' :
                    'bg-gray-700/50'
                  )}>
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm text-gray-400">Edge vs marché</p>
                        <p className={clsx(
                          'text-2xl font-bold',
                          result.edge >= 0.10 ? 'text-green-400' :
                          result.edge >= 0.05 ? 'text-green-400' :
                          result.edge >= 0 ? 'text-gray-400' :
                          'text-red-400'
                        )}>
                          {result.edge >= 0 ? '+' : ''}{(result.edge * 100).toFixed(1)}%
                        </p>
                      </div>
                      <div className="text-right">
                        <span className={clsx(
                          'px-3 py-1.5 rounded-lg font-medium',
                          result.classification === 'VALUE' ? 'bg-green-500 text-white' :
                          result.classification === 'AVOID' ? 'bg-red-500 text-white' :
                          'bg-gray-600 text-gray-300'
                        )}>
                          {result.classification === 'VALUE' && <Check className="w-4 h-4 inline mr-1" />}
                          {result.classification === 'AVOID' && <AlertCircle className="w-4 h-4 inline mr-1" />}
                          {result.classification}
                        </span>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Formula Explanation */}
          <div className="bg-gray-800 rounded-xl p-6">
            <h3 className="text-sm font-medium text-gray-400 mb-3">Formule utilisée</h3>
            <div className="bg-gray-900 rounded-lg p-4 font-mono text-sm text-gray-300">
              {marketType === 'goalscorer' ? (
                <>
                  <p>λ = xG/90 × (mins/90) × conversion × opponent × form</p>
                  <p className="text-gray-500 mt-2">P(but) = 1 - e^(-λ)</p>
                </>
              ) : (
                <>
                  <p>λ = xA/90 × (mins/90) × creation × teammate × opponent × form</p>
                  <p className="text-gray-500 mt-2">P(assist) = 1 - e^(-λ)</p>
                </>
              )}
              <p className="text-gray-500 mt-2">Fair Odds = 1 / P</p>
              <p className="text-gray-500">Edge = (Market Odds / Fair Odds) - 1</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function SliderInput({
  label,
  value,
  onChange,
  min,
  max,
  step,
  helper,
}: {
  label: string
  value: number
  onChange: (v: number) => void
  min: number
  max: number
  step: number
  helper?: string
}) {
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <label className="text-sm text-gray-400">{label}</label>
        <span className="text-sm font-medium text-white">{value}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
      />
      {helper && <p className="text-xs text-gray-500 mt-1">{helper}</p>}
    </div>
  )
}

function ResultBox({
  label,
  value,
  helper,
}: {
  label: string
  value: string
  helper?: string
}) {
  return (
    <div className="bg-gray-700/50 rounded-lg p-3">
      <p className="text-xs text-gray-400">{label}</p>
      <p className="text-xl font-bold text-white">{value}</p>
      {helper && <p className="text-xs text-gray-500">{helper}</p>}
    </div>
  )
}
