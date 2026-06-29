# CDM 2026 — Filtre sélections officielles dans "Joueurs"

## Résumé

Ajouter un onglet "CDM 2026" dans la page `/joueurs` affichant les listes officielles des 48 équipes nationales qualifiées pour la Coupe du Monde 2026 (11 juin – 19 juillet 2026). Phase 1 : listes pures (nom + club + poste). Phase 2 (hors scope) : enrichissement avec stats de club.

---

## Architecture

### 1. DB — Nouvelle table `wc2026_squad_players`

```sql
CREATE TABLE wc2026_squad_players (
    id           SERIAL PRIMARY KEY,
    nation       VARCHAR(60) NOT NULL,   -- ex: "France", "Brésil"
    group_letter CHAR(1) NOT NULL,       -- "A" … "L"
    player_name  VARCHAR(100) NOT NULL,
    club         VARCHAR(100),
    position     VARCHAR(3) NOT NULL,    -- "GK", "DEF", "MID", "FWD"
    shirt_number SMALLINT,
    UNIQUE (nation, player_name)
);
```

**Pas de FK vers `bzz_players`** — la liaison arrive en phase 2.

---

### 2. Migration Alembic

`alembic/versions/033_wc2026_squads.py` — crée la table et indexe `(nation, group_letter)`.

---

### 3. Script de seed — `app/ingestion/wc2026/seed_squads.py`

- Récupère la page Wikipedia `2026_FIFA_World_Cup_squads` via `httpx`
- Parse les tableaux HTML (BeautifulSoup) : une ligne = un joueur avec pos/nom/club/nation/groupe
- Upsert via `INSERT … ON CONFLICT (nation, player_name) DO UPDATE SET club=…, position=…`
- CLI : `python -m app.ingestion.wc2026.seed_squads [--dry-run]`
- **Tourne une seule fois** (ou si une liste est mise à jour suite à blessure/remplacement)

---

### 4. API — `app/api/wc2026.py`

Nouveau router monté sous `/wc2026` :

| Endpoint | Paramètres | Réponse |
|---|---|---|
| `GET /wc2026/nations` | — | `[{ nation, group_letter, flag_emoji, player_count }]` — trié A→L |
| `GET /wc2026/squads` | `?nation=France` | `{ nation, group_letter, gk: [...], def: [...], mid: [...], fwd: [...] }` |

Chaque joueur : `{ player_name, club, position, shirt_number }`.

---

### 5. Frontend — Layout "sidebar + grille"

**Fichier :** `frontend/src/app/dashboard/players/page.tsx` — ajout d'un état `mode: 'leagues' | 'cdm2026'`.

Quand `mode === 'cdm2026'` :
- La table existante est masquée, les filtres liga/poste/recherche aussi
- Layout 2 colonnes :
  - **Colonne gauche (sidebar scrollable)** : liste des 48 nations groupées par poule A→L, icône drapeau + nom, nation active surlignée
  - **Colonne droite** : titre nation + nombre joueurs, puis 4 sections : 🧤 GARDIENS / 🛡 DÉFENSEURS / ⚙ MILIEUX / ⚽ ATTAQUANTS, chaque section affiche des cartes `nom + club`

**Nouveau composant :** `frontend/src/components/players/WC2026View.tsx`
- Prend `nations: WCNation[]` et `squad: WCSquad | null`
- Appelle `onSelectNation(nation: string)` pour changer la sélection

**Types TS :**
```ts
interface WCNation {
  nation: string
  group_letter: string
  flag_emoji: string
  player_count: number
}
interface WCPlayer {
  player_name: string
  club: string | null
  position: string
  shirt_number: number | null
}
interface WCSquad {
  nation: string
  group_letter: string
  gk: WCPlayer[]
  def: WCPlayer[]
  mid: WCPlayer[]
  fwd: WCPlayer[]
}
```

---

## Ce qui est hors scope (phase 2)

- Liaison `wc2026_squad_players` ↔ `bzz_players` (matching nom)
- Affichage des stats de club (xG, buts, passes décisives)
- Stats de tournoi depuis scoutingstats.ai (disponibles une fois le tournoi démarré)

---

## Ordre d'implémentation

1. Migration Alembic + modèle SQLAlchemy
2. Script seed (parse Wikipedia)
3. API endpoints
4. Frontend : onglet CDM + composant `WC2026View`
