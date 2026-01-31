# Roadmap et todo list

## Phase 0 : Scaffolding du repo
- [ ] Créer structure repo Ev0 (backend, frontend, infra, docs)
- [ ] Docker-compose pour Postgres et backend
- [ ] CI : lint, tests, type checks (GitHub Actions)
- [ ] Pre-commit hooks (ruff, black, mypy)

## Phase 1 : Pipeline de données

### 1.1 Fixtures et métadonnées
- [ ] Ingestion calendrier Ligue 1 (FBref)
- [ ] Ingestion calendrier Premier League (FBref)
- [ ] Mapping stable fixture_id
- [ ] Stockage avec timestamps UTC

### 1.2 Stats joueurs - Module Buteur 🎯
- [ ] Scraping FBref : xG par joueur
- [ ] Scraping FBref : minutes par joueur
- [ ] Calcul xG/90
- [ ] Calcul conversion rate (goals/npxG) rolling 15 matchs
- [ ] Historique 5 derniers matchs
- [ ] Ajustement adversaire (xGA)
- [ ] Stockage avec as_of_utc

### 1.3 Stats joueurs - Module Passeur 🅰️
- [ ] Scraping FBref : xA par joueur
- [ ] Scraping FBref : Key Passes
- [ ] Scraping FBref : Shot-Creating Actions (SCA)
- [ ] Scraping FBref : Centres (Crosses attempted/completed)
- [ ] Scraping FBref : Passes into Penalty Area
- [ ] Scraping FBref : Progressive Passes
- [ ] Calcul score de création composite
- [ ] Historique 5 derniers matchs
- [ ] Stockage avec as_of_utc

### 1.4 Données contextuelles
- [ ] Stats équipe : xG for/against
- [ ] Qualité finition coéquipiers (team goals/xG)
- [ ] Home/Away splits
- [ ] Lineups prédits (FotMob ou autre)
- [ ] Lineups confirmés

### 1.5 Cotes bookmakers
- [ ] Intégration The Odds API (ou scraping)
- [ ] Cotes Betclic - Buteur à tout moment
- [ ] Cotes Parions Sport - Buteur à tout moment
- [ ] Cotes Unibet - Buteur à tout moment
- [ ] Cotes assists (si disponibles)
- [ ] Mapping joueur ↔ sélection marché
- [ ] Stockage avec timestamps

## Phase 2 : Moteur de pricing

### 2.1 Module Buteur 🎯
- [ ] Calcul lambda de base (xG_per_90 * expected_mins/90)
- [ ] Ajustement conversion rate individuel
- [ ] Ajustement adversaire (xGA factor)
- [ ] Decay exponentiel forme récente (λ=0.025)
- [ ] Calcul P(score >= 1) via Poisson
- [ ] Génération fair odds EV0
- [ ] Payload d'explication

### 2.2 Module Passeur 🅰️
- [ ] Score de création composite (6 variables pondérées)
- [ ] Normalisation par moyenne ligue
- [ ] Ajustement qualité coéquipiers (teammate finishing)
- [ ] Ajustement défense adverse
- [ ] Decay exponentiel forme récente (λ=0.017)
- [ ] Calcul P(assist >= 1) via Poisson
- [ ] Génération fair odds EV0
- [ ] Payload d'explication

### 2.3 Comparaison et Edge
- [ ] Retrait de marge proportionnel
- [ ] Calcul edge vs chaque bookmaker
- [ ] Identification meilleure cote
- [ ] Classification (VALUE / NO_VALUE / AVOID)

## Phase 3 : Framework de backtest
- [ ] Walk-forward validation setup
- [ ] Snapshots historiques features
- [ ] Snapshots historiques cotes
- [ ] Métriques : Brier score, calibration plot
- [ ] Métriques : ROI, P&L, drawdown
- [ ] Bootstrap intervalles de confiance
- [ ] Analyse par bucket d'edge
- [ ] Tests de régression sur dataset sample
- [ ] Rapport automatique

## Phase 4 : Stratégie de sélection
- [ ] Filtres minimum (edge, odds range, confidence)
- [ ] Filtres spécifiques Buteur
- [ ] Filtres spécifiques Passeur
- [ ] Stake sizing : flat (baseline)
- [ ] Stake sizing : Kelly fractionnel (optionnel)
- [ ] Gestion exposition par match/jour/ligue
- [ ] Détection corrélation entre paris

## Phase 5 : Calibration
- [ ] Split train/validation/test temporel
- [ ] Isotonic regression (si >1000 samples)
- [ ] Platt scaling (si <1000 samples)
- [ ] Évaluation Brier score
- [ ] Calibration plots par module
- [ ] Re-calibration périodique

## Phase 6 : Dashboard UI
- [ ] Setup Next.js + Tailwind
- [ ] Page : Recommandations du jour
- [ ] Page : Détail prédiction (drawer)
- [ ] Page : Rapport backtest
- [ ] Page : Santé des données
- [ ] Filtres par ligue, module (Buteur/Passeur)
- [ ] Historique des recommandations

## Phase 7 : Paper trading
- [ ] Log automatique des recommandations
- [ ] Interface décision opérateur (approve/reject/notes)
- [ ] Tracking résultats réels
- [ ] Rapport hebdo : calibration, P&L simulé
- [ ] Alertes si métriques dérivent

## Phase 8 : Exécution live (optionnel)
- [ ] Module flat stakes
- [ ] Caps d'exposition
- [ ] Règles de stop (drawdown max)
- [ ] Monitoring temps réel
- [ ] Alerting (Telegram/email)

---

## Priorités immédiates

### Semaine 1
1. ✅ Alignement scope et documentation
2. 🔲 Scaffold repo Ev0
3. 🔲 Docker-compose + CI

### Semaine 2
4. 🔲 Ingestion FBref (xG, xA, key passes, etc.)
5. 🔲 Prototype Module Buteur
6. 🔲 Tests unitaires pricing

### Semaine 3
7. 🔲 Prototype Module Passeur
8. 🔲 Ingestion cotes (The Odds API ou scraping)
9. 🔲 Pipeline edge calculation

### Semaine 4
10. 🔲 Setup backtest framework
11. 🔲 Premier backtest end-to-end
12. 🔲 Évaluation et ajustements

---

## Métriques de succès (Phase backtest)

| Métrique | Seuil minimum | Cible |
|----------|---------------|-------|
| Brier Score (Buteur) | < 0.22 | < 0.20 |
| Brier Score (Passeur) | < 0.26 | < 0.23 |
| ROI backtest | > 0% | > 5% |
| IC inférieur ROI 95% | > -5% | > 0% |
| Sample size | > 500 | > 1000 |
| Calibration R² | > 0.80 | > 0.90 |
