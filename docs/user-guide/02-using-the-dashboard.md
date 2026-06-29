# Guide des onglets

## Dashboard (/)

Vue d'ensemble : bankroll actuelle, ROI, nombre de paris actifs, P&L des 30 derniers jours. Le graphique P&L est cumulatif.

---

## Recommandations (/recommendations)

Vue consolidée de tous les paris à valeur identifiés, triés par heure de match.

**Mode View All (défaut)** : toutes les recommandations actives (statut `pending` ou `approved`) sont affichées, paginées 50 par page.

**Filtre date (opt-in)** : cliquer "Filtrer par date" pour n'afficher que les picks d'un jour précis.

**Filtres marché / edge** : actifs dans les deux modes.

Chaque carte affiche :
- **Joueur** + marché (`buteur` / `passeur` / `supersub`)
- **Cote** du bookmaker · **Fair odds** Ev0 · **Edge** en %
- **Mise Kelly** recommandée
- **Statut** : pending → approved → placed → won / lost / void

La section **Expirées** (en bas, repliable) liste les recommandations déjà settlées.

---

## Calculateur (/calculator)

Calcule les cotes fair en temps réel pour tous les joueurs d'un match, via le moteur top-down Ev0.

**Sélectionner un match** dans le dropdown (matchs à venir uniquement). Auto-sélection via `?match=<id>` dans l'URL.

**Badge xG source** : indique d'où vient le xG utilisé.
- `API` (bleu) — xG live Bzzoiro (cotes de marché)
- `MODEL` (orange) — modèle interne Ev0

**Fraîcheur** : un bandeau indique l'âge du dernier scraping. Si les cotes sont trop anciennes, le calcul est bloqué.

**Override xG** : chaque table d'équipe a un champ de saisie pour forcer un xG différent. Cliquer **Recalculer** pour appliquer.

**P(0-0)** : affiché à côté du bouton Recalculer — probabilité d'un score nul (utile pour les paris "remboursé si 0-0").

**Colonnes des tables joueurs** :

| Colonne | Description |
|---------|-------------|
| ⬡P | Tireur de penalty (ambre) — cliquer une ligne pour changer |
| Pos | FW / MF / DF |
| Min | Minutes attendues |
| P(sub) / t̄sub | P(remplaçant) + minute d'entrée estimée |
| **P(but+sub) / C.But+Sub** | **Priorité — marché supersub buteur** (bleu) |
| **P(ass+sub) / C.Ass+Sub** | **Priorité — marché supersub passeur** (bleu) |
| P(but) / C.But | Marché standard buteur (gris) |
| P(ass) / C.Ass | Marché standard passeur (gris) |

**Tireur de penalty** : cliquer sur une ligne pour désigner ce joueur. La ligne passe en ambre, la cote fair intègre le bonus pénalty. Le choix est sauvegardé.

**Widget Compo** (sous chaque table) : saisir les titulaires confirmés (≥5 joueurs) → le pricing est recalculé en redistribuant le xG uniquement sur ces joueurs.

---

## Joueurs (/players)

Base de données des joueurs suivis, alimentée par Bzzoiro.

Tableau filtrable par nom, poste, minutes minimum. Cliquer un joueur → historique match par match.

---

## Matchs (/matches)

Calendrier des matchs de championnat avec statut des recommandations.

---

## Compos (/lineups)

Compos probables avant les matchs. Modifier → enregistrer pour que le calculateur puisse redistribuer le xG sur les titulaires.

---

## Historique (/history)

Tous les paris settlés. Filtres : date, ligue, marché, résultat. Win rate + ROI par ligue et marché. Settlement manuel possible (correction erreur, void).

---

## Autopilot (/autopilot)

Agent de décision automatique. Voir `03-autopilot.md`.

---

## Backtest (/backtest)

Simulateur historique : nombre de paris, win rate, ROI, courbe P&L.

---

## CDM 2026 — Matchs (/wc2026/matches)

Calendrier WC2026 avec résultats, xG, stats joueurs. Panel de détail au clic sur un match.

Filtres : statut (terminés / à venir) + round (J1/J2/J3/16e/8e/QF/SF/Fin/3e).

---

## CDM 2026 — Stats (/wc2026/stats)

Stats joueurs agrégées sur l'ensemble du tournoi.

---

## CDM 2026 — Compos (/wc2026/lineups)

Compos attendues par match WC2026.

---

## CDM 2026 — Pricing (/wc2026/pricing)

Cotes fair outright CDM par joueur (meilleur buteur / passeur), comparées aux bookmakers. Recalculées automatiquement après chaque run bracket ELO.

---

## CDM 2026 — Bracket (/wc2026/bracket)

Tableau ELO + probabilités d'avancement par nation (Monte Carlo 50 000 simulations).
Colonnes triables. Horodatage du dernier calcul visible en haut.

---

## Santé (/health)

État du système : dernière mise à jour des cotes, statut des jobs worker, couverture par fixture.

---

## Paramètres (/settings)

Bankroll, Kelly multiplier, ligues/marchés actifs, mode autopilot.
