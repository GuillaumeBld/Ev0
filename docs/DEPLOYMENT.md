# Déploiement Ev0 — règles et procédures

> **Règle d'or : Dokploy auto-déploie sur chaque push GitHub (webhook).**
> Ne JAMAIS lancer de `docker compose build/up` manuellement après un push —
> les deux déploiements entrent en course et produisent des conflits de
> conteneurs orphelins (`Conflict. The container name … is already in use`).

## Déploiement standard (code)

1. `git push origin main`
2. Attendre le déploiement Dokploy (~90 s : pull + build + recreate).
3. **Vérifier que le code tourne réellement** (le tag du conteneur ne suffit pas) :
   ```bash
   ssh root@213.130.144.204 "docker exec ev0-compose-z5hvqt-worker-1 \
     python -c 'import inspect; from app import worker; print(...)'"
   ```
   ou vérifier `git log -1` dans `/etc/dokploy/compose/ev0-compose-z5hvqt/code`.

Les migrations Alembic s'exécutent automatiquement au démarrage du backend
(`alembic upgrade head` dans la commande compose).

## Variables d'environnement — TROIS endroits obligatoires

Le bloc `environment:` du `docker-compose.yml` **whiteliste** les variables :
une variable absente de ce bloc n'atteint jamais le conteneur, même présente
dans `.env` (c'est ainsi que `TELEGRAM_BOT_TOKEN` a été silencieusement mort).

Pour ajouter/modifier une variable :

1. **`docker-compose.yml`** : l'ajouter au bloc `environment:` des services
   concernés (backend ET worker si les deux l'utilisent), commit + push.
2. **`.env` sur le VPS** : `/etc/dokploy/compose/ev0-compose-z5hvqt/code/.env`
3. **DB Dokploy** (sinon le prochain déploiement écrase le `.env`) :
   ```sql
   -- dans le conteneur dokploy-postgres, db dokploy, user dokploy
   UPDATE compose SET env = env || E'\nMA_VAR=valeur'
   WHERE "composeId" = 'bpQY8Yr986JiwJRR_b0sk';
   ```
4. Mettre à jour le backup `/root/.ev0-credentials` si c'est un secret.

## Recreate manuel (uniquement hors push : changement d'env, dépannage)

```bash
cd /etc/dokploy/compose/ev0-compose-z5hvqt/code
docker rm -f ev0-compose-z5hvqt-backend-1 ev0-compose-z5hvqt-worker-1
docker compose -p ev0-compose-z5hvqt --env-file .env up -d \
  --no-deps --no-build --remove-orphans backend worker
```

- **JAMAIS `--force-recreate` sans `--no-deps`** : recrée les dépendances,
  y compris la DB (perte de données).
- **Toujours `--remove-orphans`** dans les commandes de rebuild.
- Après toute recréation du conteneur DB : réappliquer le fix pg_hba
  (voir `/root/.ev0-credentials` et la mémoire d'exploitation).

## Diagnostic rapide

```bash
docker ps --format '{{.Names}}\t{{.Status}}'      # tout doit être Up, noms SANS préfixe hash
docker logs --since 10m ev0-compose-z5hvqt-worker-1 | grep -iE 'error|failed'
```

Un conteneur nommé `<hash>_ev0-compose-…` = résidu d'un déploiement en course
→ le supprimer puis refaire le recreate manuel ci-dessus.

## Alerting

Les crashs de jobs et le bilan santé quotidien (08:00 UTC) arrivent sur
WhatsApp (CallMeBot, canal ops). Les value bets arrivent sur le canal recos.
Config : `WHATSAPP_OPS_PHONE/APIKEY`, `WHATSAPP_RECOS_PHONE/APIKEY`
(fallback Telegram via `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`).
