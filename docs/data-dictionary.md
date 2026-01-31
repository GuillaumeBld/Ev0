# Dictionnaire de Données (Data Dictionary)

Ce document fait autorité sur la définition exacte, la source et le traitement des données utilisées dans le moteur Ev0.

## 1. Métriques de Jeu (Gameplay Metrics)

### 🎯 Métriques Buteur

| Variable | Définition Exacte | Source | Unité | Null Handling |
|----------|-------------------|--------|-------|---------------|
| `npxG` | **Non-Penalty Expected Goals**. Probabilité cumulée qu'un tir résulte en but, *excluant* les pénaltys. | FBref (StatsBomb) | Float [0.0 - ∞] | **REJET**. Si npxG manquant, pas de pricing. |
| `Goals` | Buts marqués validés par l'arbitre. Inclut CSC ? **NON**. Inclut Pénaltys ? **OUI**. | FBref | Entier | 0 |
| `Minutes` | Temps de jeu effectif. Inclut le temps additionnel ? **OUI** (selon source). | FBref | Entier | 0 |
| `Penalty Taker` | Joueur désigné pour tirer les pénaltys si présents sur le terrain. | Config Manuelle + Historique | Booléen | False (Prudence) |

### 🅰️ Métriques Passeur

| Variable | Définition Exacte | Source | Unité | Null Handling |
|----------|-------------------|--------|-------|---------------|
| `xA` | **Expected Assists**. Probabilité qu'une passe devienne décisive, indépendamment de la finition. | FBref | Float [0.0 - ∞] | **REJET**. |
| `Key Pass` | Dernière passe avant un tir. (Aussi appelé "Chance Created"). | FBref | Entier | 0 |
| `SCA` | **Shot-Creating Action**. Les 2 actions offensives (passe, dribble, faute) menant directement à un tir. | FBref | Entier | 0 |
| `PPA` | **Passes into Penalty Area**. Passes réussies vers la surface (hors coups de pied arrêtés). | FBref | Entier | 0 |

---

## 2. Métriques de Marché (Market Metrics)

| Variable | Définition Exacte | Règle de Calcul |
|----------|-------------------|-----------------|
| `Implied Probability` | Probabilité implicite d'une cote brute. | `1 / Decimal_Odds` |
| `Margin (Vig)` | Pourcentage de profit théorique du bookmaker. | `(Sum(1/Odds) - 1)` sur l'ensemble du marché. |
| `Fair Odds (EV0)` | Cote sans marge calculée par le modèle. | `1 / Model_Probability` |
| `Closing Line` | Cote disponible chez le bookmaker de référence (Pinnacle) **au moment exact** du coup d'envoi. | Snapshot à T-1min |

---

## 3. Règles de Normalisation

### Identifiants Joueurs
*   **Format** : `[League]_[Team]_[Lastname]_[Firstname]` (slugified)
*   **Règle d'Unicité** : En cas d'homonyme dans la même équipe, ajouter `_dob` (Date of Birth).
*   **Traitement des Accents** : ASCII folding strict (`Mbappé` -> `mbappe`).

### Identifiants Matchs (Fixtures)
*   **Format** : `YYYY-MM-DD_[HomeTeam]_[AwayTeam]`
*   **Timezone** : Toujours **UTC**.
*   **Cas Report** : Si reporté > 48h, création d'un nouvel ID de fixture. L'ancien passe en status `POSTPONED`.

---

## 4. Statuts de Données

| Statut | Signification | Action Système |
|--------|---------------|----------------|
| `RAW` | Donnée brute ingestion. Peut contenir des doublons. | Stockage S3/Raw Table |
| `CANONICAL` | Donnée nettoyée, ID unifié, doublons fusionnés. | Stockage DB "Core" |
| `MISSING` | Donnée attendue mais absente. | Alerte Opérateur (Bloquant pour Pricing) |
| `ESTIMATED` | Donnée manquante remplacée par une moyenne (Imputation). | **INTERDIT** pour les features critiques (xG, xA). Autorisé pour données secondaires (Météo). |
