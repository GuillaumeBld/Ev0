# StatsHub — Gap-Fill Stats Design Spec
**Date:** 2026-04-28
**Status:** Implemented

---

## 1. Objectif

Bzzoiro est la source primaire de statistiques joueurs d'Ev0. En pratique, Bzzoiro produit des
**trous** : certains joueurs ont des matchs sans stats (row absente) ou des stats partiellement
nulles (xG, passes clés, tirs, etc.). Ces trous se propagent jusqu'au calculateur, qui affiche
des joueurs manquants ou des probabilités dégradées.

**StatsHub** est un agrégateur public (SofaScore-based) qui couvre les mêmes ligues avec un
ensemble riche de métriques par match. Il partage exactement le même espace d'IDs que Bzzoiro
(SofaScore IDs), ce qui rend le couplage trivial.

**Résultat attendu :**
- `bzz_player_match_stats` : NULLs comblés par les valeurs StatsHub
- `bzz_player_season_stats` : agrégats recalculés après chaque gap-fill → pricing engine plus précis
- Calculateur : 100% de couverture joueurs pour toutes les équipes des 6 ligues cibles

**Principe cardinal :** Bzzoiro reste source de vérité. StatsHub ne **remplace** jamais une valeur
existante — il comble uniquement les NULLs (`COALESCE`).

---

## 2. Espace d'IDs — Mapping Bzzoiro ↔ StatsHub

StatsHub et Bzzoiro partagent les IDs SofaScore. Aucune table de correspondance n'est nécessaire.

| Concept         | Table Bzzoiro          | Champ          | Rôle dans StatsHub  |
|-----------------|------------------------|----------------|---------------------|
| Match           | `bzz_events`           | `api_id`       | `eventId`           |
| Équipe          | `bzz_teams`            | `api_id`       | `team_id`           |
| Joueur (intern) | `bzz_players`          | `internal_id`  | `player.internalId` |
| Ligue           | `bzz_leagues`          | `api_id`       | `tournamentId`      |
| Saison          | `bzz_leagues`          | `season_id`    | `seasonId`          |

---

## 3. API StatsHub utilisée

**Base URL :** `https://www.statshub.com`
**Auth :** Aucune (API publique)
**Rate limit estimé :** ~1 req/s (pas de limite documentée, throttling observé à haute fréquence)

### Endpoint principal

```
GET /api/team/{team_id}/players/performance
    ?tournamentId={tournament_id}
    &seasonId={season_id}
```

**Réponse (structure) :**
```json
{
  "data": [
    {
      "id": 3306,
      "internalId": 101963,
      "slug": "karim-benzema",
      "name": "Karim Benzema",
      "position": "F",
      "stats": {
        "14771855": {
          "playerId": 3306,
          "eventId": 14771855,
          "teamId": 21895,
          "goals": 2,
          "goalAssist": 0,
          "onTargetScoringAttempt": 2,
          "shots": 3,
          "expectedGoals": "1.4025",
          "expectedAssists": "0.1072",
          "keyPass": 2,
          "totalCross": 1,
          "accurateCross": 0,
          "touches": 42,
          "rating": "8.30",
          "aerialWon": 0,
          "fouls": 0,
          "wasFouled": 0,
          "dispossessed": 1,
          "interceptionWon": 0,
          "minutesPlayed": 85,
          "position": "ST"
        }
      }
    }
  ],
  "events": [...],
  "currentFixturePlayerStats": [...]
}
```

**Un appel = tous les joueurs d'une équipe pour toute la saison.**

---

## 4. Mapping des champs

| Champ StatsHub            | Colonne `bzz_player_match_stats` | Type   |
|---------------------------|----------------------------------|--------|
| `onTargetScoringAttempt`  | `shots_on_target`                | int    |
| `shots`                   | `total_shots`                    | int    |
| `expectedGoals`           | `expected_goals`                 | float  |
| `expectedAssists`         | `expected_assists`               | float  |
| `keyPass`                 | `key_pass`                       | int    |
| `touches`                 | `touches`                        | int    |
| `rating`                  | `rating`                         | float  |
| `aerialWon`               | `aerial_won`                     | int    |
| `goalAssist`              | `goal_assist`                    | int    |
| `goals`                   | `goals`                          | int    |
| `fouls`                   | `fouls`                          | int    |
| `wasFouled`               | `was_fouled`                     | int    |
| `totalCross`              | `total_cross`                    | int    |
| `accurateCross`           | `accurate_cross`                 | int    |
| `dispossessed`            | `dispossessed`                   | int    |
| `interceptionWon`         | `interception`                   | int    |

Après insertion des champs bruts, `compute_derived_metrics()` (module `bzzoiro/sync_player_stats`)
recalcule automatiquement :
`xg_per_shot`, `shot_accuracy`, `finishing_delta`, `xa_delta`, `cross_accuracy`, etc.

**Champs non fournis par StatsHub** (restent NULL si absents de Bzzoiro) :
`total_pass`, `accurate_pass`, `total_long_balls`, `accurate_long_balls`, `duel_won`,
`duel_lost`, `total_tackle`, `won_tackle`, `total_clearance`, `ball_recovery`,
`yellow_card`, `red_card`, `minutes_played`, `is_home`, `saves`, `goals_conceded`.

---

## 5. Stratégie COALESCE

```
INSERT INTO bzz_player_match_stats (player_api_id, event_api_id, team_api_id, shots_on_target, ...)
VALUES (...)
ON CONFLICT (player_api_id, event_api_id) DO UPDATE SET
    shots_on_target = COALESCE(bzz_player_match_stats.shots_on_target, EXCLUDED.shots_on_target),
    expected_goals  = COALESCE(bzz_player_match_stats.expected_goals,  EXCLUDED.expected_goals),
    ...
```

Comportements :
| Cas                            | Résultat                          |
|--------------------------------|-----------------------------------|
| Row Bzzoiro absente            | Nouvelle row créée depuis StatsHub |
| Row Bzzoiro présente, NULL     | Colonne mise à jour par StatsHub  |
| Row Bzzoiro présente, non NULL | Valeur Bzzoiro conservée          |

---

## 6. Modes de synchronisation

### 6.1 Mode incrémental (daily, 08:15 UTC)

```
job_sync_statshub_gap_fill()
  → sync_statshub_gap_fill(session, days_ahead=14)
```

**Périmètre :** équipes avec un match dans les 14 prochains jours.
**Logique :** pour chaque `(team_api_id, league_api_id, season_id)` issu des `BzzEvent`
à venir, un appel StatsHub est effectué. Seuls les joueurs reconnus via `BzzPlayer.internal_id`
sont traités — les autres sont ignorés (log `DEBUG`).

Enchaînement : `job_sync_statshub_gap_fill` → `job_aggregate_season_stats` (recalcul immédiat
des `bzz_player_season_stats` pour que le pricing engine voit les données fraîches).

### 6.2 Mode full-season (weekly, lundi 03:00 UTC)

```
job_sync_statshub_full_season()
  → sync_statshub_gap_fill(session, full_season=True)
```

**Périmètre :** TOUTES les équipes apparaissant dans au moins un `BzzEvent` des 6 ligues cibles
(y compris les équipes sans match dans les 14 prochains jours — trêve internationale, etc.).
Suivi de `job_aggregate_season_stats`.

---

## 7. Rate limiting

- Pause de 1 seconde toutes les 3 équipes (`if i % 3 == 2: await asyncio.sleep(1.0)`)
- Retry exponentiel sur HTTP 429 / 502 / 503 / 504 : 4 tentatives, délais 3s → 6s → 12s → 24s
- Timeout par requête : 30s

---

## 8. Chaîne de données complète

```
StatsHub /api/team/{id}/players/performance
    │
    ▼ COALESCE upsert
bzz_player_match_stats
    │
    ▼ job_aggregate_season_stats
bzz_player_season_stats
  ├─ form_xg_5, form_assists_5   → blend 60/40 modèle top-down
  ├─ shots_on_target, key_pass   → finishing_mult, creation_mult_v2
  └─ expected_goals, expected_assists → shares buteur/passeur
    │
    ▼
recommendation_service → Calculateur → Recommandations
```

---

## 9. Fichiers implémentés

| Fichier | Rôle |
|---------|------|
| `backend/app/ingestion/statshub/__init__.py` | Module docstring |
| `backend/app/ingestion/statshub/client.py` | Client HTTP async (retry, timeout) |
| `backend/app/ingestion/statshub/sync.py` | Gap-fill COALESCE + deux modes sync |
| `backend/app/worker.py` | `job_sync_statshub_gap_fill` + `job_sync_statshub_full_season` |

---

## 10. Limitations connues

- **Joueurs non reconnus** : si `BzzPlayer.internal_id` est NULL (joueur non encore synced depuis
  Bzzoiro), la row StatsHub est ignorée. Résolution : s'assurer que `job_sync_bzzoiro_players`
  tourne avant le gap-fill (ordre garanti par les horaires — 06:05 UTC vs 08:15 UTC).
- **Saison non résolue** : si `BzzLeague.season_id` est NULL pour une ligue, toute l'équipe est
  sautée avec un log `WARNING`. Résolution : vérifier `bzz_leagues` en DB.
- **Champs absents de StatsHub** : `minutes_played`, `is_home`, cartons — non fournis par
  l'endpoint `team/players/performance`. Ces données restent sous responsabilité de Bzzoiro.
