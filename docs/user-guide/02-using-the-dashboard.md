# Guide des onglets

## Dashboard (/)

Vue d'ensemble : bankroll actuelle, ROI, nombre de paris actifs, P&L des 30 derniers jours. Le graphique P&L est cumulatif.

---

## Recommandations (/recommendations)

Vue consolidée de tous les paris à valeur identifiés, triés par heure de match.

**Mode View All (défaut)** : toutes les recommandations actives (statut `pending` ou `approved`) sont affichées, paginées 50 par page.

**Filtre date (opt-in)** : cliquer "Filtrer par date" pour n'afficher que les picks d'un jour précis. Un badge `18 avr ✕` apparaît — cliquer ✕ pour revenir en View All.

**Filtres marché / edge** : actifs dans les deux modes.

Chaque carte affiche :
- **Joueur** + marché (buteur / passeur décisif)
- **Cote** du bookmaker · **Fair odds** Ev0 · **Edge** en %
- **Mise Kelly** recommandée
- **Confiance** (0.25 à 0.85 selon le nombre de matchs et la disponibilité des données de forme)
- **Statut** : pending → approved → placed → won / lost / void

La section **Expirées** (en bas, repliable) liste les recommandations déjà settlées.

---

## Calculateur (/calculator)

Sélectionner un match → Ev0 calcule en temps réel les cotes fair pour **tous les joueurs** des deux équipes, en utilisant le même moteur top-down que les recommandations.

Chaque carte joueur affiche :
- **Part xG** (npxg_share) et **Part xA** (xa_share) dans le budget équipe
- **Cote fair buteur** et **cote fair passeur**
- **Minutes attendues** estimées depuis l'historique

Si le match a des compositions connues, activer l'onglet **Compo** pour redistribuer le xG uniquement entre les titulaires (cotes plus précises).

**Override manuel** : il est possible de saisir un xG d'équipe différent (ex. : si une cote de marché semble aberrante) — les cotes joueurs se recalculent instantanément.

---

## Joueurs (/players)

Base de données des joueurs suivis, alimentée par Bzzoiro.

**Vue liste** : tableau trié par nom, avec colonnes xG/90, xA/90, rating, minutes jouées. Filtres disponibles :
- Recherche par nom
- Filtre par poste (GK / DEF / MID / FWD)
- Filtre par seuil minimum de minutes

Cliquer sur un joueur pour dérouler l'historique match par match : évolution xG, buts, rating sur la saison.

---

## Matchs (/matches)

Calendrier des matchs à venir avec statut des recommandations disponibles. Possibilité d'ajuster manuellement les minutes attendues d'un joueur pour un match.

---

## Compos (/lineups)

Saisie des compositions probables avant les matchs.

- Sélectionner un match → voir la composition active de chaque équipe
- Cliquer **Modifier** pour saisir une compo probable
- Enregistrer → la compo devient active (mode "Probable")
- **Effacer** → revient à la dernière compo connue

Une fois une compo enregistrée, le calculateur peut redistribuer le xG uniquement entre les titulaires.

---

## Historique (/history)

Tous les paris settlés. Filtrer par date, ligue, marché, résultat.  
Statistiques : win rate global, ROI par ligue et par marché.

**Settlement manuel** : cliquer sur un pari → modifier le résultat (won / lost / void). Utilisé pour corriger les erreurs ou annuler un pari (joueur blessé en cours de match n'ayant pas joué 1 minute → void).

---

## Autopilot (/autopilot)

L'agent qui décide automatiquement quels paris prendre et à quelle mise. Voir la section dédiée dans la documentation.

---

## Backtest (/backtest)

Lance le simulateur sur les données historiques. Affiche le nombre de paris simulés, win rate, ROI et la courbe P&L cumulatif.

---

## Santé (/health)

État du système en temps réel : dernière mise à jour des cotes, statut des jobs worker, couverture des match odds par fixture.

---

## Paramètres (/settings)

- Bankroll actuelle
- Kelly multiplier (fraction de Kelly appliquée)
- Ligues et marchés actifs
- Mode autopilot (paper / live)
