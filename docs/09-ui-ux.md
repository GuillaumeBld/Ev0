# UI et UX — État réel du dashboard

## Utilisateur cible
Un opérateur unique qui :
- Analyse les matchs et calcule les cotes fair via le Calculateur
- Consulte les recommandations et gère les paris
- Surveille la santé du système et les données

---

## Navigation (Sidebar)

### Section principale
- **Dashboard** — vue macro, paris récents, résumé du jour
- **Calculateur** — pricing top-down par match
- **Recommandations** — paris proposés par le modèle

### Section Analyse
- **Joueurs** — fiche joueur (stats, forme, historique)
- **Matchs** — calendrier championnat avec stats et compos
- **Équipes** — vue équipe
- **Compos** — compos attendues par match
- **Backtest** — performance historique du modèle
- **Historique** — historique des paris

### Section CDM 2026
- **Matchs** — calendrier WC2026 avec résultats, xG, stats joueurs
- **Stats** — stats joueurs CDM agrégées
- **Compos** — compos WC2026 attendues
- **Pricing** — cotes fair buts/passes CDM par joueur (vs bookmakers)
- **Bracket** — ELO des nations + probabilités d'avancement (bracket page)

### Section Système
- **Autopilot** — gestion du pipeline automatique
- **Docs** — documentation interne
- **Santé** — état des scrapers et fraîcheur des données
- **Paramètres** — configuration

---

## Écrans principaux

### Calculateur (`/dashboard/calculator`)

Vue principale pour pricer un match de championnat en temps réel.

**Sélecteur de match**
- Dropdown des matchs à venir (≤200 fixtures, `status=scheduled, upcoming_only=true`)
- Auto-sélection via paramètre URL `?match=<fixture_id>`

**Badge xG source** (`XgSourceBadge`)
Indique d'où vient le xG utilisé :
- `API` (bleu) — xG live scrappés depuis Bzzoiro (cotes de marché)
- `MODEL` (orange) — modèle interne Ev0 (Dixon-Coles / stats historiques)
- `OVERRIDE` — valeur saisie manuellement par l'opérateur

**Fraîcheur des cotes**
Bandeau en bas du header indiquant l'âge du dernier scraping Bzzoiro (`last_scraped_at`).
Si les cotes sont trop anciennes, le calcul est bloqué avec un avertissement ambre.

**Override xG par équipe**
Champ numérique dans le header de chaque table d'équipe.
Bouton "Recalculer" pour déclencher le re-pricing avec les valeurs overridées.

**P(0-0)**
Affiché à côté du bouton Recalculer : probabilité que le score soit 0-0 (Poisson indépendant).
Pertinent pour les paris "remboursé si 0-0" proposés par certains bookmakers FR.

**Tables joueurs (par équipe)**

Colonnes affichées :

| Colonne | Description |
|---------|-------------|
| Joueur | Nom + indicateur tireur de penalty (⬡P, ambre) |
| Pos | Position (FW orange / MF bleu / DF gris) |
| Min | Minutes attendues |
| P(sub) | Probabilité d'être remplaçant entrant |
| t̄sub | Minute moyenne d'entrée si remplaçant |
| **P(but+sub)** | **P(marquer avec mécanique supersub)** — colonne primaire (bleu) |
| **C.But+Sub** | **Cote fair supersub buteur** — colonne primaire (bleu) |
| **P(ass+sub)** | **P(passer avec mécanique supersub)** — colonne primaire (bleu) |
| **C.Ass+Sub** | **Cote fair supersub passeur** — colonne primaire (bleu) |
| P(but) | P(marquer, standard) — colonne secondaire (gris) |
| C.But | Cote fair buteur standard |
| P(ass) | P(passer, standard) — colonne secondaire (gris) |
| C.Ass | Cote fair passeur standard |

Les colonnes supersub (bleues) sont affichées en priorité car c'est le marché cible principal.
Les colonnes standard (grises) restent visibles pour comparaison.

**Tireur de penalty**
- Auto-détecté depuis l'historique ou la configuration manuelle
- Cliquer sur une ligne pour désigner ce joueur comme tireur de penalty de l'équipe
- La ligne passe en ambre ; la cote fair buteur intègre le bonus pénalty
- Le choix est sauvegardé en base via `POST /api/v1/pen-takers`

**Widget Compo** (`LineupPricingWidget`)
Sous chaque table d'équipe, widget permettant de saisir les titulaires confirmés.
Si ≥5 titulaires sont renseignés, le pricing est recalculé en redistribuant le xG uniquement sur ces joueurs (`home_starters` / `away_starters` dans la requête).
Le résultat est affiché dans une table parallèle "compo confirmée" (`home_lineup_players`).

---

### CDM 2026 — Matchs (`/dashboard/wc2026/matches`)

Liste paginée des matchs WC2026 avec panel de détail latéral.

**Filtres**
- Statut : Tous / Terminés / À venir
- Round (boutons) : J1 / J2 / J3 / 16e / 8e / QF / SF / Fin / 3e

**Rounds Bzzoiro** (non-séquentiels) :

| round_number | Label | Description |
|-------------|-------|-------------|
| 1, 2, 3 | Journée 1/2/3 | Phase de groupes |
| 6 | 16es de finale | R32 — 16 matchs |
| 5 | 8es de finale | R16 — 8 matchs |
| 27 | Quarts de finale | QF |
| 28 | Demi-finales | SF |
| 29 | Finale | |
| 50 | 3e place | |

---

### CDM 2026 — Pricing (`/dashboard/wc2026/pricing`)

Tableau des cotes fair outright buts/passes CDM par joueur, comparées aux bookmakers.
Source : table `wc2026_player_pricing`, recalculée automatiquement après chaque run bracket.

---

### CDM 2026 — Bracket (`/dashboard/wc2026/bracket`)

Tableau des 48 nations avec ELO courant et probabilités d'avancement issues du Monte Carlo.

Colonnes : Nation | ELO | P(Groupes) | P(16e) | P(8e) | P(QF) | P(SF) | P(Finale) | E[matchs]

ELO color-codé : vert ≥1650 / jaune ≥1550 / orange ≥1450 / gris <1450.
Colonnes triables au clic. Horodatage du dernier calcul affiché.

---

### Recommandations (`/dashboard/recommendations`)
- Table : joueur, marché, cote Ev0, cote book, edge, P(titulaire), minutes attendues
- Indicateur `supersub_market_type` : `standard` ou `supersub`
- Clic sur ligne pour voir le détail du calcul

---

### Dashboard (`/dashboard`)
- Résumé du jour (paris actifs, ROI)
- Matchs des prochaines 48h

---

## Principes UX

- **Supersub en priorité visuelle** — colonnes bleues devant colonnes grises pour diriger l'attention vers le marché cible
- **Explicabilité** — toutes les valeurs intermédiaires (lambda, share, minutes) sont visibles
- **Fraîcheur toujours visible** — badge xG source + âge du dernier scraping sur chaque calcul
- **Override non-destructif** — l'override xG ou compo ne persiste pas entre sessions, il est recalculé à la demande
