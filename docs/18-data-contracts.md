# Contrats d'Interface de Données (Data Contracts)

## 1. Modèle de Pricing (Output) - Version 1.1

**Schéma : `PricingOutput`**

```json
{
  "meta": {
    "version": "1.1",
    "calculated_at": "2026-01-30T14:00:00Z",
    "phase": "LINEUP", // EARLY ou LINEUP
    "is_override_active": true
  },
  "fixture": {
    "id": "2026-01-30_PSG_Lyon",
    "home": "Paris Saint-Germain",
    "away": "Olympique Lyonnais"
  },
  "selection": {
    "player_id": "psg_mbappe_kylian",
    "player_name": "Kylian Mbappé",
    "market": "anytime_goalscorer",
    "status": {
      "is_starter_projected": true,
      "is_starter_confirmed": true, // Nouveau: Validé via War Room
      "projected_minutes": 90
    }
  },
  "pricing": {
    "model_probability": 0.485,
    "fair_odds": 2.06,
    "inputs_used": {
      "team_xg_override": 2.5, // Nouveau: Valeur forcée par l'opérateur
      "lambda_open_play": 0.61,
      "lambda_penalty": 0.12
    }
  },
  "decision": {
    "status": "RECOMMENDED",
    "edge": 0.12,
    "suggested_stake": 1.0, // Unités (Ajusté selon la phase)
    "bookmaker": "betclic",
    "odds": 2.30
  }
}
```
