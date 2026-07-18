# Transition saison 2026-27 — Registre de modèles Alpha/Beta, harnais d'évaluation, pricing calibré

**Date** : 2026-07-18
**Statut** : validé (brainstorming Yohan/Claude, sessions 15-18/07)
**Échéance structurante** : reprise des championnats mi-août 2026 (= début de l'évaluation autopilot sur data propre)

## 1. Contexte et problème

La saison 2025-26 s'achève ; la base joueurs et le pricing doivent traverser la
frontière de saison sans perte de précision. État des lieux :

- **Transferts mercato : déjà gérés.** `sync_players` (worker, 03:00 UTC) met à
  jour `current_team_api_id`, `loan_team_*` et `market_value` depuis Bzzoiro.
  Les stats d'un joueur transféré le suivent dans son nouveau club car le
  pricing joint les stats via le joueur, pas via l'équipe. Aucun scraping
  Transfermarkt nécessaire pour les transferts.
- **Schéma multi-saisons : déjà prêt.** `bzz_player_season_stats` est clé sur
  `(player_api_id, league_api_id, season)`.
- **Casse à la transition** :
  1. Saison codée en dur : `aggregate_all_leagues(season="2025-2026")` +
     `SEASON_START_DATE` (constante). Rien ne bascule au 1er août.
  2. Falaise du petit échantillon : le pricing prend `max(season)` par joueur
     (`team_xg.py`). Dès la J1, 90 minutes jouées écrasent une saison n-1
     complète par des per-90 ultra-bruités.
  3. Joueurs sans historique : promus, jeunes, arrivées depuis des ligues hors
     périmètre (couverture actuelle : PL, L1, Bundesliga, Liga, Serie A, LdC).

## 2. Décisions actées (non renégociables sans accord explicite)

- **Tout est pré-match.** Aucun pricing ni pari in-play. Les comparaisons de
  modèles se font sur des prix figés au coup d'envoi, réglés après match.
- **Convention "avec sub".** Le marché de référence est Buteur/Passeur avec
  remplaçant : le ticket vit les 90 minutes entières (le remplaçant hérite du
  pari). L'EV0 se price donc sur 90 minutes pleines ; l'incertitude sur les
  minutes jouées du joueur nommé sort du problème.
- **Aucune donnée futile.** La valeur marchande n'est jamais un signal de
  pricing. Tout signal candidat n'entre dans un modèle que s'il améliore le
  score en validation.
- **Aucun poids décrété.** Toute pondération (décroissance temporelle,
  conversion inter-ligues, priors) est apprise contre les résultats réels via
  le harnais, jamais choisie à la main.
- **Sources historiques** : API Bzzoiro d'abord (spike à faire) ; fallback
  **Transfermarkt** (buts/passes/minutes uniquement — pas de xG disponible) ;
  **FBref exclu**.
- **Champion/challenger.** Le modèle actuel (**Alpha**) reste seul aux
  commandes de la prod (calculateur, recos, autopilot) tant que le critère de
  bascule, défini à l'avance, n'est pas atteint par **Beta**.

## 3. Architecture

### 3.1 Registre de modèles et snapshots pré-match

- Un registre de modèles (`model_name` : "alpha", "beta", extensible "gamma"…).
- Alpha = moteur actuel `team_xg.py`, **strictement inchangé**.
- Beta = module séparé (voir 3.3), même interface de sortie.
- Nouvelle table de snapshots : pour chaque (fixture, joueur, marché,
  `model_name`), la probabilité et la cote EV0 **figées au coup d'envoi**
  (`as_of_utc`, flag `frozen`). C'est la seule matière admissible pour la
  comparaison — rien n'est recalculé a posteriori.
- La prod (site, recos, autopilot) ne lit que Alpha jusqu'à bascule.

### 3.2 Harnais d'évaluation — l'arbitre unique

Module qui prend des prédictions (n'importe quel modèle) et les règle contre
la réalité :

- **Settlement "avec sub"** : un ticket buteur est gagnant si le joueur nommé
  **ou son remplaçant** (chaîne de remplacements) a marqué. Vérité terrain =
  `match_events` (buteurs/passeurs) × incidents Bzzoiro (remplacements,
  minute d'entrée/sortie). Recoupe le bug #15 (settlement autopilot).
- **Métriques** : log-loss et Brier par marché ; courbes de calibration
  (fiabilité des probabilités annoncées) ; **delta apparié Alpha−Beta par
  ticket** (annule le bruit commun : calendrier, forme, blessures) ; CLV
  contre cotes de clôture Betclic/Unibet quand disponibles
  (`player_odds_snapshots`).
- **Deux terrains** :
  1. Rejeu complet 2025-26 (`bzz_player_match_stats` + `match_events`) —
     verdict immédiat sur gros volume, sert aussi à calibrer Beta.
  2. Accumulation des snapshots pré-match 2026-27 — confirmation en
     conditions réelles. Les issues buteur sont bruitées : ne pas conclure
     sur deux week-ends, laisser le volume s'accumuler.
- Sorties consommables par le dashboard de métriques (issue #13).

### 3.3 Modèle Beta — paramètres appris

- Consomme `bzz_player_match_stats` **match par match** (fini l'agrégat saison
  unique) avec décroissance temporelle exponentielle dont la **demi-vie est
  ajustée par le harnais** (minimisation du log-loss sur 2025-26). La
  frontière de saison disparaît comme concept.
- **Conversion inter-ligues** : coefficients estimés sur les joueurs ayant
  changé de championnat dans nos données, pas décrétés.
- **Priors sans-historique** : distribution observée des joueurs comparables
  (poste, rôle dans le 11). Jamais la valeur marchande.
- **Intensité du créneau projetée sur 90 minutes** (convention avec sub).
- Répartition de l'xG d'équipe entre coéquipiers et tireurs de penalty :
  réutilisent la mécanique d'Alpha en v1 (un pen ≈ 0.78 xG concentré sur un
  homme — l'attribution du tireur reste un gisement de précision identifié
  pour une v2).
- Admission de tout signal supplémentaire (forme 5 matchs, domicile/extérieur,
  xG concédé par poste adverse…) : uniquement sur amélioration mesurée en
  validation.

### 3.4 Transition de saison — socle indépendant du duel

À livrer quoi qu'il arrive avant la reprise :

- Débloquer `season="2025-2026"` et `SEASON_START_DATE` : saison courante et
  date de début pilotées par config (`app_config`), bascule automatique au
  rollover. L'agrégation écrit les lignes 2026-27 à côté des 2025-26.
- Vérifier que le worker suit les fixtures 2026-27 (mémoire : le backfill
  doit toujours recevoir explicitement `--season 2026-2027`).
- **Spike Bzzoiro (~1h)** : l'API expose-t-elle les agrégats de saisons
  passées et les ligues hors périmètre ? Trois issues possibles :
  1. Agrégats saison historiques disponibles → ingestion directe dans
     `bzz_player_season_stats`, coût minime.
  2. Seulement les matchs historiques → backfill **échelonné** (voir risques).
  3. Rien → fallback Transfermarkt pour les arrivants hors périmètre
     (buts/passes/minutes par saison et compétition ; prior dégradé sans xG,
     corrigé par coefficient inter-ligues appris).

## 4. Critère de bascule Alpha → Beta

Défini à l'avance pour que la décision soit mécanique :

1. Beta bat Alpha en log-loss apparié sur le rejeu 2025-26 complet, **et**
2. Beta reste devant en log-loss apparié cumulé sur 2026-27 après un nombre
   de journées fixé au lot 4 (à calibrer selon la variance observée — ordre
   de grandeur : plusieurs semaines de matchs, pas deux week-ends).

Tant que les deux conditions ne sont pas réunies, Alpha reste champion.

## 5. Lots

| Lot | Contenu | Fenêtre |
|---|---|---|
| **1 — Fondations** | Rollover saison configurable + spike Bzzoiro + registre `model_name` + table snapshots pré-match | Court, en premier |
| **2 — Le juge** | Harnais + settlement avec-sub + rejeu d'Alpha sur 2025-26 → **baseline chiffrée d'Alpha** (inconnue à ce jour) | Avant la reprise |
| **3 — Le challenger** | Beta v1 + calibration (demi-vie, coefs inter-ligues, priors) sur 2025-26 + verdict backtest apparié | Peut mordre sur début août (Alpha reste aux commandes) |
| **4 — Le duel 2026-27** | Double pricing pré-match systématique, dashboard (#13), application du critère de bascule | En continu dès la reprise |

## 6. Risques et parades

- **Quota API Bzzoiro** si backfill historique volumineux (option 2 du spike :
  ~2 200 matchs × 6 ligues pour 2024-25) → **échelonner** : ligue par ligue,
  par tranches journalières sous le quota, en commençant par les ligues à plus
  fort volume de paris. Décision d'échelonnement au vu des résultats du spike.
- **Qualité des données de remplacements** dans les incidents Bzzoiro
  (nécessaires au settlement avec-sub) → audit d'un échantillon au lot 2 ;
  si lacunes, croiser avec `match_events` existants.
- **Calendrier** : lots 1-2 impérativement avant la reprise ; le lot 3 peut
  glisser sans risque puisque Alpha reste seul en prod.
- **Bruit du duel live** : mitigé par l'appariement par ticket et par le
  verdict backtest préalable ; le critère de bascule exige la concordance des
  deux terrains.

## 7. Hors périmètre

- Pricing ou pari in-play (exclu par principe).
- Scraping FBref (exclu), scraping Winamax (abandonné le 11/07, ne pas
  reproposer).
- Refonte de la répartition d'xG d'équipe et de l'attribution des tireurs de
  penalty (v2 potentielle de Beta, après verdict v1).
- Modification d'Alpha (gelé en l'état pour servir de référence).
