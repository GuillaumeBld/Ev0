# Guide des onglets

## Dashboard (/)
Vue d'ensemble : bankroll actuelle, ROI, nombre de paris actifs, P&L des 30 derniers jours.
Le graphique P&L est cumulatif.

## Calculateur (/calculator)
Entrez une cote et une probabilité estimée → Ev0 calcule l'edge et la mise Kelly recommandée.
Utile pour vérifier un pari manuellement avant de le placer.

## Recommandations (/recommendations)
Vue consolidée de tous les paris à valeur actifs, triés chronologiquement par heure de match.

**Mode View All (défaut)** : toutes les recommandations actives (statut `pending` ou `approved`) sont affichées, paginées 50 par page. La pagination apparaît en bas quand il y a plus de 50 picks.

**Filtre date (opt-in)** : cliquer "Filtrer par date" pour n'afficher que les picks d'un jour précis. Un badge `12 avr ✕` apparaît — cliquer ✕ pour revenir en View All.

**Filtres marché / edge** : actifs dans les deux modes. Changer le filtre edge ou marché reste en View All si aucune date n'est sélectionnée.

Chaque carte affiche :
- **Joueur** + marché (buteur / passeur)
- **Cote** du bookmaker
- **Fair odds** calculés par Ev0
- **Edge** en %
- **Mise Kelly** recommandée
- **Statut** : pending → approved → placed → won/lost
- **XgBadge** : badge 🟦 `API` (source Bzzoiro) ou 🟧 `MODEL` (solveur Poisson interne), indiquant quelle source de xG a été utilisée pour cette recommandation

La section **Expirées** (en bas, repliable) suit la même logique : View All par défaut, filtrée par date si une date est active.

Cliquer sur une carte pour voir les détails (features utilisées, intensité lambda, etc.).

## Joueurs (/players)
Base de données des joueurs suivis, alimentée par les données Bzzoiro.

**Vue liste** : tableau trié par nom, avec colonnes xG/90, xA/90, rating, minutes. Filtres disponibles :
- Recherche par nom
- Filtre par **poste** (GK / DEF / MID / FWD)
- Filtre par **minutes** (seuil minimum de minutes jouées en saison)

**Graphique PlayerMatchChart** : cliquer sur un joueur pour dérouler le détail — un graphique par match s'affiche avec l'évolution de ses métriques clés (xG, buts, rating) sur les dernières journées.

Les stats affichées proviennent de `bzz_player_season_stats` (agrégats saison) et `bzz_player_match_stats` (historique match par match). Elles sont mises à jour chaque nuit par `job_aggregate_season_stats` (04:00 UTC).

## Matchs (/matches)
Calendrier des matchs à venir. Voir quels matchs ont des recommandations actives.
Possibilité d'ajouter manuellement un match ou d'ajuster les minutes attendues d'un joueur.

## Compos (/lineups)
Saisie manuelle des compositions probables avant les matchs.
- Sélectionner un match → voir la compo active de chaque équipe (badge : Officielle / Probable / Dernière compo)
- Cliquer **Modifier** pour saisir une compo probable : sélectionner les joueurs + leur poste (GK/DEF/MID/FWD)
- Enregistrer → la compo devient active pour ce match (mode "Probable")
- **Effacer** → revient à la dernière compo officielle connue
- Le statut BU (avant-centre) d'un joueur se définit dans la page Joueurs (icône 🎯)

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

## Toggle xG mode (en-tête du dashboard)
Un sélecteur dans l'en-tête permet de choisir le mode xG utilisé pour les recommandations :
- **bzzoiro** : utilise les xG prédits par l'API Bzzoiro (badge 🟦 `API`) — source principale recommandée
- **model** : utilise le solveur Poisson interne basé sur les cotes de marché (badge 🟧 `MODEL`) — utile si les prédictions Bzzoiro ne sont pas encore disponibles

Ce toggle affecte le calcul des nouvelles recommandations. Les recommandations déjà générées conservent leur badge d'origine.

## Santé (/health)
État du système en temps réel : API quota, dernière mise à jour des cotes, statut des jobs worker.
Inclut le statut des 6 jobs de synchronisation Bzzoiro (dernière exécution, nombre de records synchronisés).

## Paramètres (/settings)
- Bankroll actuelle
- Kelly multiplier
- Ligues et marchés actifs
- Mode autopilot (paper / live)
