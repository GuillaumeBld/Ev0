# Refonte section joueurs — Bzzoiro Design Spec

**Date :** 2026-04-10

**Goal :** Remplacer FotMob + Understat par Bzzoiro comme source unique de stats joueurs. Fournir une bibliothèque complète (5 grands championnats + LDC) avec liste filtrée/triée et fiche détaillée par joueur.

**Architecture :** Le backend expose des endpoints REST s'appuyant sur les tables Bzzoiro existantes (`bzz_players`, `bzz_player_season_stats`, `bzz_player_match_stats`, `bzz_leagues`, `bzz_teams`). Le frontend Next.js consomme ces endpoints — une page liste avec filtres/tri et une page de détail dédiée par joueur.

**Tech Stack :** FastAPI + SQLAlchemy async (backend), Next.js 14 App Router + Tailwind (frontend), PostgreSQL (données Bzzoiro).

---

## Page liste — `/dashboard/players`

### Barre de filtres (2 lignes)

**Ligne 1 — Championnats (boutons pill) :**
- Tous / 🏴󠁧󠁢󠁥󠁮󠁧󠁿 PL / 🇫🇷 L1 / 🇩🇪 BL / 🇪🇸 LL / 🇮🇹 SA / 🏆 UCL
- Championnats hardcodés côté frontend (api_id connus : PL=25, L1=21, BL=5, LL=16, SA=29, UCL=8)
- Clic sur un championnat → rechargement du dropdown équipes + rechargement de la liste

**Ligne 2 :**
- Recherche texte (filtre client-side sur nom/équipe)
- Dropdown équipes (dépend du championnat actif, alimenté par `GET /players/teams?league_api_id=X`)
- Filtre position : boutons Tous / G / D / M / F

### Tableau

Colonnes affichées : Joueur, Équipe, Pos, xG/90, xA/90, Rating, SoT/90, Forme (5), Mins.

Tri : clic sur n'importe quel header → toggle asc/desc → rechargement serveur avec `sort_by` + `sort_order`. Tri côté serveur (pas client-side).

Clic sur une ligne → navigation vers `/dashboard/players/[player_api_id]`.

Limite : 500 joueurs par appel (pas de pagination côté UI — filtrer par championnat/équipe réduit naturellement le volume).

---

## Page détail — `/dashboard/players/[id]`

### Header joueur
Nom complet, équipe, position (badge coloré), nationalité, âge (calculé depuis date_of_birth), taille, numéro de maillot, valeur marchande formatée (ex: 180M €).

Bouton **← Retour** qui navigue vers `/dashboard/players` en préservant les filtres actifs dans le query string (`?league=25&team=42` etc.).

### Section 1 — ⚽ Attaque
Grille de cards (stats saison) :
xG/90, xA/90, xG total, xA total, Buts, Passes décisives, Tirs/90, SoT/90, Précision tir (%), xG/tir, Finishing delta (buts − xG), xA delta (assists − xA).

### Section 2 — 🎯 Passes
Grille de cards :
Passes/90, Complétion passes (%), Passes clés/90, Longues balles/90, Précision longues balles (%), Centres/90, Précision centres (%).

### Section 3 — 🛡️ Défense
Grille de cards :
Duels gagnés (%), Duels aériens gagnés (%), Tacles réussis (%), Tacles/90, Interceptions/90, Récupérations/90, Dégagements total.

### Section 4 — 📈 Forme (5 derniers matchs)
Cards : xG form, Rating form, Buts form, Passes D. form. Indicateur de tendance rating (flèche haut/bas/stable).

### Section 5 — 📊 Graphique xG
Graphique en barres : xG par match (les N derniers matchs disponibles). Composant `PlayerMatchChart` existant, paramètre `metric="xg"`.

Si le champ `shotmap` de `BzzEvent` contient des coordonnées de tir avec `player_id`, ajouter un pitch SVG avec les positions de tir du joueur colorées par résultat (but/arrêt/manqué). Cette section est optionnelle — si les données ne le permettent pas, seul le graphique temporel est affiché.

### Section 6 — 📋 Historique match par match
Tableau scrollable horizontalement avec toutes les stats disponibles par match :
Date, Adversaire (domicile/extérieur), Mins, Buts, PD, xG, xA, Tirs, SoT, Passes, % Passes, KP, LB, Centres, Duels G/P, Aériens G/P, Tacles, Interceptions, Récupérations, Jaune, Rouge, Fautes, Subies, Rating.

Affiche les N derniers matchs disponibles dans `bzz_player_match_stats` (pas de limite artificielle).

---

## Backend — modifications

### `GET /players` (existant, étendu)
- `_SORTABLE_COLUMNS` étendu à toutes les colonnes numériques de `BzzPlayerSeasonStat` (~25 colonnes)
- `limit` max porté de 500 à 500 (déjà ok)
- Paramètre `league_api_id` déjà supporté — aucun changement

### `GET /players/leagues` (nouveau)
Retourne la liste des championnats ayant des joueurs en base :
```json
[{"api_id": 25, "name": "Premier League"}, ...]
```

### `GET /players/teams` (nouveau)
Paramètre : `league_api_id` (optionnel). Retourne les équipes ayant des joueurs en base avec stats saison :
```json
[{"api_id": 1234, "name": "Arsenal"}, ...]
```

### `GET /players/{id}` (existant, étendu)
- `recent_matches` : retourner **tous** les champs de `BzzPlayerMatchStat` (actuellement 7 champs, passer à 30+)
- Retourner **tous** les champs de `BzzPlayerSeasonStat` dans `season_stats` (déjà le cas)

---

## Frontend — fichiers

| Fichier | Action |
|---------|--------|
| `frontend/src/app/dashboard/players/page.tsx` | Modifier — ajouter filtres pill championnats + dropdown équipes, tri server-side |
| `frontend/src/app/dashboard/players/[id]/page.tsx` | Créer — page détail joueur |
| `frontend/src/lib/api.ts` | Modifier — ajouter types `League`, `Team`, `PlayerDetailFull`, `MatchStatFull` |
| `frontend/src/app/api/v1/players/teams/route.ts` | Créer — proxy API Next.js |
| `frontend/src/app/api/v1/players/leagues/route.ts` | Créer — proxy API Next.js |

---

## Ce qu'on abandonne

- FotMob scraping (de toute façon bloqué Cloudflare)
- Understat (matching trop fragile)
- L'export CSV Understat (`GET /players/export`) — endpoint conservé mais non mis en avant
- Les anciennes tables `players`, `player_stats`, `player_match_minutes` — conservées en base, non affichées

---

## Hors scope

- Comparaison de plusieurs joueurs
- Historique multi-saisons (uniquement 2025-2026)
- Photo de joueur (Bzzoiro ne fournit pas d'URL image)
