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

## HTTPS / certificats

Les domaines `*.sslip.io` sont routés par des **labels Traefik dans
`docker-compose.yml`**, pas par la configuration de domaine de Dokploy (la table
`domain` ne connaît que les hôtes `traefik.me` internes).

Chaque routeur `websecure` a besoin de **deux** labels, pas un :

```yaml
- traefik.http.routers.<routeur>-websecure.tls=true
- traefik.http.routers.<routeur>-websecure.tls.certresolver=letsencrypt
```

`tls=true` seul dit à Traefik « sers ce domaine en HTTPS » sans jamais lui
demander d'obtenir un certificat : il sert alors son **TRAEFIK DEFAULT CERT**
auto-signé. Symptôme : le site marche sur un ordinateur où l'exception de
sécurité a été acceptée une fois, mais un téléphone refuse de l'ouvrir.

Vérification :

```bash
echo | openssl s_client -connect <domaine>:443 -servername <domaine> 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates
```

L'émetteur doit être Let's Encrypt. `CN=TRAEFIK DEFAULT CERT` signale le label
manquant. Les certificats obtenus sont listés dans
`/etc/dokploy/traefik/dynamic/acme.json` (résolveur `letsencrypt`).

## xG d'équipe — source PS3838

Le xG d'équipe qui alimente le pricing buteur/passeur provient **exclusivement**
de PS3838 (déclinaison de la plateforme Pinnacle, mêmes identifiants
d'événements, joignable depuis le VPS là où `guest.api.arcadia.pinnacle.com`
répond 403).

Les cotes sont rattachées aux matchs par `fixtures.ps3838_event_id`, résolu une
seule fois en vérifiant les équipes **et** la date. Aucun rapprochement par nom
n'intervient au moment du scraping.

Betclic, Unibet et PMU restent utilisés pour les **cotes buteur et passeur**
uniquement — jamais pour le calcul du xG : ils rattachent parfois les cotes au
mauvais match.

Vérifier qu'un match est ancré et pricé :

```bash
docker exec ev0-compose-z5hvqt-db-1 psql -U ev0 -d ev0 -c "
SELECT home_team, away_team, ps3838_event_id FROM fixtures
WHERE kickoff_utc > now() AND kickoff_utc < now() + interval '7 days'
  AND ps3838_event_id IS NULL;"
```

Toute ligne renvoyée est une anomalie : PS3838 ouvre ses lignes ~10 jours à
l'avance. Ces matchs n'auront aucune recommandation, et une alerte part sur le
canal `incidents`.

La bibliothèque `team_xg_estimates` archive l'ouverture et le closing de chaque
match. **Elle ne doit jamais être ajoutée à `job_purge_old_snapshots`** : les
cotes brutes disparaissent à 45 jours, ces valeurs sont la seule trace durable.

## Alerting

Telegram est le transport primaire, WhatsApp (CallMeBot) le secours.
Trois canaux, **un groupe Telegram chacun** :

| Canal | Groupe | Sonore | Contenu |
|---|---|---|---|
| `value` | 🎯 Ev0 Value | oui | nouvelle value, ou nouveau plus haut de cote (+5 % au-dessus du dernier niveau notifié) |
| `incidents` | 🚨 Ev0 Incidents | oui | job en exception, log ERROR, settlement bloqué >48h, santé au rouge |
| `autopilot` | 🤖 Ev0 Autopilot | **à mettre en sourdine** | positions prises, paris réglés, P&L, fine-tune, auto-finish, santé quand tout va bien |

Aucune notification quand une value se dégrade ou disparaît : la fenêtre de
disponibilité appartient aux bookmakers, il n'y a rien à décider.

> **Trois endroits, pas deux.** Une variable d'environnement n'atteint les
> conteneurs que si elle est déclarée dans le bloc `environment:` de
> `docker-compose.yml` (services `backend` **et** `worker`). La poser dans le
> `.env` et dans la base Dokploy ne suffit pas : compose n'injecte que ce qui
> est listé. Symptôme : les logs du worker répètent
> `Canal 'x' non configuré — repli sur le chat historique` alors que la
> variable est bien présente dans le `.env`.

**Configuration.** `TELEGRAM_BOT_TOKEN` plus un `chat_id` par canal :
`TELEGRAM_CHAT_ID_VALUE`, `TELEGRAM_CHAT_ID_INCIDENTS`,
`TELEGRAM_CHAT_ID_AUTOPILOT`. `TELEGRAM_CHAT_ID` reste le **filet de secours** :
si un canal n'est pas configuré ou que son groupe est injoignable, le message y
part préfixé `[canal]` avec un log `WARNING` — jamais d'échec silencieux. Voir
un préfixe `[canal]` arriver dans le chat historique signale un `chat_id` mal
posé.

Secours WhatsApp (`WHATSAPP_OPS_PHONE/APIKEY`, `WHATSAPP_RECOS_PHONE/APIKEY`)
uniquement pour `value` et `incidents`. Le canal `autopilot` n'en a
volontairement aucun : une panne Telegram ne doit pas déverser son flot sur
WhatsApp.

**Garde-fous.** Même message ignoré 15 min, 20 s minimum entre deux envois d'un
même canal, et au-delà de 3 alertes d'erreur par heure les suivantes sont
absorbées dans un message de synthèse (anti-crashloop).
