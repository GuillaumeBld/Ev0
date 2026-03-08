# Limitations connues

## Quota API odds

L'API d'odds tierce (RapidAPI ou similaire) a un quota mensuel limité. Quand le quota est épuisé :
- Les cotes ne sont plus mises à jour
- Les recommandations utilisent les dernières cotes disponibles
- La page Santé indique `odds_api_exhausted: true`

**Vérifier le quota** : `GET /api/v1/health` → champ `odds_quota_remaining`

## Betclic gRPC

Le scraper Betclic utilise l'API gRPC non officielle. Il peut tomber en erreur si :
- Betclic change son protocole (1-2 fois/an)
- L'IP du VPS est bloquée (rotation d'IP non implémentée)

## Parions Sport

Scraper HTTP fragile. Retourne 404 sporadiquement. Pas de fallback implémenté.

## Compositions d'équipe

Les compositions officielles ne sont disponibles qu'1h avant le match. Avant ça, les minutes attendues sont estimées sur la base de l'historique de titularisation. Erreur possible de ±15 minutes → impact sur le calcul lambda.

## Ligues couvertes

Uniquement **Ligue 1** et **Premier League**. Les autres ligues (Bundesliga, Serie A, etc.) ne sont pas dans le pipeline.

## Marchés couverts

- ✅ Anytime Goalscorer (buteur)
- ✅ Anytime Assist (passeur)
- ❌ First Goalscorer (pas de modèle d'ordre)
- ❌ Over/Under buts (pas de modèle de match)
- ❌ Handicap (pas implémenté)

## Mode Live Autopilot

Le mode `live` de l'autopilot n'est **pas encore implémenté**. Seul le mode `paper` est actif.

## Absence de gestion des cotes en temps réel

Les cotes sont collectées toutes les heures. Si une cote bouge entre deux collectes, la recommandation peut devenir obsolète avant d'être settlée.

## Odds de synthèse pour le backtest

Le backtest utilise des cotes synthétiques (modèle Poisson + marge + bruit) plutôt que des vraies cotes historiques. Les résultats de backtest sont indicatifs, pas prédictifs.
