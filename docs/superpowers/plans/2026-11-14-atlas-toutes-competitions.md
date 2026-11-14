# Atlas : toutes les compétitions dont on a les données

Date : 14/11/2026. Demande : « on ajoute tout » — compétitions Bzzoiro non suivies
+ championnats du portail CSV de Malo (`maloelpipo/FootballDashboard`), avec
pays / clubs / championnats dans le menu de l'Atlas.

## Point de départ
- Registre Atlas (`league_registry.py` + table `leagues`, migration 060) : 57 compétitions.
- Bzzoiro en propose 103 → **46 absentes**, dont 41 avec des matchs 2026 :
  - 20 championnats : Autriche (96), Challenger Pro (97), Brasileirão B (34), Chili (111),
    Équateur (112), League One/Two (86/87), National League (91), Ligue 3 (105),
    Eerste Divisie (108), Nigeria (28), Pérou (113), Slovaquie (109), Superettan (106),
    1. Lig (107), Girabola (98), NPL Queensland (70), USL Championship (57),
    Liga 3 PT (82), Campeonato de Portugal (95).
  - 2 féminins : Liga F (36), NWSL (72).
  - 15 coupes nationales : FA Cup 39, Carabao 40, Coupe de France 44, DFB-Pokal 43,
    Coppa Italia 42, Copa del Rey 41, Copa do Brasil 35, Copa Colombia 81, Suomen Cup 56,
    Emperor Cup 51, Puchar Polski 46, Taça da Liga 93, Taça de Portugal 92,
    Coupe de Tunisie 48, Türkiye Kupası 110.
  - Sélections : CAN (30), Coupe d'Asie (68), Euro U19 (71), matchs amicaux (31) ;
    vides aujourd'hui : qualifs CM CAF (60), AFC (61), Gold Cup (69).
  - Clubs internationaux : Ligue des champions CAF (29), amicaux de clubs (79).
- Portail : 78 championnats, dont **31 inconnus de Bzzoiro** : Albanie, Algérie,
  Azerbaïdjan, Bosnie, Canada, Primera Federación I/II, Égypte, Estonie, Géorgie,
  Irlande, Islande, Israël, Croatie, 3. Liga, Lettonie, Lituanie, Luxembourg, Malte,
  Irlande du Nord, Paraguay, Russie, Serbie, Afrique du Sud, Slovénie, Émirats,
  Ukraine, Hongrie, Uruguay, Pays de Galles, Chypre.

## Pièges relevés (à neutraliser)
1. `sync_fixtures_from_bzz.py:50-60` mappe les ids 34/35 sur Ligue 1 / Bundesliga
   (espace externe) : Brasileirão B (34) et Copa do Brasil (35) créeraient des
   matchs de paris fantômes → filtrer ce mapping sur l'espace externe uniquement.
2. `rebuild()` est tout-ou-rien : un effectif hors norme sur UNE ligue bloque toutes
   les autres → vérifier chaque nouveau championnat seul avant de l'intégrer.
3. Repli par nom (`cle_club`) : un club féminin « Real Madrid » écraserait la ligne
   du club masculin → kind `women`, rattachement par identifiant Bzzoiro uniquement.
4. `/atlas/geo` ignore les kinds inconnus et les pays absents de `_COUNTRY_MAP`
   → nouveaux kinds `cup` / `women`, pays ajoutés (Nigeria, Angola, Afrique…).
5. `career_blend.py` additionne toutes les compétitions : amicaux (31, 79) exclus
   des taux par 90 (sinon les cotes buteur bougent).
6. Volume d'appels : stats joueurs par match sur ~2 000 matchs de plus par saison
   (forfait Bzzoiro illimité, OK) ; coupes et amicaux au rythme quotidien.

## Phase 1 — compétitions Bzzoiro (46)
1. Registre : nouveaux kinds `cup`, `women` ; invariants recalculés (taille, rangs).
   Effectifs attendus pris de Bzzoiro (saison en cours), vérifiés un par un.
2. Migration 066 : insère les 46 lignes `leagues`.
3. Garde-fou fixtures 34/35 ; amicaux exclus de `career_blend`.
4. Clubs : `rebuild` ligue par ligue (dry-run, puis écriture) ; féminines par id.
5. Atlas : kinds `cup`/`women` dans le menu du pays, pays/continents ajoutés,
   organisateurs CAF/AFC, sélections CAN / Coupe d'Asie dans les pays-sélections.
6. Transfermarkt : codes des nouveaux championnats dans `TM_COMPETITION_CODES`
   (synchro des effectifs → tm_id → carrières).
7. Tests, PR, fusion, rattrapage (événements, joueurs, stats, effectifs, carrières).

## Phase 2 — championnats du portail inconnus de Bzzoiro (31)
1. Ids de ligue réservés (9001+), kind `domestic_1`, pays.
2. Clubs : club TM du portail ↔ équipe Bzzoiro par vote majoritaire des joueurs
   (nom + âge) parmi les 68 000 joueurs Bzzoiro hors référentiel.
3. Canonical teams créées avec `bzz_team_id` + `transfermarkt_club_id`, puis la
   machinerie existante (effectifs, tm_id, carrières du portail) fait le reste.
4. Clubs sans équivalent Bzzoiro : listés, non créés (pas de joueurs à afficher).
