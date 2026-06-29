# Schéma de base de données

## Bzzoiro — données brutes

### `bzz_leagues`
Ligues suivies. `league_api_id` est la clé Bzzoiro.

### `bzz_teams`
Équipes. Lien vers `bzz_leagues`.

### `bzz_players`
Joueurs. Lien vers `bzz_teams`. Contient `name`, `position`, `nationality`, `bzz_id`.

### `bzz_events`
Fixtures (matchs). Clé primaire `bzz_id`.
Champs clés : `home_team`, `away_team`, `event_date`, `status`, `home_score`, `away_score`, `home_xg`, `away_xg`, `round_number`, `league_api_id`, `period`.

### `bzz_player_match_stats`
Stats joueur par match terminé. Lien `bzz_event_id` + `bzz_player_id`.
Champs : `goals`, `assists`, `minutes`, `rating`, `shots`, `shots_on_target`, `xg`, `xa`, `key_passes`.

### `bzz_player_season_stats`
Stats agrégées par joueur par saison. Source `"average"` (agregation Bzzoiro) utilisée pour le pricing.
Champs : `npxg_per_90`, `xa_per_90`, `minutes`, `goals`, `assists`, `rating`, `source`.

### `bzz_predictions`
Compos prédites par Bzzoiro avant chaque match.

---

## Fixtures et lignes

### `fixtures`
Calendrier des matchs (championnats). Source Bzzoiro + mapping interne.
Champs : `id`, `home_team`, `away_team`, `kickoff_utc`, `status`, `league`, `home_score`, `away_score`.

### `match_events`
Événements de match (buts, assists). Source Sofascore (import local).

### `player_match_minutes`
Minutes jouées par joueur par match, pour le calcul des expected_minutes.

---

## Cotes

### `player_odds_snapshots`
Cotes joueur par bookmaker (Betclic, Unibet). Marché : `goal` | `assist` | `supersub`.
Champs : `fixture_id`, `player_name`, `market`, `bookmaker`, `odds`, `scraped_at`.

### `match_odds_snapshots`
Cotes 1X2 par match (OddsPortal). Utilisées pour le xG de marché.

### `odds_snapshots`
Archive des snapshots de cotes brutes.

### `odds_scrape_state`
État du scraper : dernier succès, prochaine tentative, par bookmaker + fixture.

### `oddsportal_poll_state`
État du polling OddsPortal par fixture.

---

## Pricing et recommandations

### `team_xg_estimates`
Estimations xG d'équipe par fixture, avec source.

### `recommendations`
Paris recommandés par le modèle.
Champs : `fixture_id`, `player_id`, `market_type` (`goal`|`assist`|`supersub`), `fair_odds`, `book_odds`, `edge`, `status` (`pending`|`approved`|`placed`|`won`|`lost`|`void`).

### `bankroll_entries`
Historique des mises et résultats (pour le suivi ROI).

### `autopilot_decisions`
Décisions de l'autopilot (approuver / passer / miser fort) par recommandation.

---

## Lignes et compos

### `team_lineups` / `team_lineup_players`
Compos confirmées ou prédites pour les matchs de championnat.

---

## WC 2026

### `wc2026_squad_players`
Joueurs sélectionnés par nation pour le WC2026.

### `wc2026_expected_lineups` / `wc2026_expected_lineup_players`
Compos attendues pour les matchs WC2026.

### `wc2026_outright_odds`
Cotes bookmaker pour l'avancement au tournoi (vainqueur, top 2/4/8, groupes) par nation.

### `wc2026_team_advancement`
Résultat du Monte Carlo ELO — probabilités d'avancement par nation.
Champs : `nation`, `elo`, `p_r32`, `p_r16`, `p_qf`, `p_sf`, `p_finalist`, `p_winner`, `e_games`, `n_sim`, `computed_at`.
TRUNCATE + INSERT à chaque run bracket.

### `wc2026_player_pricing`
Cotes fair outright CDM par joueur (buteur / passeur).
Champs : `nation`, `player_name`, `position`, `lambda_goal`, `lambda_assist`, `p_top_scorer`, `fair_odds_top_scorer`, `p_top_assister`, `fair_odds_top_assister`, `computed_at`.
TRUNCATE + INSERT après chaque bracket run.

---

## Système

### `app_config`
Configuration applicative clé-valeur.

### `user_settings`
Paramètres utilisateur (seuils, préférences d'autopilot).

### `canonical_teams`
Mapping de normalisation des noms d'équipes entre sources.
