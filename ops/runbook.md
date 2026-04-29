# Runbook Ev0

---

## Vérifications quotidiennes

```bash
# Statut des conteneurs
docker compose -p ev0-compose-z5hvqt ps

# Health API
curl -s http://localhost:8000/api/v1/ready | python3 -m json.tool
curl -s http://localhost:8000/api/v1/data-quality | python3 -m json.tool

# Logs worker (dernières 100 lignes)
docker compose -p ev0-compose-z5hvqt logs --tail=100 worker
```

**Points à vérifier :**
- Tous les 6 conteneurs `Up` (`db`, `redis`, `backend`, `worker`, `frontend`, `db-backup`)
- `data-quality` → tous les `freshness` à `"fresh"` (< 24h)
- Aucune exception non capturée dans les logs worker
- Fixtures à venir dans les 48h présentes en DB
- Snapshots de cotes présents pour les matchs du jour

---

## Déploiement sécurisé (post-push GitHub)

> Ne jamais laisser Dokploy auto-déployer si des conteneurs tournent — risque de suppression d'image.

```bash
cd /etc/dokploy/compose/ev0-compose-z5hvqt/code

# Pull + build uniquement les services modifiés
docker compose -p ev0-compose-z5hvqt build backend worker

# Redémarrage sans rebuild
docker compose -p ev0-compose-z5hvqt --env-file .env up -d --no-build
```

---

## Déclenchement manuel des jobs

Pour forcer un job sans attendre son prochain cycle planifié, utiliser le worker en mode
one-shot depuis le conteneur :

```bash
docker compose -p ev0-compose-z5hvqt exec worker \
  python -c "import asyncio; from app.worker import <nom_du_job>; asyncio.run(<nom_du_job>())"
```

**Exemples fréquents :**

```bash
# Re-synchroniser les stats joueurs Bzzoiro (14 jours)
docker compose -p ev0-compose-z5hvqt exec worker \
  python -c "import asyncio; from app.worker import job_sync_bzzoiro_player_stats; asyncio.run(job_sync_bzzoiro_player_stats())"

# Re-lancer le gap-fill StatsHub stats
docker compose -p ev0-compose-z5hvqt exec worker \
  python -c "import asyncio; from app.worker import job_sync_statshub_gap_fill; asyncio.run(job_sync_statshub_gap_fill())"

# Re-lancer le gap-fill StatsHub full-season
docker compose -p ev0-compose-z5hvqt exec worker \
  python -c "import asyncio; from app.worker import job_sync_statshub_full_season; asyncio.run(job_sync_statshub_full_season())"

# Recalculer les agrégats saison
docker compose -p ev0-compose-z5hvqt exec worker \
  python -c "import asyncio; from app.worker import job_aggregate_season_stats; asyncio.run(job_aggregate_season_stats())"

# Re-synchroniser les fixtures depuis Bzzoiro
docker compose -p ev0-compose-z5hvqt exec worker \
  python -c "import asyncio; from app.worker import job_sync_fixtures; asyncio.run(job_sync_fixtures())"

# Regénérer les recommandations
docker compose -p ev0-compose-z5hvqt exec worker \
  python -c "import asyncio; from app.worker import job_generate_recommendations; asyncio.run(job_generate_recommendations())"
```

---

## Incident : cotes indisponibles (Betclic / Unibet)

**Symptôme :** Le calculateur affiche "No market odds available". Les recommandations ont
`fair_prob = null`.

**Diagnostic :**
```bash
# Vérifier les derniers snapshots de cotes
docker compose -p ev0-compose-z5hvqt exec db \
  psql -U ev0 -c "SELECT source, COUNT(*), MAX(scraped_at) FROM match_odds_snapshots GROUP BY source ORDER BY MAX(scraped_at) DESC;"

# Logs du scheduler de cotes
docker compose -p ev0-compose-z5hvqt logs worker | grep -i "odds_scheduler\|betclic\|unibet" | tail -30
```

**Actions :**
1. Tester l'accès réseau depuis le VPS vers Betclic/Unibet.
2. Si une source est bloquée : l'autre prend le relais automatiquement (pas d'action requise).
3. Si les deux sont bloquées : les snapshots périmés (> 30min) font passer le calculateur
   en mode dégradé — mode visible dans l'UI avec un badge jaune.
4. Résolution automatique dès que le scraper retrouve l'accès (le scheduler tourne toutes
   les 60 secondes).

---

## Incident : stats Bzzoiro manquantes ou en retard

**Symptôme :** Des joueurs apparaissent dans le calculateur sans stats (`xG = 0`, `lambda`
très faible). Les logs montrent des 502/503 sur l'API Bzzoiro.

**Diagnostic :**
```bash
# Compter les stats par date (les derniers jours doivent avoir des rows)
docker compose -p ev0-compose-z5hvqt exec db \
  psql -U ev0 -c "
    SELECT DATE(e.event_date) as match_day, COUNT(s.id) as stat_rows
    FROM bzz_events e
    LEFT JOIN bzz_player_match_stats s ON s.event_api_id = e.api_id
    WHERE e.event_date > NOW() - INTERVAL '14 days'
    GROUP BY 1 ORDER BY 1 DESC LIMIT 10;"

# Vérifier les erreurs Bzzoiro dans les logs
docker compose -p ev0-compose-z5hvqt logs worker | grep -i "bzzoiro.*error\|502\|503" | tail -20
```

**Actions :**
1. Si l'API Bzzoiro est temporairement indisponible : les anciens agrégats restent valides
   pendant 7 jours. Aucune action urgente.
2. Si les stats manquent depuis > 24h : relancer manuellement
   `job_sync_bzzoiro_player_stats` puis `job_aggregate_season_stats`.
3. Si le problème persiste > 7 jours : les formes ne sont plus à jour. Contacter Bzzoiro.

---

## Incident : StatsHub gap-fill stats en échec

**Symptôme :** Les logs affichent `statshub: all retries failed for team=...` en boucle.
Certains joueurs ont encore des NULLs dans `bzz_player_match_stats` après le job quotidien.

**Impact :** Dégradation silencieuse — Bzzoiro continue à alimenter le pricing. Les NULLs
restent nuls, mais le calculateur ne casse pas. Priorité basse.

**Diagnostic :**
```bash
# Compter les NULLs critiques (xG, tirs) pour les matchs récents
docker compose -p ev0-compose-z5hvqt exec db \
  psql -U ev0 -c "
    SELECT
      COUNT(*) AS total_rows,
      COUNT(*) FILTER (WHERE expected_goals IS NULL) AS null_xg,
      COUNT(*) FILTER (WHERE total_shots IS NULL) AS null_shots,
      COUNT(*) FILTER (WHERE expected_goals IS NULL) * 100.0 / COUNT(*) AS pct_null_xg
    FROM bzz_player_match_stats s
    JOIN bzz_events e ON e.api_id = s.event_api_id
    WHERE e.event_date > NOW() - INTERVAL '30 days';"

# Tester l'endpoint StatsHub manuellement
curl -s "https://www.statshub.com/api/event/lineup-status?ids=14023940" \
  -H "Referer: https://www.statshub.com/" | python3 -m json.tool
```

**Actions :**
1. Si StatsHub est joignable : re-lancer `job_sync_statshub_gap_fill` manuellement.
2. Si StatsHub est inaccessible (timeout, 5xx) : attendre la prochaine fenêtre (08:15 UTC).
   Le job réessaie automatiquement (4 tentatives avec backoff exponentiel).
3. Pour combler toute la saison en une fois : re-lancer `job_sync_statshub_full_season`.

---

## Incident : compo officielle non scrappée

> ⚠️ Applicable une fois `job_poll_statshub_lineups` implémenté.

**Symptôme :** Le calculateur affiche le badge "Dernière connue" ou "Manuel" pour un match
dont le KO est dans moins d'1h. Les logs ne montrent pas `StatsHub lineup: upserted official`.

**Diagnostic :**
```bash
# Vérifier l'état des compos pour les fixtures à venir
docker compose -p ev0-compose-z5hvqt exec db \
  psql -U ev0 -c "
    SELECT f.id, f.home_team, f.away_team, f.kickoff_utc,
           tl.lineup_type, tl.source
    FROM fixtures f
    LEFT JOIN team_lineups tl ON tl.fixture_id = f.id
    WHERE f.status = 'scheduled'
      AND f.kickoff_utc BETWEEN NOW() AND NOW() + INTERVAL '3 hours'
    ORDER BY f.kickoff_utc, f.id, tl.lineup_type;"

# Tester l'endpoint lineup-status pour un event_id spécifique
# (extraire l'event_api_id depuis external_id = "bzz_XXXXXXXX")
curl -s "https://www.statshub.com/api/event/lineup-status?ids=<event_api_id>" \
  -H "Referer: https://www.statshub.com/" | python3 -m json.tool
```

**Actions :**
1. Si StatsHub retourne `"confirmed"` mais la compo n'est pas en DB : re-lancer le job
   `job_poll_statshub_lineups` manuellement.
2. Si StatsHub retourne `"none"` ou `"predicted"` : la compo n'est pas encore officielle.
   Saisir manuellement via **Dashboard → Compos** (`probable_manual`).
3. Si le match débute dans < 15min sans compo : le calculateur utilisera `last_known`
   (dernière compo officielle du match précédent). C'est le comportement normal de fallback.

---

## Incident : erreurs de mapping joueurs

**Symptôme :** Des recommandations référencent des joueurs inconnus, ou des joueurs sont
absents du calculateur alors qu'ils jouent.

**Diagnostic :**
```bash
# Joueurs présents dans les compos mais absents de bzz_players
docker compose -p ev0-compose-z5hvqt exec db \
  psql -U ev0 -c "
    SELECT DISTINCT tlp.player_name
    FROM team_lineup_players tlp
    LEFT JOIN bzz_players bp ON bp.name = tlp.player_name
    WHERE bp.id IS NULL
    ORDER BY 1;"

# Vérifier si le joueur est dans Bzzoiro sous un nom différent
docker compose -p ev0-compose-z5hvqt exec db \
  psql -U ev0 -c "
    SELECT name, current_team_name FROM bzz_players
    WHERE name ILIKE '%<nom_partiel>%' LIMIT 10;"
```

**Actions :**
1. Si le joueur existe dans Bzzoiro sous un nom différent : mettre à jour la compo
   manuellement avec le bon nom via **Dashboard → Compos**.
2. Si le joueur est absent de Bzzoiro (transfert récent, U23) : attendre le prochain
   `job_sync_bzzoiro_players` (03:00 UTC) ou le déclencher manuellement.

---

## Vérification post-migration Alembic

```bash
# Appliquer les migrations
docker compose -p ev0-compose-z5hvqt exec backend alembic upgrade head

# Vérifier la version courante
docker compose -p ev0-compose-z5hvqt exec backend alembic current

# Rollback d'une migration si nécessaire
docker compose -p ev0-compose-z5hvqt exec backend alembic downgrade -1
```

---

## Requêtes SQL utiles

```sql
-- Nombre de stats par source (Bzzoiro vs StatsHub)
-- (approximation : les rows sans minutes_played proviennent souvent de StatsHub)
SELECT
  CASE WHEN minutes_played IS NOT NULL THEN 'bzzoiro' ELSE 'statshub_only' END AS source,
  COUNT(*) AS rows
FROM bzz_player_match_stats
GROUP BY 1;

-- Couverture xG des joueurs par ligue (% de matchs avec xG non null)
SELECT
  l.name AS league,
  COUNT(*) AS total_stats,
  COUNT(s.expected_goals) AS with_xg,
  ROUND(COUNT(s.expected_goals) * 100.0 / COUNT(*), 1) AS pct_xg
FROM bzz_player_match_stats s
JOIN bzz_events e ON e.api_id = s.event_api_id
JOIN bzz_leagues l ON l.api_id = e.league_api_id
GROUP BY l.name ORDER BY pct_xg DESC;

-- Fixtures sans aucune compo (ni official, ni probable_manual)
SELECT f.id, f.home_team, f.away_team, f.kickoff_utc, f.league
FROM fixtures f
WHERE f.status = 'scheduled'
  AND f.kickoff_utc > NOW()
  AND NOT EXISTS (
    SELECT 1 FROM team_lineups tl WHERE tl.fixture_id = f.id
  )
ORDER BY f.kickoff_utc;

-- Dernière activité par job (approximation via updated_at des tables)
SELECT 'bzz_player_match_stats' AS source, MAX(updated_at) AS last_update FROM bzz_player_match_stats
UNION ALL
SELECT 'bzz_player_season_stats', MAX(updated_at) FROM bzz_player_season_stats
UNION ALL
SELECT 'match_odds_snapshots', MAX(scraped_at) FROM match_odds_snapshots
UNION ALL
SELECT 'player_odds_snapshots', MAX(scraped_at) FROM player_odds_snapshots
UNION ALL
SELECT 'team_lineups', MAX(updated_at) FROM team_lineups
ORDER BY last_update DESC;
```
