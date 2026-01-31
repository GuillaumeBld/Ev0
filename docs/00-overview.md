# Overview

## Objectif
Construire un moteur qui calcule la "vraie" probabilité qu'un joueur marque (buteur) ou fasse une passe décisive (passeur) lors d'un match de foot, puis compare cette probabilité (convertie en cote "fair" EV0) aux cotes des bookmakers français pour identifier les paris à valeur positive (+EV).

## Deux modules distincts

### 🎯 Module Buteur (Anytime Goalscorer)
Approche simplifiée basée sur :
- xG (expected goals)
- Minutes jouées
- Ratio xG/90 minutes
- Poste du joueur
- Forme récente (5 derniers matchs)

### 🅰️ Module Passeur (Anytime Assist)
Approche enrichie avec statistiques avancées :
- xA (expected assists)
- Occasions créées (chances created)
- Centres (crosses)
- Passes dans la surface adverse (passes into penalty area)
- Passes amenant à un tir (key passes)
- Forme récente (5 derniers matchs)

## Bookmakers cibles
- 🇫🇷 Betclic
- 🇫🇷 Parions Sport en Ligne (FDJ)
- 🇫🇷 Unibet France

## Ligues couvertes
- 🇫🇷 Ligue 1
- 🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League

## Sources de données
| Priorité | Source | Usage |
|----------|--------|-------|
| 🥇 Primaire | FBref | Stats complètes xG, xA, passes, centres, etc. |
| 🥈 Secondaire | Understat | xG/xA détaillé par tir |
| 🥉 Tertiaire | FotMob | Données match, lineups |

## Contraintes clés
- Incertitude sur les compositions et minutes à anticiper
- Les cotes player props ont souvent plus de marge que les marchés principaux
- Données doivent être fiables, stables, et légalement accessibles
- Backtesting doit éviter le leakage et être reproductible

## Définition de "done"
- Base de données avec snapshots historiques features + cotes
- Service de pricing produisant des fair prices pour chaque fixture
- Backtest harness produisant :
  - Courbes de calibration et Brier score
  - P&L stratégie avec intervalles de confiance
- Dashboard expliquant et auditant chaque recommandation
- Période de paper trading avec métriques stables avant stakes réels

## Non-objectifs
- Profit garanti (les marchés s'adaptent)
- Parier sans validation suffisante
- ML lourd avant validation du baseline
