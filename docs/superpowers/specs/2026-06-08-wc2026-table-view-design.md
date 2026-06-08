# CDM 2026 — Vue table joueurs avec filtres

## Résumé

Remplacer la vue CDM 2026 actuelle (sidebar nation + cartes) par une vue table paginée et filtrable, cohérente avec la vue championnats existante. La vue cartes reste accessible via un toggle. Les joueurs sans données Bzzoiro apparaissent avec des tirets (`—`).

---

## Contexte

### État actuel

- `dashboard/players/page.tsx` a un toggle `leagues ↔ cdm2026`
- Mode `cdm2026` affiche `WC2026View` : sidebar nation + grille de cartes joueurs
- Les stats de club (xG, buts, etc.) sont déjà enrichies via `GET /wc2026/squads?nation=X`
- La vue championnats a une table triable/filtrable avec search, pagination 50/page, tri serveur

### Objectif

Donner accès aux données CDM dans le même paradigme que les championnats : table sortable, filtres, recherche, comparaison cross-nations. La vue cartes reste disponible.

---

## Architecture

### Nouveau endpoint backend

`GET /api/v1/wc2026/players`

**Paramètres :**

| Param | Type | Défaut | Description |
|---|---|---|---|
| `nation` | string \| None | None | Filtre sur le nom de nation (ex. "France") |
| `position` | "GK"\|"DEF"\|"MID"\|"FWD"\|"" | "" | Filtre position |
| `search` | string | "" | Recherche sur `player_name` (ILIKE %search%) |
| `sort_by` | string | "xg_per90" | Champ de tri (voir liste ci-dessous) |
| `sort_order` | "asc"\|"desc" | "desc" | Ordre de tri |
| `page` | int ≥ 1 | 1 | Numéro de page |
| `page_size` | int | 50 | Taille de page (fixe, non exposé au client) |

**Champs triables (tri SQL) :**
`goals`, `assists`, `xg`, `xa`, `xg_per90`, `xa_per90`, `avg_rating`, `matches_played`, `minutes_played`, `saves`, `form_xg_5`, `form_goals_5`, `form_rating_5`

**Réponse :**
```json
{
  "players": [ ...WCPlayerOut... ],
  "total": 1218,
  "page": 1,
  "page_size": 50
}
```

`WCPlayerOut` est le modèle existant (17 champs). Un nouveau champ `nation: str` et `group_letter: str` sont ajoutés pour l'affichage dans la table.

**Logique SQL :** même CTE que `/wc2026/squads` (norm_bzz + agg_stats + form_stats), sans le `WHERE nation = :nation` obligatoire, avec :
- `WHERE (:nation IS NULL OR wsp.nation = :nation)`
- `AND (:position = '' OR wsp.position = :position)`
- `AND (:search = '' OR lower(wsp.player_name) LIKE lower('%' || :search || '%'))`
- `ORDER BY` dynamique via un mapping Python `_WC_SORT_FIELD_MAP` (dict `sort_by` → nom de colonne SQL) pour éviter toute injection — jamais d'interpolation directe du param dans la requête
- `LIMIT :page_size OFFSET (:page - 1) * :page_size`
- `total` calculé par une sous-requête `COUNT(*)` avec les mêmes filtres

### Modèle Pydantic étendu

```python
class WCPlayerOut(BaseModel):
    player_name: str
    nation: str | None = None          # nouveau — None dans /squads, renseigné dans /players
    group_letter: str | None = None    # nouveau — idem
    club: str | None
    position: str
    shirt_number: int | None
    # ... tous les champs stats existants (inchangés)

class WCPlayersPageOut(BaseModel):
    players: list[WCPlayerOut]
    total: int
    page: int
    page_size: int
```

**Note :** `WCPlayerOut` existant dans `/wc2026/squads` reste rétrocompatible — `nation` et `group_letter` sont déclarés avec `= None` comme valeur par défaut. Le endpoint `/squads` ne les renseigne pas (la nation est déjà dans `WCSquadOut.nation`). Le endpoint `/players` les renseigne toujours.

---

## Frontend

### Nouveau composant `WC2026TableView`

Fichier : `frontend/src/components/players/WC2026TableView.tsx`

**Props :**
```typescript
interface Props {
  onSwitchToCards: (nation: string | null) => void
}
```

**État interne :**
```typescript
const [nation, setNation] = useState<string | null>(null)
const [position, setPosition] = useState<'' | 'GK' | 'DEF' | 'MID' | 'FWD'>('')
const [search, setSearch] = useState('')
const [sortBy, setSortBy] = useState('xg_per90')
const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc')
const [page, setPage] = useState(1)
const [data, setData] = useState<WCPlayersPage | null>(null)
const [loading, setLoading] = useState(false)
```

**Layout :**
```
[Nation ▾]  [Position ▾]  [🔍 Rechercher…]          [≡ Table] [⊞ Cartes]
──────────────────────────────────────────────────────────────────────────
Joueur         Nation      Club        Pos  Buts  PD  xG/90  xA/90  ★  Forme
──────────────────────────────────────────────────────────────────────────
K. Mbappé      🇫🇷 France   Real Madrid  FWD   39   8   1.09   0.22  7.5   3
M. Maignan     🇫🇷 France   AC Milan     GK     0   0    —      —    7.3   —
...
──────────────────────────────────────────────────────────────────────────
← Précédent                   Page 1 / 25                      Suivant →
```

- Valeurs nulles affichées `—`
- Colonnes masquées sur mobile (même responsive que vue championnats)
- Le dropdown Nation est alimenté par `GET /wc2026/nations` (déjà existant)
- Clic sur toggle `⊞ Cartes` appelle `onSwitchToCards(nation)` — passe la nation filtrée

### Modifications `dashboard/players/page.tsx`

- Le toggle actuel `[⚽ CDM 2026]` / `[Championnats]` est remplacé par deux boutons séparés dans la barre de navigation :
  ```
  [Championnats]  [CDM 2026]
  ```
- Quand mode `cdm2026` : affiche `WC2026TableView` (et non plus `WC2026View` directement)
- `WC2026TableView` reçoit `onSwitchToCards` qui bascule sur `WC2026View` avec la nation présélectionnée
- Le mode carte CDM redevient accessible via le toggle dans `WC2026TableView`

### Types TypeScript (`frontend/src/lib/api.ts`)

```typescript
export interface WCPlayer {
  // champs existants +
  nation: string | null        // nouveau — null dans réponse /squads, renseigné dans /players
  group_letter: string | null  // nouveau — idem
}

export interface WCPlayersPage {
  players: WCPlayer[]
  total: number
  page: number
  page_size: number
}
```

---

## Comportement des données manquantes

Les joueurs sans correspondance Bzzoiro (environ 20% des 1218) sont toujours inclus dans la réponse avec tous les champs stats à `null`. Le frontend affiche `—` pour chaque valeur null. Ces joueurs apparaissent en bas quand le tri est sur une stat numérique (NULLS LAST).

---

## Ce qui ne change pas

- `GET /wc2026/squads?nation=X` : endpoint inchangé, utilisé par `WC2026View` (mode cartes)
- `WC2026View` : composant inchangé
- `WC2026TableView` réutilise les types `WCPlayer` étendus avec `nation`/`group_letter`
- La logique de normalisation des noms (unaccent CTE) est identique

---

## Ce qui est hors scope

- Filtrage par groupe (A, B, C…) — la nation suffit
- Comparaison côte-à-côte de joueurs
- Stats de tournoi CDM (disponibles une fois le tournoi commencé)
- Export CSV / partage de vue filtrée
