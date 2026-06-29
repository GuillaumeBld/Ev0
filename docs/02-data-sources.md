# Sources de données

## Stats joueurs — Bzzoiro API

Source principale pour toutes les statistiques joueurs et fixtures. Clé : `BZZOIRO_API_KEY`.

| Données | Endpoint / table destination |
|---------|------------------------------|
| Fixtures (matchs) | `bzz_events` |
| Stats joueurs par match | `bzz_player_match_stats` |
| Stats joueurs par saison | `bzz_player_season_stats` |
| Joueurs | `bzz_players` |
| Équipes | `bzz_teams` |
| Ligues | `bzz_leagues` |
| Compos prédites | `bzz_predictions` |

**Ligues couvertes** : Ligue 1, Premier League, Bundesliga, Serie A, La Liga, Ligue des Champions, WC2026 (`league_api_id=27`).

---

## Cotes bookmakers

### OddsPortal (scraping HTML)

xG de marché implicite via les cotes WDW (1X2). Source primaire pour `MarketXgService`.
Tables : `odds_snapshots`, `match_odds_snapshots`, `oddsportal_poll_state`.

### Betclic (scraping gRPC)

Cotes buteur/passeur joueur. Marché FR principal.
Table : `player_odds_snapshots`.

### Unibet (scraping LVS)

Cotes buteur/passeur joueur. Marché FR secondaire.
Table : `player_odds_snapshots` (même table, champ `bookmaker`).

### Bzzoiro (xG live)

Bzzoiro fournit aussi des xG live en cours de match via ses cotes. Activé quand disponibles et récents (`xg_source = "bzzoiro"`).

---

## WC2026 — Sources spécifiques

### Bzzoiro (scores et stats)

Résultats et stats joueurs WC2026 via `league_api_id=27`. Même pipeline que les ligues championnat.

### Sofascore (events de buts — import local)

Bzzoiro et FotMob retournent 403 depuis le VPS pour les détails de match.
Solution : fetch Sofascore localement et import via script dans le container.
- Ligue 1 : `tournament_id=34`, `season_id=77356`
- Premier League : `tournament_id=17`, `season_id=76986`
- Incidents endpoint : `https://api.sofascore.com/api/v1/event/{event_id}/incidents`

### API-Football (ID mapping)

Utilisé ponctuellement pour mapper les IDs d'équipes (`fix_api_football_ids.py`).

---

## Priorité et fallbacks xG

```
1. Override manuel (opérateur)      → xg_source = "override"
2. Bzzoiro live xG                  → xg_source = "bzzoiro"
3. Market-implied (OddsPortal)      → xg_source = "market_implied"
4. Market-implied flagged (ancien)  → xg_source = "market_implied_flagged"
```

Si aucune source n'est disponible, le calcul échoue avec une erreur explicite (pas de fallback silencieux).

---

## Données WC2026 outrights (TEAM_BM)

Cotes bookmaker pour l'avancement au tournoi (vainqueur, top 2, top 4, top 8) stockées dans `wc2026_outright_odds`. Utilisées comme :
1. Initialisation des ELO (via `_elo_from_team_bm`)
2. Fallback de `compute_expected_games()` si la table `wc2026_team_advancement` est vide
