# Sanctuaire data/odds — plan d'implémentation

> **Pour les workers agentiques :** SUB-SKILL requise — `superpowers:subagent-driven-development` (recommandé) ou `superpowers:executing-plans`, tâche par tâche. Les étapes utilisent des cases `- [ ]`.

**Goal :** rendre la bibliothèque des xG consultable — une page qui montre, pour chaque match, ce que le marché disait à l'ouverture et juste avant le coup d'envoi.

**Architecture :** un routeur FastAPI en lecture seule lit `team_xg_estimates`, regroupe les deux phases par match, calcule l'amplitude du mouvement et applique quatre filtres ; une page Next.js affiche une carte par match, clôture en jaune et ouverture en bleu.

**Tech Stack :** FastAPI, SQLAlchemy 2 async, Pydantic ; Next.js 14 (App Router, `'use client'`), TanStack Query, Tailwind avec tokens CSS maison ; pytest + pytest-asyncio (`asyncio_mode = "auto"`).

## Global Constraints

- Spec de référence : `docs/superpowers/specs/2026-08-21-sanctuaire-data-odds-design.md`.
- Tests depuis `backend/` : `cd backend && uv run pytest …`. `asyncio_mode = "auto"` — jamais de `@pytest.mark.asyncio`.
- Commentaires et docstrings du code Python de production en français **sans accents** (convention du dépôt). Le TSX et les textes affichés à l'écran portent les accents normalement.
- **La page ne calcule rien d'autre que l'amplitude.** Elle lit `team_xg_estimates`, point. Aucun accès aux snapshots de cotes, aucun recalcul de λ.
- **Bleu = ouverture, jaune = clôture.** Ces deux couleurs ne servent à rien d'autre sur la page.
- **Le nul n'a jamais de xG** : c'est une issue, pas une équipe.
- **Amplitude = le plus grand mouvement relatif parmi les trois cotes du 1X2**, jamais la moyenne.
- Choisir un seuil d'amplitude **force** l'état « avec clôture », et la page le dit.
- Le dépôt possède un système de couleurs en tokens (`--ev-bg`, `--ev-t1`…`--ev-t5`, `--ev-bd`, `--ev-pos/neg/warn`) défini dans `frontend/src/app/globals.css`, avec thèmes sombre et clair. **Ne jamais écrire une couleur en dur** : passer par un token.

## Structure des fichiers

| Fichier | Responsabilité | Tâche |
|---|---|---|
| `backend/app/api/sanctuary.py` | routeur lecture seule + filtres | 1 |
| `backend/app/main.py` | enregistrement du routeur | 1 |
| `backend/tests/api/test_sanctuary_api.py` | amplitude + filtres | 1 |
| `frontend/src/app/globals.css` | tokens `--ev-open` / `--ev-close` | 2 |
| `frontend/tailwind.config.ts` | exposition des deux tokens | 2 |
| `frontend/src/app/dashboard/sanctuaire/page.tsx` | la page | 2 |
| `frontend/src/components/Sidebar.tsx` | entrée de menu | 2 |

---

### Task 1 : Point d'accès et filtres

**Files:**
- Create: `backend/app/api/sanctuary.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/api/test_sanctuary_api.py`

**Interfaces:**
- Consumes: `TeamXgEstimate` (`app/models/team_xg.py`) avec les champs `fixture_id`, `phase` (`"opening"` | `"closing"`), `as_of_utc`, `lambda_home`, `lambda_away`, `odds` (JSONB `{"h2h": {...}, "totals": {...}}`) ; `Fixture` avec `id`, `home_team`, `away_team`, `league`, `kickoff_utc`.
- Produces :
  - `max_move_pct(opening_odds: dict | None, closing_odds: dict | None) -> float | None`
  - `GET /api/v1/sanctuary/matches` — paramètres `team`, `league`, `with_closing`, `min_move`
  - `GET /api/v1/sanctuary/leagues` — ligues présentes dans la bibliothèque

**Contexte.** `team_xg_estimates` n'a aujourd'hui **aucun accès en lecture** — la seule mention de la table dans l'API est une suppression. C'est ce qui rend la bibliothèque invisible.

Deux filtres se traduisent en SQL (équipe, compétition) ; les deux autres exigent d'avoir regroupé les phases par match, donc s'appliquent en Python après regroupement. C'est assumé : la table compte 56 lignes, et la pagination est hors périmètre.

- [ ] **Étape 1 : écrire les tests de l'amplitude**

Créer `backend/tests/api/test_sanctuary_api.py` :

```python
"""Sanctuaire : amplitude du mouvement et filtres de la bibliotheque."""
from app.api.sanctuary import max_move_pct

OUV = {"h2h": {"home": 2.29, "draw": 3.10, "away": 3.73}}
CLO = {"h2h": {"home": 2.52, "draw": 3.13, "away": 3.18}}


def test_retient_le_plus_grand_mouvement_pas_la_moyenne():
    """Rayo-Alaves reel : dom +10,0 %, nul +1,0 %, ext -14,7 %.
    Le maximum est 14,7 -- une moyenne diluerait le signal."""
    m = max_move_pct(OUV, CLO)
    assert m == round(abs(3.18 - 3.73) / 3.73 * 100, 2)
    assert m > 14.0 and m < 15.0


def test_un_seul_cote_qui_decroche_suffit():
    """Deux issues immobiles, une qui bouge fort : le match doit ressortir."""
    ouv = {"h2h": {"home": 2.00, "draw": 3.00, "away": 4.00}}
    clo = {"h2h": {"home": 2.00, "draw": 3.00, "away": 6.00}}
    assert max_move_pct(ouv, clo) == 50.0


def test_aucun_mouvement_donne_zero():
    assert max_move_pct(OUV, dict(OUV)) == 0.0


def test_sans_cloture_pas_d_amplitude():
    assert max_move_pct(OUV, None) is None
    assert max_move_pct(None, CLO) is None
    assert max_move_pct(None, None) is None


def test_h2h_incomplet_donne_none():
    """On ne devine pas : une issue manquante rend le calcul impossible."""
    assert max_move_pct({"h2h": {"home": 2.0}}, CLO) is None
    assert max_move_pct({"totals": {"over_2.5": 2.0}}, CLO) is None


def test_cote_nulle_ne_fait_pas_exploser():
    """Une cote a zero en base serait aberrante, mais ne doit pas lever."""
    assert max_move_pct({"h2h": {"home": 0, "draw": 3.0, "away": 4.0}}, CLO) is None
```

- [ ] **Étape 2 : lancer les tests, vérifier qu'ils échouent**

```bash
cd backend && uv run pytest tests/api/test_sanctuary_api.py -q
```
Attendu : ÉCHEC (`ModuleNotFoundError: app.api.sanctuary`).

- [ ] **Étape 3 : écrire le routeur**

Créer `backend/app/api/sanctuary.py` :

```python
"""API lecture seule de la bibliotheque des xG.

La table team_xg_estimates archive, pour chaque match, l'ouverture et la
cloture : les lambdas des deux equipes et les cotes brutes qui les ont produits.
Elle n'avait aucun acces en lecture -- d'ou son invisibilite.

Cette API ne calcule rien d'autre que l'amplitude du mouvement. Elle ne touche
pas aux snapshots de cotes et ne recalcule aucun lambda : elle montre ce qui est
archive, ni plus ni moins.
"""
from __future__ import annotations

import re
import unicodedata

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.fixtures import Fixture
from app.models.team_xg import TeamXgEstimate

router = APIRouter(tags=["sanctuary"])

_ISSUES = ("home", "draw", "away")


def _fold(name: str) -> str:
    """Nom plie en minuscules sans accents, pour la recherche par equipe.

    Meme intention que app/ingestion/ps3838/anchor.py : 'Alaves' doit trouver
    'Deportivo Alaves' ecrit avec son accent.
    """
    extra = str.maketrans({
        "ø": "o", "æ": "ae", "å": "a",
        "ł": "l", "đ": "d", "ß": "ss",
    })
    s = (name or "").translate(extra)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def max_move_pct(opening_odds: dict | None, closing_odds: dict | None) -> float | None:
    """Plus grand mouvement relatif parmi les trois cotes du 1X2, en pourcent.

    On retient le MAXIMUM et non la moyenne : un seul camp qui decroche est
    precisement le signal recherche, une moyenne le diluerait avec les issues
    restees immobiles.

    None si l'une des deux phases manque, si le 1X2 est incomplet, ou si une
    cote d'ouverture est nulle (donnee aberrante -- on ne divise pas par zero).
    """
    if not opening_odds or not closing_odds:
        return None
    ouv, clo = opening_odds.get("h2h") or {}, closing_odds.get("h2h") or {}
    if not all(k in ouv and k in clo for k in _ISSUES):
        return None

    ecarts = []
    for k in _ISSUES:
        try:
            a, b = float(ouv[k]), float(clo[k])
        except (TypeError, ValueError):
            return None
        if a <= 0:
            return None
        ecarts.append(abs(b - a) / a * 100)
    return round(max(ecarts), 2)


class PhaseOut(BaseModel):
    as_of_utc: str
    odds: dict
    xg_home: float
    xg_away: float


class SanctuaryMatchOut(BaseModel):
    fixture_id: int
    home_team: str
    away_team: str
    league: str | None
    kickoff_utc: str
    opening: PhaseOut | None
    closing: PhaseOut | None
    max_move_pct: float | None


def _phase(est: TeamXgEstimate) -> PhaseOut:
    return PhaseOut(
        as_of_utc=est.as_of_utc.isoformat(),
        odds=est.odds or {},
        xg_home=est.lambda_home,
        xg_away=est.lambda_away,
    )


@router.get("/sanctuary/leagues", response_model=list[str])
async def list_leagues(db: AsyncSession = Depends(get_db)) -> list[str]:
    """Ligues reellement presentes dans la bibliotheque, pas une liste figee."""
    rows = (await db.execute(
        select(Fixture.league)
        .join(TeamXgEstimate, TeamXgEstimate.fixture_id == Fixture.id)
        .where(Fixture.league.isnot(None))
        .distinct()
        .order_by(Fixture.league)
    )).scalars().all()
    return [r for r in rows if r]


@router.get("/sanctuary/matches", response_model=list[SanctuaryMatchOut])
async def list_matches(
    team: str | None = Query(None, description="Nom d'equipe, les deux cotes"),
    league: str | None = Query(None),
    with_closing: bool = Query(False, description="Seulement les archives completes"),
    min_move: float | None = Query(None, ge=0, description="Amplitude minimale en %"),
    db: AsyncSession = Depends(get_db),
) -> list[SanctuaryMatchOut]:
    """Matchs archives, du plus recent au plus ancien.

    team et league filtrent en SQL ; with_closing et min_move s'appliquent apres
    regroupement des deux phases, qui est necessaire pour les evaluer.
    """
    stmt = (
        select(TeamXgEstimate, Fixture)
        .join(Fixture, Fixture.id == TeamXgEstimate.fixture_id)
        .order_by(Fixture.kickoff_utc.desc())
    )
    if league:
        stmt = stmt.where(Fixture.league == league)

    rows = (await db.execute(stmt)).all()

    # Un seuil d'amplitude n'a de sens que sur un match ayant sa cloture.
    exiger_cloture = with_closing or min_move is not None

    besoin = _fold(team) if team else None
    par_match: dict[int, dict] = {}
    ordre: list[int] = []
    for est, fx in rows:
        if besoin and besoin not in _fold(fx.home_team) and besoin not in _fold(fx.away_team):
            continue
        if fx.id not in par_match:
            par_match[fx.id] = {"fixture": fx, "opening": None, "closing": None}
            ordre.append(fx.id)
        par_match[fx.id][est.phase] = est

    sortie: list[SanctuaryMatchOut] = []
    for fid in ordre:
        bloc = par_match[fid]
        fx, ouv, clo = bloc["fixture"], bloc["opening"], bloc["closing"]
        if exiger_cloture and clo is None:
            continue
        move = max_move_pct(ouv.odds if ouv else None, clo.odds if clo else None)
        if min_move is not None and (move is None or move < min_move):
            continue
        sortie.append(SanctuaryMatchOut(
            fixture_id=fx.id,
            home_team=fx.home_team,
            away_team=fx.away_team,
            league=fx.league,
            kickoff_utc=fx.kickoff_utc.isoformat(),
            opening=_phase(ouv) if ouv else None,
            closing=_phase(clo) if clo else None,
            max_move_pct=move,
        ))
    return sortie
```

- [ ] **Étape 4 : enregistrer le routeur**

Dans `backend/app/main.py`, ajouter l'import à côté des autres routeurs puis, après
la ligne `app.include_router(lineups_api.router, ...)` :

```python
app.include_router(sanctuary.router, prefix="/api/v1", tags=["sanctuary"])
```

Ajouter `sanctuary` à l'import groupé des modules `app.api` en tête du fichier,
en respectant l'ordre alphabétique déjà en place.

- [ ] **Étape 5 : ajouter les tests des filtres**

Ajouter à la fin de `backend/tests/api/test_sanctuary_api.py`, en **remontant les
deux imports en tête du fichier** à côté de l'import existant (ne pas les laisser
au milieu) :

```python
# en tete du fichier, avec les autres imports :
#   import pytest
#   from app.api.sanctuary import _fold, max_move_pct


def test_recherche_equipe_insensible_aux_accents():
    assert "alaves" in _fold("Deportivo Alavés")
    assert "atletico" in _fold("Atlético Madrid")
    assert "bodo" in _fold("Bodø/Glimt")


def test_recherche_equipe_insensible_a_la_casse():
    assert _fold("ARSENAL") == _fold("Arsenal") == "arsenal"


def test_fold_ne_plante_pas_sur_vide():
    assert _fold("") == ""
    assert _fold(None) == ""


@pytest.mark.parametrize("with_closing,min_move,attendu", [
    (False, None, False),
    (True, None, True),
    (False, 10.0, True),
    (True, 10.0, True),
])
def test_un_seuil_d_amplitude_force_l_exigence_de_cloture(with_closing, min_move, attendu):
    """Regle metier : un match sans cloture n'a pas de mouvement.
    Cette table reproduit la condition du routeur."""
    exiger = with_closing or min_move is not None
    assert exiger is attendu
```

- [ ] **Étape 6 : lancer les tests**

```bash
cd backend && uv run pytest tests/api/test_sanctuary_api.py -v
uv run python -c "import app.main; print('main importe OK')"
uv run pytest tests/ -q
```
Attendu : SUCCÈS, `main importe OK`, aucune régression.

- [ ] **Étape 7 : commit**

```bash
git add backend/app/api/sanctuary.py backend/app/main.py backend/tests/api/test_sanctuary_api.py
git commit -m "feat(sanctuaire): API lecture seule de la bibliotheque xG + filtres"
```

---

### Task 2 : La page

**Files:**
- Modify: `frontend/src/app/globals.css`
- Modify: `frontend/tailwind.config.ts`
- Create: `frontend/src/app/dashboard/sanctuaire/page.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`

**Interfaces:**
- Consumes: `GET /api/v1/sanctuary/matches` (paramètres `team`, `league`, `with_closing`, `min_move`) et `GET /api/v1/sanctuary/leagues` (Task 1).
- Produces: la route `/dashboard/sanctuaire`.

**Contexte.** Le dépôt a son propre système de couleurs en tokens CSS, avec thèmes sombre et clair, dont les valeurs suivent la palette Tailwind (`#34d399` = emerald-400, `#fbbf24` = amber-400). Deux tokens manquent : le bleu de l'ouverture et le jaune de la clôture.

**Ne pas détourner `--ev-warn`** pour la clôture : il porte une sémantique d'alerte, et la clôture n'est pas un avertissement.

- [ ] **Étape 1 : ajouter les deux tokens**

Dans `frontend/src/app/globals.css`, dans le bloc du thème **sombre**, après
`--ev-warn: #fbbf24;` :

```css
  /* Code de lecture du Sanctuaire : bleu = ouverture, jaune = cloture. */
  --ev-open:  #60a5fa;
  --ev-close: #fbbf24;
```

Et dans le bloc du thème **clair**, après `--ev-warn: #d97706;` :

```css
  --ev-open:  #2563eb;
  --ev-close: #d97706;
```

Puis dans `frontend/tailwind.config.ts`, à côté de `'ev-warn'` :

```ts
        'ev-open':  'var(--ev-open)',
        'ev-close': 'var(--ev-close)',
```

- [ ] **Étape 2 : écrire la page**

Créer `frontend/src/app/dashboard/sanctuaire/page.tsx` :

```tsx
'use client'

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import { clsx } from 'clsx'

interface Phase {
  as_of_utc: string
  odds: { h2h?: Record<string, number>; totals?: Record<string, number> }
  xg_home: number
  xg_away: number
}

interface SanctuaryMatch {
  fixture_id: number
  home_team: string
  away_team: string
  league: string | null
  kickoff_utc: string
  opening: Phase | null
  closing: Phase | null
  max_move_pct: number | null
}

const SEUILS = [
  { label: 'Tous', value: '' },
  { label: '> 5 %', value: '5' },
  { label: '> 10 %', value: '10' },
  { label: '> 15 %', value: '15' },
]

async function fetchMatches(params: URLSearchParams): Promise<SanctuaryMatch[]> {
  const res = await fetch(`/api/v1/sanctuary/matches?${params}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

async function fetchLeagues(): Promise<string[]> {
  const res = await fetch('/api/v1/sanctuary/leagues')
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

function fmt(n: number | undefined | null, d = 2): string {
  return n === undefined || n === null ? '—' : n.toFixed(d)
}

function kickoff(iso: string): string {
  const dt = new Date(iso)
  return dt.toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

/** Une case : cloture en jaune au-dessus, ouverture en bleu en dessous. */
function Cell({
  close, open, align = 'left',
}: { close: number | null; open: number | null; align?: 'left' | 'center' | 'right' }) {
  return (
    <div className={clsx('flex flex-col gap-0.5',
      align === 'right' && 'items-end',
      align === 'center' && 'items-center')}>
      {close === null ? (
        <span className="font-mono text-sm text-ev-t4">en attente</span>
      ) : (
        <span className="font-mono tabular-nums text-xl font-semibold text-ev-close">
          {fmt(close)}
        </span>
      )}
      <span className="font-mono tabular-nums text-xs text-ev-open">{fmt(open)}</span>
    </div>
  )
}

export default function SanctuairePage() {
  const [team, setTeam] = useState('')
  const [league, setLeague] = useState('')
  const [withClosing, setWithClosing] = useState(false)
  const [minMove, setMinMove] = useState('')

  const params = new URLSearchParams()
  if (team) params.set('team', team)
  if (league) params.set('league', league)
  if (withClosing) params.set('with_closing', 'true')
  if (minMove) params.set('min_move', minMove)

  const { data: leagues } = useQuery({ queryKey: ['sanctuary-leagues'], queryFn: fetchLeagues })
  const { data, isLoading, error } = useQuery({
    queryKey: ['sanctuary', params.toString()],
    queryFn: () => fetchMatches(params),
  })

  const clotureForcee = minMove !== ''

  return (
    <div className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold text-ev-t1">Sanctuaire</h1>
        <p className="text-sm text-ev-t2 max-w-2xl">
          Ce que le marché disait à l&apos;ouverture de la ligne, et juste avant le coup
          d&apos;envoi. <span className="text-ev-open">Bleu&nbsp;: ouverture.</span>{' '}
          <span className="text-ev-close">Jaune&nbsp;: clôture.</span>
        </p>
      </header>

      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-ev-bd bg-ev-surface p-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-ev-t4" />
          <input
            value={team}
            onChange={(e) => setTeam(e.target.value)}
            placeholder="Équipe…"
            className="w-52 rounded-md border border-ev-bd bg-ev-surface2 py-2 pl-8 pr-3 text-sm text-ev-t1 placeholder:text-ev-t4 focus:outline-none focus:ring-2 focus:ring-ev-open"
          />
        </div>

        <select
          value={league}
          onChange={(e) => setLeague(e.target.value)}
          className="rounded-md border border-ev-bd bg-ev-surface2 px-3 py-2 text-sm text-ev-t1 focus:outline-none focus:ring-2 focus:ring-ev-open"
        >
          <option value="">Toutes compétitions</option>
          {(leagues ?? []).map((l) => <option key={l} value={l}>{l}</option>)}
        </select>

        <label className={clsx('flex items-center gap-2 text-sm',
          clotureForcee ? 'text-ev-t3' : 'text-ev-t2')}>
          <input
            type="checkbox"
            checked={withClosing || clotureForcee}
            disabled={clotureForcee}
            onChange={(e) => setWithClosing(e.target.checked)}
            className="accent-ev-close"
          />
          Avec clôture
        </label>

        <select
          value={minMove}
          onChange={(e) => setMinMove(e.target.value)}
          className="rounded-md border border-ev-bd bg-ev-surface2 px-3 py-2 text-sm text-ev-t1 focus:outline-none focus:ring-2 focus:ring-ev-open"
        >
          {SEUILS.map((s) => (
            <option key={s.value} value={s.value}>Mouvement&nbsp;: {s.label}</option>
          ))}
        </select>

        {clotureForcee && (
          <span className="text-xs text-ev-t3">
            Un seuil de mouvement n&apos;a de sens qu&apos;avec une clôture&nbsp;: le filtre est imposé.
          </span>
        )}
      </div>

      {isLoading && <p className="text-sm text-ev-t3">Chargement…</p>}
      {error && <p className="text-sm text-ev-neg">Impossible de charger la bibliothèque.</p>}
      {data && data.length === 0 && (
        <p className="text-sm text-ev-t3">Aucun match ne correspond à ces filtres.</p>
      )}

      <div className="space-y-3">
        {(data ?? []).map((m) => {
          const co = m.closing?.odds?.h2h ?? null
          const oo = m.opening?.odds?.h2h ?? null
          return (
            <article key={m.fixture_id}
              className="rounded-lg border border-ev-bd bg-ev-surface p-4">
              <div className="mb-4 flex items-baseline justify-between gap-4">
                <div className="grid flex-1 grid-cols-[1fr_84px_1fr] items-baseline gap-x-3">
                  <span className="font-medium text-ev-t1">{m.home_team}</span>
                  <span className="text-center text-[11px] uppercase tracking-wider text-ev-t4">nul</span>
                  <span className="text-right font-medium text-ev-t1">{m.away_team}</span>
                </div>
                <div className="flex shrink-0 items-baseline gap-3">
                  {m.max_move_pct !== null && (
                    <span className="font-mono text-xs text-ev-t3">{fmt(m.max_move_pct, 1)} %</span>
                  )}
                  <span className="font-mono text-xs text-ev-t4">{kickoff(m.kickoff_utc)}</span>
                </div>
              </div>

              <div className="border-t border-ev-bd2 pt-3">
                <p className="mb-2 text-[10px] uppercase tracking-wider text-ev-t4">Cote</p>
                <div className="grid grid-cols-[1fr_84px_1fr] gap-x-3">
                  <Cell close={co?.home ?? null} open={oo?.home ?? null} />
                  <Cell close={co?.draw ?? null} open={oo?.draw ?? null} align="center" />
                  <Cell close={co?.away ?? null} open={oo?.away ?? null} align="right" />
                </div>
              </div>

              <div className="mt-3 border-t border-ev-bd2 pt-3">
                <p className="mb-2 text-[10px] uppercase tracking-wider text-ev-t4">xG</p>
                <div className="grid grid-cols-[1fr_84px_1fr] gap-x-3">
                  <Cell close={m.closing?.xg_home ?? null} open={m.opening?.xg_home ?? null} />
                  {/* Le nul est une issue, pas une equipe : jamais de xG. */}
                  <div className="text-center font-mono text-lg text-ev-t4">—</div>
                  <Cell close={m.closing?.xg_away ?? null} open={m.opening?.xg_away ?? null} align="right" />
                </div>
              </div>
            </article>
          )
        })}
      </div>
    </div>
  )
}
```

- [ ] **Étape 3 : ajouter l'entrée de menu**

Dans `frontend/src/components/Sidebar.tsx`, section `label: 'Analyse'`, entre
l'entrée « Matchs » et l'entrée « Équipes » :

```tsx
      { name: 'Sanctuaire', href: '/dashboard/sanctuaire', icon: Library },
```

Ajouter `Library` à l'import depuis `lucide-react`, en respectant l'ordre déjà
en place dans ce fichier.

- [ ] **Étape 4 : vérifier la compilation**

```bash
cd frontend && npx tsc --noEmit
```
Attendu : aucune erreur.

- [ ] **Étape 5 : commit**

```bash
git add frontend/src/app/globals.css frontend/tailwind.config.ts \
        frontend/src/app/dashboard/sanctuaire/page.tsx frontend/src/components/Sidebar.tsx
git commit -m "feat(sanctuaire): page de consultation de la bibliotheque xG"
```

---

### Task 3 : Mise en service

**Files:**
- Modify: `docs/DEPLOYMENT.md`
- Vérification en production

**Interfaces:**
- Consumes: tout ce qui précède.
- Produces: rien de programmatique.

- [ ] **Étape 1 : documenter**

Dans `docs/DEPLOYMENT.md`, à la fin de la sous-section « Cotes archivées »,
ajouter :

```markdown
La bibliothèque est consultable sur `/dashboard/sanctuaire` — une carte par
match, la clôture en jaune et l'ouverture en bleu, avec quatre filtres : équipe,
compétition, présence de la clôture, et amplitude du mouvement.

L'amplitude est le **plus grand** mouvement relatif parmi les trois cotes du
1X2 : un seul camp qui décroche est le signal recherché, une moyenne le
diluerait. Choisir un seuil impose le filtre « avec clôture », un match sans
clôture n'ayant pas de mouvement.
```

- [ ] **Étape 2 : commit**

```bash
git add docs/DEPLOYMENT.md
git commit -m "docs: page Sanctuaire de consultation de la bibliotheque"
```

- [ ] **Étape 3 : déployer**

Le frontend est un conteneur distinct du backend.

```bash
cd /etc/dokploy/compose/ev0-compose-z5hvqt/code
git fetch origin --quiet && git reset --hard origin/main --quiet
docker compose -p ev0-compose-z5hvqt --env-file .env \
  up -d --build --no-deps --remove-orphans backend frontend
```

- [ ] **Étape 4 : vérifier le point d'accès**

```bash
docker exec ev0-compose-z5hvqt-backend-1 python -c "
import asyncio, json
from app.api.sanctuary import list_matches
from app.db import async_session

async def main():
    async with async_session() as s:
        rows = await list_matches(db=s)
        print(len(rows), 'matchs')
        if rows:
            m = rows[0]
            print(m.home_team, '-', m.away_team, '| mouvement:', m.max_move_pct)
asyncio.run(main())
"
```

Attendu : un nombre de matchs non nul, et un mouvement chiffré sur ceux qui ont
leur clôture.

- [ ] **Étape 5 : vérifier la page**

Ouvrir `https://ev0-213-130-144-204.sslip.io/dashboard/sanctuaire` et contrôler :

- une carte par match, la plus récente en haut ;
- la clôture en jaune, l'ouverture en bleu, sur les matchs joués ;
- « en attente » sur les matchs à venir, avec l'ouverture toujours visible ;
- la case du nul sans xG ;
- une recherche « alaves » trouve « Deportivo Alavés » ;
- choisir un seuil de mouvement coche et grise « Avec clôture », avec la mention
  explicative.

---

## Auto-revue

**Couverture de la spec.**

| Exigence | Tâche |
|---|---|
| Entrée « Sanctuaire » dans *Analyse*, route `/dashboard/sanctuaire` | 2 |
| Une carte par match, coup d'envoi décroissant | 1 (tri SQL) + 2 (rendu) |
| Clôture en jaune, ouverture en bleu | 2 (tokens dédiés) |
| Le nul n'a jamais de xG | 2 (case en dur) |
| Lecture exclusive de `team_xg_estimates` | 1 |
| Filtre équipe, deux côtés, sans accents | 1 (`_fold`) |
| Filtre compétition alimenté par la bibliothèque | 1 (`/sanctuary/leagues`) |
| Filtre état de l'archive | 1 |
| Filtre amplitude = **maximum** des trois mouvements | 1 (`max_move_pct`) |
| Un seuil force « avec clôture », et la page le dit | 1 (logique) + 2 (mention) |
| « en attente » quand la clôture manque | 2 |
| Match absent de la bibliothèque → absent de la page | 1 (jointure interne) |
| Documentation | 3 |

**Cohérence des noms.** `max_move_pct(opening_odds, closing_odds)` et `_fold(name)`
gardent ces signatures de la tâche 1 à la tâche 3. Les champs de
`SanctuaryMatchOut` correspondent exactement à l'interface `SanctuaryMatch` du
TSX. Les paramètres `team` / `league` / `with_closing` / `min_move` sont nommés
pareil du routeur à l'`URLSearchParams`.

**Points de vigilance pour l'implémenteur.**

- **Ne jamais écrire une couleur en dur** dans le TSX : le dépôt a des tokens et
  deux thèmes. Une couleur littérale casse le thème clair.
- `--ev-warn` **n'est pas** la couleur de clôture : il porte une sémantique
  d'alerte. Les deux tokens ajoutés sont dédiés.
- L'amplitude retient le **maximum**, pas la moyenne. Le test
  `test_retient_le_plus_grand_mouvement_pas_la_moyenne` verrouille ce point.
- Le filtre équipe se fait en Python après la requête, faute de pliage d'accents
  en SQL. Sur 56 lignes c'est sans conséquence ; à revoir si la table grossit
  fortement.
