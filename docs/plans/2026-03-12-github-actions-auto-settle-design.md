# GitHub Actions Auto-Settle Design

**Date:** 2026-03-12

## Goal

Automatiser complètement le settlement des paris approuvés dans l'heure suivant la fin d'un match, sans intervention manuelle, en fetchant les minutes jouées depuis Understat via Playwright sur les serveurs GitHub Actions.

## Architecture

```
[GitHub Actions cron */30 * * * *]
        │
        ▼
1. SSH → VPS : récupère les fixtures non-settlées
   (approved recs + result=NULL + fixture finished + pas de PlayerMatchMinutes)
        │
        ▼
2. Fetch Understat rosters via Playwright (GitHub runner, non-bloqué)
   — seulement les N matchs concernés (pas toutes les ligues)
   — /tmp/understat_rosters.json
        │
        ▼
3. SSH → VPS : scp JSON + docker exec import_understat_rosters.py
        │
        ▼
4. SSH → VPS : curl POST /api/v1/history/settle
        │
        ▼
[Paris settlés VOID/WON/LOST]
```

## Composants

### `.github/workflows/auto-settle.yml`
- Trigger : `schedule: cron '*/30 * * * *'` + `workflow_dispatch` (manuel)
- Runner : `ubuntu-latest`
- Steps : checkout → Python + Playwright → get-fixtures (SSH) → fetch-rosters → import+settle (SSH)
- Secrets : `VPS_SSH_KEY`, `VPS_HOST`

### `ops/fetch_understat_rosters.py` (modification)
- Ajouter `--fixtures fixtures.json` : lit une liste de `{league, home, away, date}` au lieu de fetcher toutes les ligues
- Si `--fixtures` non fourni : comportement actuel (toutes les ligues, utile pour backfill)

### `ops/get_pending_fixtures.py` (nouveau)
- Script qui tourne dans le backend container
- Retourne en JSON les fixtures qui ont des recs non-settlées ET pas encore de PlayerMatchMinutes
- Output : `[{league, home, away, date, understat_slug}]`

## Secrets GitHub à configurer

| Secret | Valeur |
|--------|--------|
| `VPS_SSH_KEY` | Clé privée SSH (root@213.130.144.204) |
| `VPS_HOST` | `213.130.144.204` |

## Timing estimé

- Cron : toutes les 30 min
- Récupération fixtures : ~5s
- Fetch Understat (N matchs) : N × 2s + overhead Playwright ~30s → <2 min pour 30 matchs
- Import + settle : ~10s
- **Total : ~3 min** → settlement dans les ~33 min après fin de match

## Ce qui ne change pas

- `auto_settle.py` — inchangé
- `import_understat_rosters.py` — inchangé
- Worker APScheduler job — inchangé (backup si GitHub Actions rate un cycle)
