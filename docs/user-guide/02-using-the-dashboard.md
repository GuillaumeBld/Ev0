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
L'agent qui decide automatiquement quels paris prendre et a quelle mise.

La page est organisee en plusieurs sections :
- **En haut** : cartes resumant les performances des 30 derniers jours (nombre de paris, win rate, ROI, Sharpe, Brier score) et un bouton pour activer/desactiver l'agent
- **Entrainement** : bouton pour entrainer l'agent sur les donnees historiques. Affiche les resultats une fois termine (nombre de records, P&L, Sharpe)
- **Optimisation** : bouton "Lancer Optimisation" qui cherche automatiquement les meilleurs reglages pour l'agent (~30 a 60 secondes). Affiche ensuite la croissance du capital (log-wealth), la fiabilite du resultat (DSR), et quelles informations l'agent utilise ou ignore. L'agent est mis a jour automatiquement apres chaque optimisation
- **Decisions du jour** : tableau avec chaque pari recommande, l'action choisie par l'agent (passer, quart Kelly, demi Kelly, Kelly), et le montant de la mise
- **En bas** : courbe P&L cumulative, importance de chaque information dans la decision, repartition des actions, et graphique de calibration

Voir la page Autopilot dans la documentation pour comprendre comment l'agent fonctionne.

## Santé (/health)
État du système en temps réel : API quota, dernière mise à jour des cotes, statut des jobs worker.

## Paramètres (/settings)
- Bankroll actuelle
- Kelly multiplier
- Ligues et marchés actifs
- Mode autopilot (paper / live)
