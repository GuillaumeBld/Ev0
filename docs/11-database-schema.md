# Schéma de Base de Données (PostgreSQL)

Ce schéma est la source de vérité pour l'implémentation. Il distingue strictement les données brutes (Passé) des features calculées (Futur).

## 1. Référentiel (Static Data)

### `leagues`
*   `id` (PK, String): Code ligue (ex: 'FRA-L1', 'ENG-PL')
*   `name` (String): Nom complet
*   `country` (String): Pays

### `teams`
*   `id` (PK, String): Slug unique (ex: 'psg', 'arsenal')
*   `name` (String): Nom complet
*   `league_id` (FK): Ligue actuelle

### `players`
*   `id` (PK, String): Slug canonique `team_lastname_firstname`
*   `name` (String): Nom complet normalisé
*   `team_id` (FK): Équipe actuelle
*   `is_penalty_taker` (Bool): Flag manuel ou déduit
*   `created_at`, `updated_at`: Timestamps UTC

---

## 2. Ingestion Brute (The Truth)

Ces tables stockent ce qui s'est *réellement* passé. Elles servent à reconstruire les snapshots.

### `fixtures` (Calendrier)
*   `id` (PK, String): `YYYY-MM-DD_Home_Away`
*   `date_utc` (DateTime): Coup d'envoi officiel
*   `home_team_id` (FK)
*   `away_team_id` (FK)
*   `status` (String): 'SCHEDULED', 'FINISHED', 'POSTPONED'

### `player_match_stats` (Boxscore)
Une ligne par joueur par match terminé.
*   `id` (PK, BigInt)
*   `fixture_id` (FK)
*   `player_id` (FK)
*   `minutes` (Int): Temps de jeu réel
*   `goals` (Int): Buts réels
*   `assists` (Int): Passes D réelles
*   `npxg` (Float): **Non-Penalty xG** (Source FBref)
*   `xa` (Float): Expected Assists
*   `shots` (Int)
*   `key_passes` (Int)
*   `sca` (Int)

---

## 3. Pricing Features (The View)

Ces tables stockent l'état de la connaissance à un instant T. C'est l'input du modèle.

### `player_stats_snapshots` (Rolling View)
Généré quotidiennement ou avant chaque journée.
*   `id` (PK, BigInt)
*   `player_id` (FK)
*   `as_of_utc` (DateTime): Date de validité du snapshot
*   `npxg_per_90` (Float): Moyenne rolling (ex: 15 derniers matchs)
*   `xa_per_90` (Float)
*   `conversion_rate` (Float): Ratio Buts/npxG historique
*   `form_factor` (Float): Score de forme pondéré
*   `season_stats` (JSON): Détail complet pour debug

### `odds_snapshots` (Market View)
*   `id` (PK, BigInt)
*   `fixture_id` (FK)
*   `bookmaker` (String): 'betclic', 'unibet', 'pinnacle'
*   `market_name` (String): 'anytime_goalscorer', 'anytime_assist'
*   `odds_data` (JSON): Map `{ "player_canonical_id": decimal_odd }`
*   `ingested_at` (DateTime)

---

## 4. Sortie Modèle & Décision

### `model_outputs`
*   `id` (PK, BigInt)
*   `fixture_id` (FK)
*   `player_id` (FK)
*   `market` (String)
*   `phase` (String): 'EARLY' or 'LINEUP'
*   `team_xg_override` (Float): Null if auto
*   `model_prob` (Float)
*   `fair_odds` (Float)

### `decisions` (Trading Log)
*   `id` (PK, BigInt)
*   `model_output_id` (FK)
*   `bookmaker` (String)
*   `market_odds` (Float)
*   `edge` (Float)
*   `stake` (Float): Mise conseillée (Unités)
*   `status` (String): 'RECOMMENDED', 'REJECTED', 'EXECUTED'
*   `rejection_reason` (String): Null si recommandé

---

## Indexing Strategy
*   `fixtures`: Index sur `date_utc` (Recherche range).
*   `player_match_stats`: Composite `(player_id, fixture_id)` (Unicité).
*   `player_stats_snapshots`: Composite `(player_id, as_of_utc)` (Time travel).