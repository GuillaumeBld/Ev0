# GitHub Actions Auto-Settle Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Automatiser complètement le settlement des paris via un workflow GitHub Actions qui tourne toutes les 30 min, fetche les rosters Understat et importe les minutes jouées en DB.

**Architecture:** (1) `get_pending_fixtures.py` tourne dans le container backend et retourne les fixtures avec des paris non-settlés sans PlayerMatchMinutes. (2) `fetch_understat_rosters.py` est étendu avec un mode `--fixtures` ciblé (ne fetche que les matchs nécessaires). (3) Un workflow GitHub Actions orchestre tout : récupère les fixtures pendantes → fetch Understat → import → settle.

**Tech Stack:** GitHub Actions, Playwright (ubuntu-latest), SSH, Python 3.11, Docker

---

## Context

- **VPS:** `213.130.144.204`, user `root`, container `ev0-compose-z5hvqt-backend-1`
- **Understat est bloqué depuis le VPS** → fetch se fait sur les runners GitHub (non bloqués)
- **Sofascore est aussi bloqué** depuis VPS et local sans Playwright
- **Secrets GitHub à configurer** : `VPS_SSH_KEY` (clé privée), `VPS_HOST` (`213.130.144.204`)
- **Repo GitHub:** `GuillaumeBld/Ev0`
- **`import_understat_rosters.py`** contient déjà le `TEAM_NAME_MAP` pour normaliser les noms d'équipes — on le réutilise dans le mode `--fixtures` de fetch

---

## Task 1: `ops/get_pending_fixtures.py`

**Files:**
- Create: `ops/get_pending_fixtures.py`

Script qui tourne dans le backend container. Retourne en JSON les fixtures qui ont des recs `approved + result=NULL + fixture finished` et pas encore de `PlayerMatchMinutes`.

**Step 1: Créer le fichier**

```python
"""get_pending_fixtures.py — list fixtures needing Understat roster data for auto-settle.

Run inside the backend container:
    docker exec -e PYTHONPATH=/app <container> python3 /tmp/get_pending_fixtures.py

Outputs JSON list of {fixture_id, league, home, away, date} for fixtures that:
- Have at least one approved rec with result=NULL
- Fixture status is 'finished'
- No PlayerMatchMinutes exist yet for the fixture
"""

import asyncio
import json
from datetime import timezone

from sqlalchemy import select

from app.db import async_session
from app.models.fixtures import Fixture
from app.models.player_match_minutes import PlayerMatchMinutes
from app.models.recommendations import Recommendation


async def main():
    async with async_session() as db:
        # Get distinct finished fixtures with unsettled approved recs
        stmt = (
            select(Fixture)
            .join(Recommendation, Recommendation.fixture_id == Fixture.id)
            .where(
                Recommendation.status == "approved",
                Recommendation.result.is_(None),
                Fixture.status == "finished",
            )
            .distinct()
        )
        fixtures = (await db.execute(stmt)).scalars().all()

        result = []
        for fx in fixtures:
            # Skip if PlayerMatchMinutes already imported for this fixture
            pmm = await db.execute(
                select(PlayerMatchMinutes)
                .where(PlayerMatchMinutes.fixture_id == fx.id)
                .limit(1)
            )
            if pmm.scalar_one_or_none() is not None:
                continue

            result.append({
                "fixture_id": fx.id,
                "league": fx.league,
                "home": fx.home_team,
                "away": fx.away_team,
                "date": fx.kickoff_utc.astimezone(timezone.utc).strftime("%Y-%m-%d"),
            })

    print(json.dumps(result))


asyncio.run(main())
```

**Step 2: Vérifier la syntaxe**

```bash
cd /Users/yohan.resin/Ev0
python3 -c "import ast; ast.parse(open('ops/get_pending_fixtures.py').read()); print('OK')"
```

Expected: `OK`

**Step 3: Commit**

```bash
git add ops/get_pending_fixtures.py
git commit -m "ops: add get_pending_fixtures.py for GitHub Actions workflow"
```

---

## Task 2: Mode `--fixtures` dans `fetch_understat_rosters.py`

**Files:**
- Modify: `ops/fetch_understat_rosters.py`

Ajouter un mode ciblé : au lieu de fetcher toutes les ligues (~1291 matchs), on passe un fichier JSON `[{league, home, away, date}]` et on ne fetche que ces matchs-là.

**La logique de matching** : on charge le `datesData` de la ligue une seule fois, on cherche le match par `date + noms d'équipes normalisés` (même TEAM_NAME_MAP que dans `import_understat_rosters.py`).

**Step 1: Ajouter le TEAM_NAME_MAP et les helpers de matching en haut du fichier** (après les imports existants, avant `OUTPUT`)

```python
# Team name map: Understat names (lowercase) → DB names (lowercase)
# Used for --fixtures mode to correlate Understat matches with DB fixtures
_TEAM_NAME_MAP = {
    "manchester city": "man city",
    "manchester united": "man utd",
    "nottingham forest": "nott'm forest",
    "newcastle united": "newcastle",
    "wolverhampton wanderers": "wolves",
    "tottenham hotspur": "tottenham",
    "brighton & hove albion": "brighton",
    "west ham united": "west ham",
    "leicester city": "leicester",
    "ipswich town": "ipswich",
    "paris saint-germain": "psg",
    "olympique de marseille": "marseille",
    "olympique lyonnais": "lyon",
    "stade de reims": "reims",
    "stade brestois 29": "brest",
    "rc strasbourg alsace": "strasbourg",
    "stade rennais fc": "rennes",
    "fc nantes": "nantes",
    "ogc nice": "nice",
    "montpellier hsc": "montpellier",
    "rc lens": "lens",
    "toulouse fc": "toulouse",
    "borussia dortmund": "dortmund",
    "bayer leverkusen": "leverkusen",
    "eintracht frankfurt": "frankfurt",
    "vfb stuttgart": "stuttgart",
    "sc freiburg": "freiburg",
    "1. fc union berlin": "union berlin",
    "1. fsv mainz 05": "mainz",
    "fc augsburg": "augsburg",
    "1. fc heidenheim 1846": "heidenheim",
    "sv werder bremen": "werder bremen",
    "borussia mönchengladbach": "gladbach",
    "vfl wolfsburg": "wolfsburg",
    "vfl bochum": "bochum",
    "fc st. pauli": "st. pauli",
    "atletico madrid": "atlético madrid",
    "deportivo alaves": "alavés",
    "leganes": "leganés",
    "real valladolid": "valladolid",
    "ac milan": "milan",
    "hellas verona": "verona",
}


def _norm(name: str) -> str:
    return _TEAM_NAME_MAP.get(name.lower().strip(), name.lower().strip())


def _teams_match(us_home: str, us_away: str, db_home: str, db_away: str) -> bool:
    return _norm(us_home) == db_home.lower().strip() and _norm(us_away) == db_away.lower().strip()
```

**Step 2: Ajouter `--fixtures` à argparse dans `main()`** (après `--limit`)

```python
parser.add_argument(
    "--fixtures", type=str, default=None,
    help="JSON file with list of {fixture_id, league, home, away, date} — targeted mode"
)
```

**Step 3: Ajouter le mode ciblé dans `main()`** — remplacer le bloc `async with async_playwright()` par une version qui branche selon `args.fixtures`

Remplacer tout le bloc `async with async_playwright() as p:` par :

```python
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        if args.fixtures:
            # Targeted mode: only fetch specific fixtures
            with open(args.fixtures) as f:
                pending = json.load(f)

            if not pending:
                print("No pending fixtures.")
                await browser.close()
                with open(args.output, "w") as f:
                    json.dump({}, f)
                return

            # Group by league
            by_league: dict[str, list] = {}
            for fx in pending:
                by_league.setdefault(fx["league"], []).append(fx)

            for league_key, fixtures in by_league.items():
                league_cfg = LEAGUES.get(league_key)
                if not league_cfg:
                    print(f"  SKIP: unknown league '{league_key}'")
                    continue
                slug, year = league_cfg
                print(f"\n=== {league_key} — {len(fixtures)} fixture(s) to fetch ===")
                dates_data = await get_dates_data(page, slug, year)
                finished = {mid: m for mid, m in dates_data.items() if m.get("isResult")}

                for fx in fixtures:
                    # Find matching Understat match by date + team names
                    match_ref = None
                    for mid, m in finished.items():
                        us_home = m.get("h", {}).get("title", "")
                        us_away = m.get("a", {}).get("title", "")
                        dt_str = m.get("datetime", "")[:10]
                        if dt_str == fx["date"] and _teams_match(us_home, us_away, fx["home"], fx["away"]):
                            match_ref = (mid, us_home, us_away, dt_str)
                            break

                    if match_ref is None:
                        print(f"  SKIP: no Understat match for {fx['home']} vs {fx['away']} ({fx['date']})")
                        continue

                    mid, home, away, dt_str = match_ref
                    if mid in results:
                        continue

                    print(f"  {home} vs {away} ({dt_str}) id={mid}")
                    try:
                        rosters_data = await get_rosters_data(page, mid)
                        if not rosters_data:
                            print(f"    WARNING: empty rostersData for match {mid}")
                            continue
                        results[mid] = parse_roster(rosters_data, home, away, dt_str)
                        print(f"    → {len(results[mid]['players'])} players")
                    except Exception as e:
                        print(f"    ERROR: {e}")
                        continue

                    await asyncio.sleep(RATE_LIMIT)

        else:
            # Full mode: fetch all leagues (used for manual backfill)
            for league_key, (slug, year) in LEAGUES.items():
                print(f"\n=== {league_key} ({slug}/{year}) ===")
                dates_data = await get_dates_data(page, slug, year)

                finished = {
                    mid: m for mid, m in dates_data.items()
                    if m.get("isResult")
                }
                print(f"  {len(finished)} finished matches found")

                match_ids = list(finished.keys())
                if args.limit:
                    match_ids = match_ids[:args.limit]

                for i, mid in enumerate(match_ids):
                    m = finished[mid]
                    home = m.get("h", {}).get("title", "")
                    away = m.get("a", {}).get("title", "")
                    dt_str = m.get("datetime", "")[:10]

                    if mid in results:
                        continue

                    print(f"  [{i+1}/{len(match_ids)}] {home} vs {away} ({dt_str}) id={mid}")
                    try:
                        rosters_data = await get_rosters_data(page, mid)
                        if not rosters_data:
                            print(f"    WARNING: empty rostersData for match {mid}")
                            continue
                        results[mid] = parse_roster(rosters_data, home, away, dt_str)
                        print(f"    → {len(results[mid]['players'])} players")
                    except Exception as e:
                        print(f"    ERROR: {e}")
                        continue

                    await asyncio.sleep(RATE_LIMIT)

        await browser.close()
```

**Step 4: Vérifier syntaxe + tester le mode `--fixtures`**

```bash
cd /Users/yohan.resin/Ev0
python3 -c "import ast; ast.parse(open('ops/fetch_understat_rosters.py').read()); print('Syntax OK')"
```

Test rapide avec un fixture réel en JSON :
```bash
echo '[{"fixture_id": 1, "league": "ligue_1", "home": "Lyon", "away": "Paris FC", "date": "2026-03-09"}]' > /tmp/test_fixtures.json
python3 ops/fetch_understat_rosters.py /tmp/test_rosters.json --fixtures /tmp/test_fixtures.json
```

Expected: `=== ligue_1 — 1 fixture(s) to fetch ===` puis le match trouvé avec ~30 players.

**Step 5: Commit**

```bash
git add ops/fetch_understat_rosters.py
git commit -m "feat: add --fixtures targeted mode to fetch_understat_rosters.py"
```

---

## Task 3: `.github/workflows/auto-settle.yml`

**Files:**
- Create: `.github/workflows/auto-settle.yml`

**Step 1: Créer le répertoire et le fichier**

```bash
mkdir -p /Users/yohan.resin/Ev0/.github/workflows
```

**Step 2: Créer le workflow**

```yaml
name: Auto-Settle

on:
  schedule:
    - cron: '*/30 * * * *'   # toutes les 30 min
  workflow_dispatch:           # déclenchement manuel pour tests

jobs:
  settle:
    runs-on: ubuntu-latest
    timeout-minutes: 15

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install Playwright
        run: |
          pip install playwright
          playwright install chromium --with-deps

      - name: Configure SSH
        run: |
          mkdir -p ~/.ssh
          echo "${{ secrets.VPS_SSH_KEY }}" > ~/.ssh/vps_key
          chmod 600 ~/.ssh/vps_key
          ssh-keyscan -H ${{ secrets.VPS_HOST }} >> ~/.ssh/known_hosts

      - name: Get pending fixtures
        id: pending
        run: |
          scp -i ~/.ssh/vps_key ops/get_pending_fixtures.py root@${{ secrets.VPS_HOST }}:/tmp/get_pending_fixtures.py
          ssh -i ~/.ssh/vps_key root@${{ secrets.VPS_HOST }} \
            "docker cp /tmp/get_pending_fixtures.py ev0-compose-z5hvqt-backend-1:/tmp/ && \
             docker exec -e PYTHONPATH=/app ev0-compose-z5hvqt-backend-1 python3 /tmp/get_pending_fixtures.py" \
            > /tmp/pending_fixtures.json
          COUNT=$(python3 -c "import json; print(len(json.load(open('/tmp/pending_fixtures.json'))))")
          echo "count=$COUNT" >> $GITHUB_OUTPUT
          echo "Pending fixtures: $COUNT"
          cat /tmp/pending_fixtures.json

      - name: Fetch Understat rosters
        if: steps.pending.outputs.count != '0'
        run: |
          python3 ops/fetch_understat_rosters.py /tmp/understat_rosters.json --fixtures /tmp/pending_fixtures.json

      - name: Import rosters into DB
        if: steps.pending.outputs.count != '0'
        run: |
          scp -i ~/.ssh/vps_key /tmp/understat_rosters.json root@${{ secrets.VPS_HOST }}:/tmp/understat_rosters.json
          scp -i ~/.ssh/vps_key ops/import_understat_rosters.py root@${{ secrets.VPS_HOST }}:/tmp/import_understat_rosters.py
          ssh -i ~/.ssh/vps_key root@${{ secrets.VPS_HOST }} \
            "docker cp /tmp/understat_rosters.json ev0-compose-z5hvqt-backend-1:/tmp/ && \
             docker cp /tmp/import_understat_rosters.py ev0-compose-z5hvqt-backend-1:/tmp/ && \
             docker exec -e PYTHONPATH=/app ev0-compose-z5hvqt-backend-1 python3 /tmp/import_understat_rosters.py"

      - name: Trigger auto-settle
        if: steps.pending.outputs.count != '0'
        run: |
          ssh -i ~/.ssh/vps_key root@${{ secrets.VPS_HOST }} \
            "docker exec ev0-compose-z5hvqt-backend-1 curl -s -X POST http://localhost:8000/api/v1/history/settle"
```

**Step 3: Commit**

```bash
git add .github/workflows/auto-settle.yml
git commit -m "ci: add GitHub Actions auto-settle workflow (cron */30)"
```

---

## Task 4: Configurer les secrets SSH + tester

**Files:** aucun (configuration GitHub)

**Step 1: Générer une clé SSH dédiée pour GitHub Actions**

```bash
ssh-keygen -t ed25519 -C "github-actions-ev0" -f /tmp/github_actions_key -N ""
```

Expected: deux fichiers `/tmp/github_actions_key` (privée) et `/tmp/github_actions_key.pub` (publique)

**Step 2: Ajouter la clé publique au VPS**

```bash
ssh root@213.130.144.204 "echo '$(cat /tmp/github_actions_key.pub)' >> ~/.ssh/authorized_keys"
```

Vérifier :
```bash
ssh root@213.130.144.204 "tail -1 ~/.ssh/authorized_keys"
```

Expected: la clé publique github-actions-ev0

**Step 3: Ajouter les secrets au repo GitHub**

```bash
# Afficher la clé privée à copier dans GitHub Secrets
cat /tmp/github_actions_key
```

Aller sur : `https://github.com/GuillaumeBld/Ev0/settings/secrets/actions`

Ajouter :
- `VPS_SSH_KEY` → contenu de `/tmp/github_actions_key` (clé privée entière, incluant `-----BEGIN...-----END-----`)
- `VPS_HOST` → `213.130.144.204`

**Step 4: Tester via `workflow_dispatch`**

```bash
gh workflow run auto-settle.yml --repo GuillaumeBld/Ev0
```

Suivre les logs :
```bash
gh run list --repo GuillaumeBld/Ev0 --workflow auto-settle.yml --limit 1
gh run view --repo GuillaumeBld/Ev0 <run-id> --log
```

Expected:
- Step "Get pending fixtures" → `Pending fixtures: 0` (normal si aucun match finished non-settlé actuellement)
- Ou si des matchs sont en attente → les steps suivants s'exécutent et settlent les paris

**Step 5: Supprimer les clés temporaires**

```bash
rm /tmp/github_actions_key /tmp/github_actions_key.pub
```

---

## Notes de déploiement

- Le workflow **ne tourne pas** si `count == 0` (pas de fixtures en attente) → pas de coût inutile
- Le premier vrai test aura lieu lors du prochain match qui se termine avec un pari approuvé dessus
- En cas d'erreur dans le workflow, les paris restent `running` (aucun risque de mauvais settlement)
- Pour backfiller les anciens matchs : utiliser `fetch_understat_rosters.py` sans `--fixtures` en local (mode complet)
