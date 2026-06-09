# WC2026 — Compo type + UI pitch

**Date :** 2026-06-09
**Statut :** Approuvé

---

## Objectif

Permettre la saisie et la gestion des compositions attendues ("compo type") pour chaque nation qualifiée à la CDM 2026. Chaque nation a une compo de base plus des overrides par matchday. Ces compos alimentent le moteur de pricing (Spec 3) en fournissant les `expected_minutes` par joueur.

## Architecture

### Modèle de données

**`wc2026_expected_lineups`** — une compo par (nation, contexte)

```
id            SERIAL PK
nation        VARCHAR(60)   NOT NULL   -- "France", "Brésil" — doit matcher wc2026_squad_players.nation
context       VARCHAR(20)   NOT NULL   -- "default" | "matchday_1" | "matchday_2" | "matchday_3" | "r16" | "qf" | "sf" | "final"
formation     VARCHAR(10)   NOT NULL   -- "4-3-3", "4-2-3-1", "3-5-2"…
source        VARCHAR(20)   NOT NULL   -- "manual" | "rotowire"
created_at    TIMESTAMPTZ   NOT NULL   DEFAULT now()
updated_at    TIMESTAMPTZ   NOT NULL   DEFAULT now()
UNIQUE (nation, context)
```

**`wc2026_expected_lineup_players`** — joueurs dans une compo

```
id                SERIAL PK
lineup_id         INT        NOT NULL   REFERENCES wc2026_expected_lineups(id) ON DELETE CASCADE
player_name       VARCHAR(100) NOT NULL -- doit matcher wc2026_squad_players.player_name
position          VARCHAR(4) NOT NULL   -- GK / DEF / MID / FWD (pour le pricing engine)
line_index        SMALLINT   NOT NULL   -- ligne sur le terrain : 0=GK, 1=1ère ligne déf, 2=2ème ligne…
slot_index        SMALLINT   NOT NULL   -- ordre gauche→droite dans la ligne (0, 1, 2…)
is_starter        BOOLEAN    NOT NULL   DEFAULT true
role              VARCHAR(20) NOT NULL  -- "starter" | "sub_planned" | "sub_tactical" | "reserve"
expected_minutes  SMALLINT   NOT NULL   -- défaut selon rôle (voir ci-dessous)
UNIQUE (lineup_id, player_name)
```

**Pourquoi `line_index` :** `position` (GK/DEF/MID/FWD) est trop coarse pour le rendu pitch. Un 4-2-3-1 a 5 lignes — 2 lignes de milieux impossibles à distinguer sans `line_index`. Le pitch est rendu ligne par ligne : `line_index=0` (GK) en bas, lignes croissantes vers le haut jusqu'aux attaquants.

### Formations supportées

Le `formation` est une chaîne parsée de gauche à droite = du fond vers l'avant. Le GK est implicite (toujours `line_index=0`).

```python
FORMATIONS = {
    # ── 4 défenseurs ──────────────────────────────────────────────
    "4-4-2":     [4, 4, 2],        # lignes : DEF×4 | MID×4 | FWD×2
    "4-4-2d":    [4, 1, 2, 1, 2],  # diamond : DEF×4 | DM×1 | CM×2 | AM×1 | FWD×2
    "4-3-3":     [4, 3, 3],
    "4-2-3-1":   [4, 2, 3, 1],
    "4-3-2-1":   [4, 3, 2, 1],     # Christmas tree
    "4-5-1":     [4, 5, 1],
    "4-1-4-1":   [4, 1, 4, 1],
    "4-1-3-2":   [4, 1, 3, 2],
    "4-2-2-2":   [4, 2, 2, 2],
    "4-6-0":     [4, 6, 0],        # rare
    # ── 3 défenseurs ──────────────────────────────────────────────
    "3-5-2":     [3, 5, 2],
    "3-4-3":     [3, 4, 3],
    "3-4-2-1":   [3, 4, 2, 1],
    "3-3-4":     [3, 3, 4],
    "3-6-1":     [3, 6, 1],
    "3-4-1-2":   [3, 4, 1, 2],
    # ── 5 défenseurs (back 5) ─────────────────────────────────────
    "5-3-2":     [5, 3, 2],
    "5-4-1":     [5, 4, 1],
    "5-2-3":     [5, 2, 3],
    "5-2-2-1":   [5, 2, 2, 1],
    "5-1-2-2":   [5, 1, 2, 2],
    # ── 4-3-3 variantes ───────────────────────────────────────────
    "4-3-1-2":   [4, 3, 1, 2],
}
```

La somme des valeurs de chaque formation vaut toujours 10 (+ le GK implicite = 11).

**Parsing :** `"4-2-3-1".split("-")` → `[4, 2, 3, 1]`. Les joueurs de `line_index=1` sont les 4 DEF, `line_index=2` les 2 DM, `line_index=3` les 3 AM, `line_index=4` le CF. Les formations avec `d` (diamond) sont gérées comme cas spéciaux.

**Validation à la sauvegarde :** la somme des `line_index > 0` players = 10 exactement. Sinon erreur 422.

**Minutes par défaut selon rôle :**

| role | expected_minutes |
|---|---|
| starter | 85 |
| sub_planned | 30 |
| sub_tactical | 12 |
| reserve | 0 |

**Résolution au pricing :** pour le match N d'une nation, chercher `context="matchday_N"`. Si absent → fallback `context="default"`.

### API endpoints

```
GET  /api/v1/wc2026/lineups
     → liste des 48 nations avec statut compo (complete: bool, starters_count: int)

GET  /api/v1/wc2026/lineups/{nation}
     → { default: LineupOut, overrides: { matchday_1?: LineupOut, … } }

PUT  /api/v1/wc2026/lineups/{nation}/{context}
     Body: { formation: str, players: [{ player_name, position, line_index, slot_index, is_starter, role, expected_minutes }] }
     → upsert complet de la compo (remplace tous les joueurs existants)

POST /api/v1/wc2026/lineups/sync-rotowire
     → scraping ponctuel Rotowire, pré-peuple les compos manquantes (source="rotowire")
       Ne remplace PAS les compos source="manual"
```

### Rotowire seeder

Script `backend/app/ingestion/wc2026/sync_rotowire_lineups.py` :

- Fetch `https://www.rotowire.com/soccer/lineups.php?league=WOC`
- Parser HTML (BeautifulSoup) : extraire nom, position, numéro par équipe
- Pour chaque nation reconnue : upsert `wc2026_expected_lineups` avec `source="rotowire"`, `context="default"`
- Skip si une compo `source="manual"` existe déjà pour cette nation
- Mapping des noms d'équipe Rotowire → noms DB via `_TEAM_ALIASES` dans `odds_scheduler.py`

Utilisé une seule fois avant le début du tournoi pour pré-peupler. Pas de job récurrent.

### UI — Page `/dashboard/wc2026/lineups`

**Layout général :**

```
┌──────────────────────────────────────────────────┬────────────────────────┐
│  🇫🇷 France  [Compo type ▼]  [Sync Rotowire]     │  Effectif disponible   │
│  Formation : [4-3-3 ▼]                            │  ────────────────────  │
│  ┌────────────────────────────────────────────┐  │  GK                    │
│  │  [Dembélé 85'] [Mbappé 85'] [Kanté 85']   │  │  · Lloris              │
│  │     [Tchou. 85']  [Camav. 85'] [Rab. 85'] │  │                        │
│  │  [T.H 85'] [Konaté 85'] [Upam 85'] [Pav]  │  │  DEF                   │
│  │                 [Maignan 90']              │  │  · Koundé              │
│  │  ── Remplaçants ──────────────────────── │  │  · Theo Hernandez      │
│  │  [Giroud 30'] [Coman 15'] [Z-E. 12']      │  │  · Saliba              │
│  └────────────────────────────────────────────┘  │                        │
│  Tabs: [Buteur] [Passeur] [Décisif]               │  MID                   │
└──────────────────────────────────────────────────┴────────────────────────┘
```

**Sélecteur de formation :** dropdown groupé par famille de défenseurs :

```
4 défenseurs  → 4-4-2 | 4-3-3 | 4-2-3-1 | 4-3-2-1 | 4-5-1 | 4-1-4-1 | 4-1-3-2 | 4-4-2d
3 défenseurs  → 3-5-2 | 3-4-3 | 3-4-2-1 | 3-4-1-2 | 3-3-4
5 défenseurs  → 5-3-2 | 5-4-1 | 5-2-3 | 5-2-2-1
```

Quand la formation change, le terrain se réorganise immédiatement. Les joueurs déjà placés sont redistribués ligne par ligne en respectant le nouvel effectif par ligne. Les joueurs en excès (ligne supprimée ou réduite) sont renvoyés dans le panel droit.

**Composant jersey card :**

Chaque joueur est affiché comme une carte jersey sur le terrain :
- Numéro de maillot (depuis `wc2026_squad_players.shirt_number`)
- Nom du joueur (cliquable en mode édition → remplace par joueur du panel droit)
- Input numérique `expected_minutes` éditable inline (click → champ texte)
- Couleur maillot basée sur le flag de la nation (`flag_emoji`)

**Panel droit :**

Liste scrollable des joueurs du squad de 26 **non encore dans la compo** (ni titulaires ni remplaçants), groupée par GK / DEF / MID / FWD. Click sur un joueur :
- Si un slot est sélectionné sur le terrain → remplace ce joueur
- Sinon → ajoute comme remplaçant avec `role="reserve"`, `expected_minutes=0`

**Selector context :** dropdown en haut → "Compo type" | "Matchday 1" | "Matchday 2" | "Matchday 3". Chaque context est une compo indépendante avec un bouton "Copier depuis Compo type" pour partir de la base.

**Tabs Buteur / Passeur / Décisif :** une fois Spec 3 implémentée, les jersey cards affichent la cote ev0 calculée selon le tab actif. Avant Spec 3, affichent les `expected_minutes`.

### Fichiers à créer / modifier

| Fichier | Action |
|---|---|
| `backend/alembic/versions/036_wc2026_expected_lineups.py` | Migration : 2 nouvelles tables |
| `backend/app/models/wc2026_lineups.py` | SQLAlchemy models |
| `backend/app/api/wc2026_lineups.py` | Endpoints CRUD |
| `backend/app/ingestion/wc2026/sync_rotowire_lineups.py` | Scraper Rotowire |
| `backend/app/main.py` | Enregistrer le nouveau router |
| `frontend/src/app/dashboard/wc2026/lineups/page.tsx` | Page navigation nations |
| `frontend/src/components/wc2026/LineupPitchEditor.tsx` | Composant terrain éditable |
| `frontend/src/components/wc2026/JerseyCard.tsx` | Carte joueur avec minutes éditables |
| `frontend/src/components/wc2026/SquadPanel.tsx` | Panel droit joueurs disponibles |
| `frontend/src/lib/api/wc2026_lineups.ts` | Fonctions fetch API |
