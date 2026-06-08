# CDM 2026 — Enrichissement des stats joueurs

## Résumé

Enrichir les cartes joueurs de la vue CDM 2026 avec les stats de club saison 2025-2026 provenant de `bzz_player_season_stats`. Le lien entre `wc2026_squad_players` et `bzz_players` se fait par normalisation des noms (unaccent + lowercase + strip ponctuation), sans migration de schéma ni colonne FK supplémentaire.

---

## Contexte de données

- `bzz_players` : 91 785 joueurs avec `api_id`, `name`, `current_team_name`
- `bzz_player_season_stats` : 24 454 lignes pour la saison 2025-2026, plusieurs ligues par joueur
- `wc2026_squad_players` : 1 219 joueurs, `player_name` string, pas de FK
- Match exact par nom : **978/1218 joueurs** (80 %)
- Avec normalisation (unaccent + strip) : couverture attendue ~95 %+
- Stats disponibles : buts, passes déc., xG, xA, xG/90, xA/90, matchs, minutes, note, forme (5j)

---

## Architecture

### Normalisation des noms

Fonction PostgreSQL appliquée aux deux côtés du JOIN :

```sql
lower(regexp_replace(unaccent(name), '[^a-z0-9 ]', '', 'g'))
```

Exemples :
- `"Dembélé"` → `"dembele"`
- `"M'Bappé"` → `"mbappe"`
- `"Van Dijk"` → `"van dijk"`
- `"Müller"` → `"muller"`

Pré-requis : extension `unaccent` activée dans PostgreSQL (`CREATE EXTENSION IF NOT EXISTS unaccent`).

### Agrégation des stats

Pour chaque joueur, on somme **toutes les ligues** de la saison 2025-2026 :

| Champ de sortie | Source SQL |
|---|---|
| `matches_played` | `SUM(bss.matches_played)` |
| `minutes_played` | `SUM(bss.minutes_played)` |
| `goals` | `SUM(bss.goals)` |
| `assists` | `SUM(bss.goal_assist)` |
| `xg` | `SUM(bss.expected_goals)` |
| `xa` | `SUM(bss.expected_assists)` |
| `xg_per90` | `SUM(xG) / NULLIF(SUM(minutes), 0) * 90` |
| `xa_per90` | `SUM(xA) / NULLIF(SUM(minutes), 0) * 90` |
| `avg_rating` | `SUM(rating * matches) / NULLIF(SUM(matches), 0)` (moyenne pondérée) |
| `saves` | `SUM(bss.saves)` (GK uniquement, pour les autres = 0 → affiché null) |
| `goals_conceded` | `SUM(bss.goals_conceded)` (GK uniquement) |
| `form_goals_5` | `bss.form_goals_5` de la ligne avec `MAX(minutes_played)` |
| `form_xg_5` | idem |
| `form_rating_5` | idem |

Les champs `form_*` proviennent de la **ligne avec le plus de minutes** (compétition principale du joueur) car ils ne sont pas sommables.

### Endpoint modifié

`GET /wc2026/squads?nation=X` — même URL, même structure de réponse, contenu de chaque `WCPlayerOut` enrichi avec les stats.

---

## Backend : `app/api/wc2026.py`

### Nouveau `WCPlayerOut`

```python
class WCPlayerOut(BaseModel):
    player_name: str
    club: str | None
    position: str
    shirt_number: int | None
    # Stats (None si pas de données Bzzoiro)
    matches_played: int | None = None
    minutes_played: int | None = None
    goals: int | None = None
    assists: int | None = None
    xg: float | None = None
    xa: float | None = None
    xg_per90: float | None = None
    xa_per90: float | None = None
    avg_rating: float | None = None
    form_goals_5: int | None = None
    form_xg_5: float | None = None
    form_rating_5: float | None = None
    saves: int | None = None           # GK uniquement
    goals_conceded: int | None = None  # GK uniquement
```

### Requête SQL

La requête utilise deux CTEs :
1. `norm_bzz` — `bzz_players` avec `normalized_name` calculé
2. `agg_stats` — `bzz_player_season_stats` agrégé par `player_api_id`, saison 2025-2026
3. `form_stats` — ligne avec `MAX(minutes_played)` pour les champs `form_*`

Jointure : `wc2026_squad_players` LEFT JOIN `norm_bzz` ON `normalize(player_name) = normalize(name)` LEFT JOIN `agg_stats` ON `player_api_id`.

**Note :** En cas de doublons dans `bzz_players` (même nom normalisé, plusieurs `api_id`), on choisit le joueur avec le `current_team_name` le plus proche du `club` dans `wc2026_squad_players` — ou à défaut, l'`api_id` le plus bas (stable).

---

## Migration Alembic

Numéro de révision : `034_enable_unaccent.py`

```sql
CREATE EXTENSION IF NOT EXISTS unaccent;
```

Pas de modification de table — uniquement activation de l'extension.

---

## Frontend : `WCPlayer` type et `PlayerCard`

### Types TypeScript (`src/lib/api.ts`)

```typescript
interface WCPlayer {
  player_name: string
  club: string | null
  position: string
  shirt_number: number | null
  // Stats
  matches_played: number | null
  minutes_played: number | null
  goals: number | null
  assists: number | null
  xg: number | null
  xa: number | null
  xg_per90: number | null
  xa_per90: number | null
  avg_rating: number | null
  form_goals_5: number | null
  form_xg_5: number | null
  form_rating_5: number | null
  saves: number | null
  goals_conceded: number | null
}
```

### `PlayerCard` enrichie (`WC2026View.tsx`)

Layout de la carte :
```
┌──────────────────────────────────────────┐
│ Kylian Mbappé                            │
│ Real Madrid                              │
│ ⚽ 29  🅰 2  |  xG 20.4  xA 2.8         │  ← ligne 1 stats brutes (non-GK)
│ 23 matchs · xG/90 0.82 · ★ 7.1          │  ← ligne 2 contexte + note
│ Forme 5j: ⚽⚽⚽○⚽  xG 4.2             │  ← ligne 3 forme (optionnel)
└──────────────────────────────────────────┘
```

Pour les GK :
- Ligne 1 : `🧤 42 parades  ⛔ 18 buts encaissés`
- Ligne 2 : `23 matchs · ★ 6.8`

Les champs `null` ne sont pas affichés. Si aucune stat n'est disponible pour un joueur, la carte reste comme avant (nom + club uniquement, sans ligne stats).

---

## Tests

### Backend (`tests/api/test_wc2026_api.py`)

- `test_player_stats_aggregated_across_leagues` : mock avec 2 lignes de stats pour un joueur (2 ligues), vérifie que `goals = SUM`, `xg_per90 = SUM(xG)/SUM(min)*90`
- `test_player_not_found_stats_null` : joueur sans correspondance Bzzoiro → tous les champs stats sont `None`
- `test_name_normalization` : `_normalize_name("Dembélé") == _normalize_name("Dembele")`
- `test_gk_shows_saves_not_goals` : GK → champs `saves` et `goals_conceded` non-null, `xg` null (ou 0)

---

## Ce qui est hors scope

- Lier `wc2026_squad_players` à `bzz_players` par FK permanente (colonne `bzz_player_api_id`)
- Stats de tournoi CDM 2026 (disponibles une fois le tournoi commencé)
- Filtres/tris dans la vue par stats (ex. "top buteurs de la compétition")
