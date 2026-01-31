# UI and UX specification

## Primary user
A single operator who:
- Reviews recommendations and explanations
- Approves paper trades and later live trades
- Monitors model health and drift

## Information architecture
Navigation:
- Dashboard
- **Lineup War Room** (New! ⚡️)
- Fixtures
- Recommendations
- Backtest
- Settings

## Core screens

### ⚡️ Lineup War Room (Le Menu Dynamique)
Cette vue est optimisée pour la réactivité à H-1.

**État "En Attente" :**
*   Liste des matchs qui débutent dans < 90 min.
*   Statut : "Waiting for Lineups".
*   Bouton d'action : "Saisir Compo" ou "Refresh Source".

**État "Compo Confirmée" :**
*   Affichage : 11 Titulaires (Home & Away).
*   **Impact Immédiat** : Tableau des Deltas de Value.
    *   *Exemple :* "Player X (Titulaire Surprise) -> Edge +12%".
    *   *Exemple :* "Player Y (Remplaçant Surprise) -> NO BET (Annulé)".
*   **Action Rapide** : Bouton "Approve Bet" à côté de chaque opportunité flash.

### Dashboard (Vue Macro)
- Today and next 48h fixtures
- Recommendation summary by league
- CLV trend (7d, 30d)
- Data health widget

### Fixtures
- Filter by league, date
- For each fixture:
  - Team expected goals (Override input here)
  - Key players priced
  - Odds snapshots availability

### Recommendations
- Table:
  - Player, market, EV0, market odds, edge, confidence
  - Start probability, expected minutes
  - Timestamp and source
- Click row for detail explanation:
  - Inputs used, opponent multipliers, minutes scenario weights
  - Market margin removal details

### Backtest
- Filters by season, league, market
- Charts:
  - CLV distribution
  - Reliability curve
  - Edge bucket performance

## UX principles
- **Speed first** in War Room (minimal clics).
- Explainability by default.
- Separate "Predicted" (Early) from "Confirmed" (Live) clearly via distinct badges.

## Accessibility
- Keyboard navigation (Shortcuts for Approve/Reject).
- Clear status indicators with text labels.