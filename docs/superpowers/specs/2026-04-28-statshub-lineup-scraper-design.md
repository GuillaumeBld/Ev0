# StatsHub — Scraper de Compositions Officielles Design Spec
**Date:** 2026-04-28
**Status:** Implemented

---

## 1. Objectif

La section **Compos** du dashboard affiche actuellement uniquement des compositions saisies
manuellement (`lineup_type = "probable_manual"`). Lorsqu'aucune compo n'a été saisie, le système
bascule sur la dernière compo officielle connue d'un match précédent (`last_known`).

**Problème :** les recommandations et le calculateur ne peuvent pas filtrer les joueurs remplaçants
tant que la compo officielle n'est pas saisie. Le pricing est dilué sur 30+ joueurs alors qu'il
devrait se concentrer sur les 11 titulaires confirmés.

**StatsHub** publie les compositions officielles jusqu'à **1h avant le coup d'envoi** (parfois
davantage). L'API expose un endpoint de poll bulk (`lineup-status`) qui permet de détecter le
moment exact où une compo devient disponible, puis un endpoint de performances par équipe
(`team/players/performance`) qui contient le champ `currentFixturePlayerStats` avec les titulaires.

**Résultat attendu :**
- `team_lineups` alimenté automatiquement avec `lineup_type="official"`, `source="statshub"`,
  sans aucune saisie manuelle
- Le resolver (`lineup_resolver.py`) sélectionne automatiquement l'`official` en priorité
- Calculateur et recommandations n'opèrent plus que sur les titulaires confirmés
- La saisie manuelle (`probable_manual`) reste disponible en fallback tant que la compo
  officielle n'est pas disponible

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│               job_poll_statshub_lineups                      │
│          (toutes les 15min entre J-2h et J-10min)            │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
         GET /api/event/lineup-status
             ?ids={bzz_event_api_ids}
                      │
          ┌───────────┴────────────┐
          │ "confirmed" / "predicted"│
          │ (déclenche le fetch)    │
          └───────────┬────────────┘
                      │
         ┌────────────┴─────────────┐
         │                          │
         ▼                          ▼
GET /api/team/{home_api_id}/   GET /api/team/{away_api_id}/
players/performance             players/performance
?tournamentId=...               ?tournamentId=...
?seasonId=...                   ?seasonId=...
         │                          │
         └────────────┬─────────────┘
                      │
                      ▼
          currentFixturePlayerStats
          (starters du prochain match)
                      │
                      ▼
         UPSERT → team_lineups (official)
                  team_lineup_players
                      │
                      ▼
         lineup_resolver → official (priorité 0)
                      │
              ┌───────┴───────┐
              ▼               ▼
        Calculateur    Recommandations
    (xG redistribué   (joueurs banc
     sur titulaires)   filtrés)
```

---

## 3. Endpoints StatsHub utilisés

### 3.1 Poll bulk lineup-status

```
GET /api/event/lineup-status?ids={event_id_1},{event_id_2},...
```

**Réponse :**
```json
{
  "data": {
    "15342062": "none",
    "14023940": "confirmed",
    "14167984": "predicted"
  }
}
```

**Valeurs possibles :**
| Valeur        | Signification                                              |
|---------------|------------------------------------------------------------|
| `"none"`      | Pas de compo disponible (plus de 1–2h avant KO en général)|
| `"predicted"` | Compo prédite par algo StatsHub (non officielle)           |
| `"confirmed"` | Compo officielle publiée par le club / la fédération       |

**Usage :** appelé en batch sur tous les event IDs des matchs dans les 3h suivantes.
Un seul appel pour N matchs (pas de limite sur le nombre d'IDs testée à > 20 IDs).

### 3.2 Fetch composition par équipe

```
GET /api/team/{team_api_id}/players/performance
    ?tournamentId={league_api_id}
    &seasonId={season_id}
```

**Champ `currentFixturePlayerStats`** — tableau des joueurs pour le prochain match dont la
compo est confirmée :

```json
{
  "currentFixturePlayerStats": [
    {
      "playerId": 3306,
      "internalId": 101963,
      "name": "Karim Benzema",
      "position": "ST",
      "jerseyNumber": 9,
      "isHomeTeam": true,
      "eventId": 15342062
    },
    ...
  ]
}
```

> ⚠️ **Risque :** Le comportement exact de `currentFixturePlayerStats` n'a pas encore été
> vérifié sur un match Big5 avec compo confirmée. Le champ est vide pour les matchs terminés.
> À valider sur le premier match target-league avec `lineupConfirmed=true`.
> Fallback prévu (§ 5.3).

---

## 4. Espace d'IDs — Pont Fixture ↔ StatsHub

Même espace d'IDs SofaScore que Bzzoiro. Le pont est immédiat :

```
Fixture.external_id = "bzz_14023940"
                              │
                              ▼
                   BzzEvent.api_id = 14023940
                              │
               ┌──────────────┴──────────────┐
               ▼                              ▼
  BzzEvent.home_team_api_id        BzzEvent.away_team_api_id
       = BzzTeam.api_id                = BzzTeam.api_id
       = StatsHub team_id              = StatsHub team_id
```

Résolution en base :
```python
event_api_id = int(fixture.external_id.removeprefix("bzz_"))
bzz_event = await session.execute(
    select(BzzEvent).where(BzzEvent.api_id == event_api_id)
)
# → home_team_api_id, away_team_api_id, league_api_id, season_id
```

---

## 5. Logique d'implémentation

### 5.1 Fenêtre de polling

Le job ne tourne que dans la fenêtre **J-2h → J-10min** avant chaque KO :

```
Fixtures filtrées :
  status = "scheduled"
  kickoff_utc BETWEEN (now + 10min) AND (now + 2h)
  external_id LIKE "bzz_%"                     ← a un BzzEvent associé
  PAS de team_lineup official existante         ← pas encore scrappée
```

Cadence : **toutes les 15 minutes** (cron `*/15 * * * *`). Léger car peu de matchs simultanés.
Arrêt automatique dès qu'une compo `official` est persistée pour ce fixture.

### 5.2 Algorithme principal

```python
async def sync_statshub_lineup(session, fixture) -> bool:
    # 1. Extraire l'event_api_id depuis external_id
    event_api_id = int(fixture.external_id.removeprefix("bzz_"))

    # 2. Poll lineup-status (batch si plusieurs fixtures)
    status = await client.get_lineup_status([event_api_id])
    if status.get(str(event_api_id)) not in ("confirmed", "predicted"):
        return False  # Pas encore disponible

    # 3. Récupérer BzzEvent pour home/away team IDs + league/season
    bzz_ev = await session.get_bzz_event(event_api_id)

    # 4. Fetch home + away lineups
    home_data = await client.get_team_player_performance(
        bzz_ev.home_team_api_id, bzz_ev.league_api_id, bzz_ev.league_season_id
    )
    away_data = await client.get_team_player_performance(
        bzz_ev.away_team_api_id, bzz_ev.league_api_id, bzz_ev.league_season_id
    )

    # 5. Parser currentFixturePlayerStats pour event_api_id
    home_starters = _extract_starters(home_data, event_api_id)
    away_starters = _extract_starters(away_data, event_api_id)

    # 6. Upsert team_lineups + team_lineup_players
    lineup_type = "official" if status == "confirmed" else "probable_statshub"
    await _upsert_lineup(session, fixture, fixture.home_team, home_starters, lineup_type)
    await _upsert_lineup(session, fixture, fixture.away_team, away_starters, lineup_type)

    return True
```

### 5.3 Fallback si `currentFixturePlayerStats` est vide

Si `currentFixturePlayerStats` ne retourne rien (comportement non encore confirmé sur Big5),
fallback sur les stats du dernier match joué dans la saison :

```python
def _extract_starters_from_last_match(players_data, event_api_id):
    """Cherche l'event dans stats{} et prend minutesPlayed > 0 + substitutedIn is null."""
    starters = []
    for player in players_data.get("data", []):
        event_stats = player["stats"].get(str(event_api_id))
        if not event_stats:
            continue
        if event_stats.get("minutesPlayed", 0) > 0 and event_stats.get("substitutedIn") is None:
            starters.append({
                "name": player["name"],
                "position": _map_tactical_position(event_stats.get("position", "")),
                "jersey_number": None,
            })
    return starters
```

Ce fallback est uniquement utilisable après le match (données post-match). Pour les matchs à
venir, si `currentFixturePlayerStats` est vide et que le match n'a pas encore eu lieu, le job
loggue un WARNING et laisse la compo `probable_manual` ou `last_known` actives.

### 5.4 Mapping position tactique → position générique

| Positions StatsHub         | Position `team_lineup_players` |
|----------------------------|-------------------------------|
| `GK`                       | `GK`                          |
| `CB`, `LCB`, `RCB`, `LB`, `RB`, `LWB`, `RWB`, `WB` | `DEF` |
| `CDM`, `CM`, `CAM`, `LM`, `RM`, `LCM`, `RCM`, `DM`  | `MID` |
| `LW`, `RW`, `SS`, `CF`, `ST`, `F`, `AM`              | `FWD` |
| Valeur inconnue            | `MID` (par défaut)             |

---

## 6. Schéma DB — aucune modification requise

La table `team_lineups` supporte déjà tous les cas :

```sql
-- Nouveaux enregistrements créés par ce scraper :
INSERT INTO team_lineups (fixture_id, team, lineup_type, source, created_by)
VALUES (42, 'PSG', 'official', 'statshub', 'system');

-- Valeurs source utilisées :
-- 'statshub'              → compo officielle confirmée
-- 'statshub_predicted'    → compo prédite (status "predicted")
```

Le champ `source` existant (`VARCHAR(50)`) absorbe les nouvelles valeurs sans migration.

**Contrainte `uq_team_lineup`** (`fixture_id`, `team`, `lineup_type`) :
- Une seule compo `official` par (fixture, équipe) — idempotent par design
- Si une compo `probable_manual` existe, elle est conservée (type différent = pas de conflit)
- `official` prime sur `probable_manual` dans le resolver (priorité 0 vs 1)

---

## 7. Impact sur le calculateur et les recommandations

### Avant (sans compo officielle)
```
load_match_pricing(fixture_id=42)
  → resolve_lineup() → "last_known" ou None
  → pricing calculé sur ~30 joueurs (tous les joueurs connus de l'équipe)
  → home_lineup_players = None  ← calculateur affiche "compo non disponible"
```

### Après (avec compo StatsHub)
```
load_match_pricing(fixture_id=42)
  → resolve_lineup() → "official" (source="statshub")
  → pricing calculé uniquement sur les 11 titulaires
  → xG redistribué via compute_lineup_allocation()
  → home_lineup_players = [11 joueurs avec λ, probabilité, edge]
```

Les recommandations générées après la confirmation de compo n'incluent plus les remplaçants.
Les recommandations déjà approuvées sur un joueur finalement remplaçant devront être annulées
manuellement (pas de logique d'invalidation automatique dans cette spec).

---

## 8. Fichiers à créer / modifier

| Fichier | Action | Contenu |
|---------|--------|---------|
| `backend/app/ingestion/statshub/client.py` | Modifier | Ajouter `get_lineup_status(event_ids)` |
| `backend/app/ingestion/statshub/sync_lineups.py` | Créer | `sync_statshub_lineup()` + helpers |
| `backend/app/worker.py` | Modifier | `job_poll_statshub_lineups` + schedule `*/15` |
| `backend/tests/ingestion/statshub/test_lineup_sync.py` | Créer | Tests unitaires |

---

## 9. Risques et points à valider

| Risque | Probabilité | Mitigation |
|--------|-------------|------------|
| `currentFixturePlayerStats` toujours vide | Moyenne | Fallback stats post-match (§ 5.3) |
| StatsHub publie `"confirmed"` mais liste vide | Faible | Log WARNING + skip, retry au prochain tick |
| Joueur dans la compo StatsHub absent de `bzz_players` | Moyenne | Insertions par nom uniquement (sans FK player) |
| Décalage horodatage (timezone) | Faible | Utiliser `BzzEvent.event_date` UTC, jamais les timestamps StatsHub |
| Compo modifiée après confirmation (blessure de dernière minute) | Faible | Le job repoll et écrase l'`official` existante (upsert) |

---

## 10. Critère de succès

- `team_lineups` contient au moins une row `official` / `source=statshub` pour chaque fixture
  cible dans les 60 minutes précédant le KO
- Le calculateur retourne `home_lineup_players` non-null pour 100% des fixtures avec KO > 45min
- Le dashboard Compos affiche le badge **StatsHub** (vert) au lieu de **Manuel** ou **Dernière connue**
