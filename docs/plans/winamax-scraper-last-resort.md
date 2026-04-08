# Winamax Scraper — Last Resort (NE PAS IMPLÉMENTER SANS ORDRE EXPLICITE)

## Statut
**EN ATTENTE** — Option de dernier recours. Ne pas implémenter sans instruction explicite.

## Contexte
Winamax est une source de cotes H2H + Over/Under 2.5 gratuite utilisable en fallback
pour `MarketXgService`. Scraping via Socket.IO (pas d'API officielle — violation CGU Winamax).
Usage accepté pour projet personnel non-commercial uniquement.

## Architecture technique

### Endpoint Socket.IO
```
wss://sports-eu-west-3.winamax.fr/uof-sports-server/socket.io/
  ?EIO=4&transport=websocket&language=FR&version=3.9.1&embed=false
```

### Flow de connexion (EIO v4)
1. Recv `0{...}` (open)
2. Send `40` (Socket.IO connect)
3. Recv `40{"sid":"..."}` (connected)
4. Emit `42["m", {"route": "sport:1", "requestId": "<timestamp>"}]`
5. Recv `42["m", {...payload...}]`
6. Répondre aux pings `2` avec `3`

### Format subscription
```python
# Tous les matchs football
emit("m", {"route": "sport:1", "requestId": str(int(time.time() * 1000))})

# Match spécifique
emit("m", {"route": f"match:{winamax_match_id}", "requestId": f"req_{int(time.time()*1000)}_{match_id}"})
```

### Structure payload réponse
```json
{
  "sports": {"1": {"matches": [matchId1, matchId2]}},
  "matches": {
    "61513672": {
      "competitor1Name": "Paris Saint-Germain",
      "competitor2Name": "AS Monaco",
      "tournamentId": 96,
      "matchStart": 1705355100
    }
  },
  "bets": {
    "betId": {
      "matchId": 61513672,
      "marketId": 1,
      "outcomes": [outcomeId1, outcomeId2, outcomeId3],
      "specialBetValue": "line=2.5"
    }
  },
  "odds": {
    "outcomeId1": 1.85,
    "outcomeId2": 3.10,
    "outcomeId3": 4.00
  }
}
```

### Market IDs football (sport=1)
| marketId | Marché | Outcomes |
|---|---|---|
| 1 | H2H (1X2) | home, draw, away |
| 18 | Over/Under (filter `specialBetValue="line=2.5"`) | over, under |
| 7016 | Handicap | — |

### Résolution des cotes
```python
for bet in payload["bets"].values():
    if bet["matchId"] != target_match_id:
        continue
    if bet["marketId"] == 1:  # H2H
        home_odds, draw_odds, away_odds = [payload["odds"][oid] for oid in bet["outcomes"]]
    elif bet["marketId"] == 18 and bet.get("specialBetValue") == "line=2.5":
        over_odds, under_odds = [payload["odds"][oid] for oid in bet["outcomes"]]
```

## Mapping matchId Winamax → fixture_id Ev0
Matching par `competitor1Name` + `competitor2Name` + date (même logique que `match_event_to_fixture_by_teams`).

## Fréquence prévue
- J-7 → J-1 : 1x/jour
- H-24 → H-1 : toutes les 30 min
- H-1 → H0 : **toutes les minutes**

## Intégration pipeline
- Bookmaker tag : `"winamax"`
- Table cible : `match_odds_snapshots` (même que Odds API)
- Priorité dans `_BOOKMAKER_PRIORITY` : après betfair/pinnacle

## Risques
- Violation CGU Winamax (civil, non pénal)
- IP VPS datacenter potentiellement détectable
- Mitigation : User-Agent browser réaliste, 1 req/min max
