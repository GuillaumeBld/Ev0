# Spike — historique API Bzzoiro (résultats)

**Date** : 2026-07-19
**Réf** : spec 2026-07-18 §3.5, plan lot 1 tâche 4

## A. Ligues et saisons exposées

`/api/leagues/` retourne `count: 69` ligues (contre 6 ciblées par `TARGET_LEAGUE_INTERNAL_IDS`), avec pagination (`next` présent dès la page 1). Au-delà des 6 cibles, on retrouve entre autres : AFC Asian Cup, Africa Cup of Nations, Allsvenskan (Suède), Botola Pro (Maroc), Brasileirão Serie A/B, CAF Champions League, Carabao Cup (EFL Cup), **Championship** (Angleterre), Chinese Super League, Club Friendlies — soit une couverture bien plus large que le périmètre produit actuel.

Chaque ligue porte un objet `current_season` (`id`, `name`, `year`, `start_date`, `end_date`) — ex. Premier League → saison courante id `1058` (« Premier League 26/27 »). Un endpoint dédié existe : `/api/seasons/?league=<internal_id>`, qui liste **tout le catalogue** de saisons d'une ligue. Pour la Premier League (`league=1`) : `count: 35` saisons, de « Premier League 17/18 » (id `329`) à « Premier League 26/27 » (id `1058`), en passant par **id `336` = « Premier League 24/25 »** (`year: 2024`, `start_date: 2024-07-01`, `end_date: 2025-06-30`).

Donc : les saisons passées sont *cataloguées* (metadata) très loin en arrière, mais cela ne présage pas de la disponibilité des données associées (voir B et C).

## B. Matchs historiques (fenêtre 2024-25)

**Servis, oui, et bien au-delà de la fenêtre testée.**

- `/api/events/?league=1&date_from=2024-08-01&date_to=2024-09-01` → `count: 30` matchs Premier League, statut `finished`, scores complets (`home_score`/`away_score`), dates, équipes, stades — objets riches et complets, identiques à la structure des matchs courants.
- Élargi à la saison complète : `/api/events/?league=1&date_from=2024-08-01&date_to=2025-06-30` → `count: 380` (cohérent avec 20 équipes × 38 journées).
- Test de profondeur : `/api/events/?league=1&date_from=2018-08-01&date_to=2018-09-01` → `count: 37` matchs finis avec score complet. L'historique des matchs remonte donc **au moins à 2018**, probablement plus loin (catalogue de saisons jusqu'à 17/18).
- Ajout du paramètre `season=2024-2025` (chaîne, déduite du nommage des saisons) à la requête B : **aucun effet observable** — mêmes 30 résultats, ni erreur ni changement. Le filtrage par `date_from`/`date_to` suffit seul ; le paramètre `season` sur `/api/events/` n'a pas d'effet démontré dans ce spike (ni positif ni négatif).

**Volume estimé pour un backfill matchs** : ~380 matchs/saison/ligue pour une ligue à 20 équipes (moins pour les ligues à effectif réduit type Ligue 1 à 18, etc.). Pour les 6 ligues cibles sur une saison 2024-25 : de l'ordre de **2000-2300 matchs**.

## C. Profondeur des player-stats

**Aucune profondeur historique — confirmé de façon directe et définitive.**

Point de méthode : l'endpoint `/api/player-stats/` n'accepte **pas** de filtre `league=` (confirmé par le code existant, `sync_player_stats.py` : « Filter: use `player=<internal_id>` (NOT `player_id=`) »). Les requêtes C/E du script initial avec `league=1` retournaient donc `count: 0` — ce n'était pas une absence de données mais un mauvais paramètre. Correction : récupération d'un `internal_id` de joueur réel via `/api/players/?team=17` (Man Utd) → joueur `id=1792` (Amad Diallo, à Man Utd depuis 2021 selon son historique de transferts, donc présent toute la saison 24/25).

- `/api/player-stats/?player=1792` → `count: 49` lignes au total, **sans pagination supplémentaire** (`next: null`). Dates couvertes : **2025-10-10 → 2026-06-30**. Aucune ligne avant octobre 2025, alors que ce joueur a joué toute la saison 2024-25 pour un club qui dispute ~50-60 matchs/an (championnat + coupes + Europe).
- Ajout de `season=336` (id confirmé de « Premier League 24/25 » via `/api/seasons/`) à la même requête → **résultat rigoureusement identique** (49 lignes, mêmes dates) : le paramètre `season` est silencieusement ignoré par `/api/player-stats/`.
- Preuve directe et incontestable : `/api/player-stats/?event=160173` (l'événement Man Utd–Fulham du 16/08/2024, confirmé exister et être `finished` via B) → **`count: 0`**. Aucune stat joueur n'existe pour ce match précis de la saison 24/25, quel que soit le joueur. Test de contrôle sur un match encore plus ancien (`event=157893`, PL août 2018) → également `count: 0`.

Conclusion C : `/api/player-stats/` ne couvre qu'une **fenêtre glissante d'environ 9 mois** (grosso modo la saison en cours + une marge), sans paramètre permettant d'accéder aux saisons passées. Ni le nom de saison (chaîne), ni l'id de saison catalogué, ni un filtre direct par événement historique ne débloquent de données antérieures.

## Décision (issue spec §3.5)

**Option retenue : 2** — backfill matchs échelonné, avec une réserve documentée ci-dessous sur les stats joueur détaillées.

- **Option 1 — agrégats historiques directs : non faisable.** La liste des 16 endpoints exposés par la racine `/api/` (`leagues`, `teams`, `events`, `fixtures`, `matches`, `live`, `predictions`, `players`, `player-stats`, `odds`, `managers`, `seasons`, `venues`, `social`, `tv-channels`, `broadcasts`) ne contient aucun endpoint d'agrégat saison (pas de `player-season-stats`, `team-season-stats`, ou équivalent). Le seul endpoit de stats par joueur (`player-stats`) est strictement du **per-match**, et confirmé sans profondeur historique (cf. C).
- **Option 2 — backfill matchs échelonné : faisable, retenue.** `/api/events/` sert l'historique complet des matchs (scores, statuts, dates, équipes, stades) sur plusieurs saisons, testé jusqu'en 2018. Volume estimé : ~2000-2300 matchs pour les 6 ligues cibles sur la saison 2024-25 seule (~380/ligue à 20 équipes). Plan d'échelonnement ligue par ligue, dans l'ordre de volume de paris décroissant (aligné sur l'ordre implicite de `TARGET_LEAGUE_API_IDS`/production) : Premier League → Champions League → Ligue 1 → Bundesliga → La Liga → Serie A. Pagination native de `/api/events/` (offset/limit=50) permet un backfill par petits lots sans risque de dépassement de quota — à raison d'une ligue/saison par run planifié (cf. rythme déjà utilisé pour `sync_events`/`sync_events_full_season` dans `app/worker.py`).
- **Option 3 — fallback Transfermarkt : requis, mais seulement pour les stats joueur détaillées (pas pour les matchs).** Puisque `/api/player-stats/` n'a aucune profondeur avant ~octobre 2025 (confirmé match par match), tout besoin de statistiques par joueur (xG, passes, tirs, etc.) pour la saison 2024-25 ou antérieure ne peut pas être comblé par Bzzoiro. Si les lots 2-3 ont besoin d'un historique de stats joueur (et pas seulement de résultats de matchs) pour le backtesting/calibration, un fallback Transfermarkt (ou équivalent) sera nécessaire pour cette seule couche — en sachant que Transfermarkt ne fournit typiquement que des stats basiques (apparitions, buts, passes déterminantes, cartons, minutes), pas les métriques avancées (xG, xA, précision de passe) utilisées par le moteur de pricing actuel.

## Conséquences sur les lots 2-3

- Le **harnais de backfill** (lot 2) doit cibler `/api/events/` avec pagination par ligue/saison, échelonné dans le temps pour rester sous quota (le client a déjà un retry avec backoff exponentiel intégré — `BzzoiroClient.get_page`). Il peut reconstituer l'historique des matchs (fixtures, scores, calendriers) des 6 ligues cibles sur plusieurs saisons passées sans dépendance externe.
- Le harnais **ne doit pas** tenter de backfiller `/api/player-stats/` au-delà de la fenêtre glissante actuelle (~9 mois) : c'est un appel perdu, systématiquement `count: 0` pour tout événement antérieur à ~octobre 2025. Toute logique de pricing/calibration (Beta, lot 3) qui dépend de stats joueur historiques pour la saison 2024-25 devra soit (a) se limiter aux résultats de matchs bruts (issus d'Option 2) comme proxy, soit (b) intégrer une source externe (Transfermarkt, Option 3) pour la couche stats joueur — à trancher explicitement dans le plan du lot 3 selon ce que le modèle Beta exige réellement comme features historiques.
- Le paramètre `season=` ne s'étant montré fiable sur **aucun** des deux endpoints testés (`events`, `player-stats`), le plan du lot 2 doit continuer à filtrer par `date_from`/`date_to` (déjà le pattern utilisé dans `sync_events`) plutôt que de introduire une dépendance à un paramètre `season` non garanti.
