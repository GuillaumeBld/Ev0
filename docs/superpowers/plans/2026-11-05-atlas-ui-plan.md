# Atlas UI — brancher les vraies données (plan)

**Goal:** Porter la maquette validée « Ev0 Atlas » (map-first) en vraie page Next.js câblée au backend (854 clubs, joueurs, stats segmentées, écussons).

**Maquette de référence (UX/visuel à répliquer) :** `docs/superpowers/atlas-reference-mockup.html` (carte monde par continent, palette ambre, Explorateur = barre d'outils unique + rail clubs + bandeau club + cartes/liste joueurs + comparaison, fiche joueur segmentée).

## Données & API (déjà en place — réutiliser)
- `GET /api/v1/players` — filtres `league_api_id, team_api_id, position(G/D/M/F), season, min_minutes, sort_by, sort_order, limit, offset` → PlayerSummary (name, position, team_name, goals, goal_assist, xg_per_90, avg_rating, matches_played, minutes_played, crest_url, api_football_id).
- `GET /api/v1/players/{id}` → PlayerDetail.season_stats = **tous** les champs segmentés (goals, expected_goals, total_shots, shot_accuracy, key_pass, pass_completion, total/accurate_pass, tackles_per_90, interceptions_per_90, duel_win_rate, aerial_win_rate, ball_recovery, yellow/red_card, minutes, starts, starts_pct, avg_rating, form_*).
- `GET /api/v1/players/{id}/career` → carrière.
- `GET /api/v1/canonical-teams` → tous les clubs (name_fr, country, league_api_id, bzz_team_id, api_football_id, crest_url).
- `leagues` (table) : id, name, country, tier, kind, format, rank.

## Tâche 1 — Endpoint géographique `GET /api/v1/atlas/geo` (backend)
Retourne l'arbre pour carte + menus :
- `countries: [{code(ISO2), name, continent, leagues:[{league_api_id, name, tier, kind, coverage('full'|'part'), club_count}]}]` — dérivé de `leagues` (kind domestic_1/2) + `canonical_teams` (comptes clubs par league_api_id non nul), ordonné par `rank`. ISO2 + continent via un dict nom→(code,continent) côté serveur (maintenable). `coverage` = `data_coverage_runs` récent (status ok/partial) sinon présence de season-stats.
- `international: [{id, name, org('UEFA'|'FIFA'|…), kind('uefa_club'|'selection')}]` — depuis `leagues` kind uefa_club/selection (7/8/83/90/27/64/65…).
- Tests : structure, ISO2 présents pour les pays couverts, international listé, club_count cohérent.

## Tâche 2 — Composant carte `WorldMap.tsx` (frontend)
- Composant React embarquant le SVG monde inline (177 pays, viewBox 0 0 1000 394 — réutiliser les paths de la maquette) + bbox pour le zoom.
- Props : `covered` (set ISO2 → continent color), `onPick(iso)`, hover tooltip, zoom/pan (transform sur `<g>`), couleurs par continent.

## Tâche 3 — Page Atlas `dashboard/atlas/page.tsx` (frontend)
- Barre top (recherche pays, bascule Carte/Menus, palette ambre figée, thème).
- Carte (WorldMap) + hero/légende continents + bouton Compétitions internationales ; mode Menus (pays + international).
- Charge `/atlas/geo` au montage.

## Tâche 4 — Explorateur (frontend, dans la page Atlas)
- Fenêtre plein écran : barre d'outils unique (retour, pays + onglets division, filtre poste, recherche, tri, vue cartes/liste, comparer), rail clubs (depuis canonical-teams d'une ligue), bandeau club au clic, cartes/liste joueurs via `/players?league_api_id&team_api_id&position&sort_by`.
- Pagination serveur (limit/offset) + compteur.

## Tâche 5 — Fiche joueur segmentée + comparaison (frontend)
- Panneau fiche depuis `/players/{id}` : groupes Finition/Création/Distribution/Défense/Discipline/Temps de jeu (mapper les champs season_stats) + carrière (`/career`) + état « données partielles ».
- Comparaison 2-3 joueurs côte à côte.

## Tâche 6 — Intégration nav + déploiement
- Entrée « Joueurs » du dashboard → la page Atlas (remplacer/compléter l'ancienne `players/page.tsx`). Garder le calculateur/calendrier intacts.
- Déployer (rebuild frontend) + vérifier en prod.

## Garde-fous
- Palette ambre + couleurs par continent (validées). Contraste fiche élevé.
- Écussons via `crest_url` (jamais par nom). Coverage « partielle » signalée, jamais de zéro trompeur.
- Ne pas toucher au pipeline paris ni aux pages calculateur/calendrier.
