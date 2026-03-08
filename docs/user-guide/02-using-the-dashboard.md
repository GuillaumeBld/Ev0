# Guide des onglets

## Dashboard (/)
Vue d'ensemble : bankroll actuelle, ROI, nombre de paris actifs, P&L des 30 derniers jours.
Le graphique P&L est cumulatif.

## Calculateur (/calculator)
Entrez une cote et une probabilité estimée → Ev0 calcule l'edge et la mise Kelly recommandée.
Utile pour vérifier un pari manuellement avant de le placer.

## Recommandations (/recommendations)
Liste des paris à valeur détectés aujourd'hui. Chaque carte affiche :
- **Joueur** + marché (buteur / passeur)
- **Cote** du bookmaker
- **Fair odds** calculés par Ev0
- **Edge** en %
- **Mise Kelly** recommandée
- **Statut** : pending → approved → placed → won/lost

Cliquer sur une carte pour voir les détails (features utilisées, intensité lambda, etc.).

## Joueurs (/players)
Base de données des joueurs suivis. Recherche par nom, filtre par ligue ou poste.
Cliquer sur un joueur pour voir ses stats, son historique de recommandations, et sa forme.

## Matchs (/matches)
Calendrier des matchs à venir. Voir quels matchs ont des recommandations actives.
Possibilité d'ajouter manuellement un match ou d'ajuster les minutes attendues d'un joueur.

## Backtest (/backtest)
Lance le simulateur sur les données historiques (saison 2024-2025). Affiche :
- Nombre de paris simulés, win rate, ROI
- Courbe P&L cumulatif
- Comparaison avec une stratégie flat stake

## Historique (/history)
Tous les paris settlés. Filtrer par date, ligue, marché, résultat.
Statistiques : win rate global, ROI par ligue/marché.

## Autopilot (/autopilot)
Agent RL qui décide automatiquement quels paris prendre et à quelle mise.
Voir l'onglet Autopilot dans la documentation pour les détails.

## Santé (/health)
État du système en temps réel : API quota, dernière mise à jour des cotes, statut des jobs worker.

## Paramètres (/settings)
- Bankroll actuelle
- Kelly multiplier
- Ligues et marchés actifs
- Mode autopilot (paper / live)
