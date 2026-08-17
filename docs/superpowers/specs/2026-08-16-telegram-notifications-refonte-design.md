# Refonte des notifications Telegram — design

**Date :** 2026-08-16
**Statut :** validé (design), à planifier
**Dépendance :** ne démarre qu'une fois la branche `feat/transfermarkt-squad-sync` mergée
(migration 049 occupée ; la nôtre sera la 050).

## Problème

Toutes les notifications atterrissent dans une **conversation privée unique**
(`chat_id = 8589235488`, positif = chat privé). Le code définit pourtant deux
canaux logiques (`ops`, `recos`), mais `send_telegram_alert`
(`backend/app/notifications.py`) **ignore le paramètre `channel`** : la
séparation n'existe que côté WhatsApp, qui n'est qu'un fallback.

Conséquence : plusieurs dizaines de notifications par jour dans un seul fil
(volume estimé d'après les cadences ci-dessous, non mesuré en production),
mélangeant des alertes actionnables et de la plomberie. Sources par volume
décroissant :

| Source | Cadence | Volume |
|---|---|---|
| Autopilot : position prise | job toutes les 2h | **1 notif par pari** (12 paris = 12 vibrations) |
| Value bet détectée | à chaque détection | 1 par value |
| Settlement automatique | toutes les 30 min | 1 par batch réglé |
| Auto-finish fixtures | toutes les 30 min | 1 par lot |
| Digest recos expirant | toutes les 15 min | 1 par vague |
| Santé quotidienne, autopilot réglé, fine-tune | 8h / 9h | 3/jour |
| Tout log `ERROR` du worker | à la volée | variable (dédup 15 min) |

Aggravants :

- chaque notif autopilot embarque le **scorecard complet** (5 lignes), répété à
  l'identique sur chaque pari du même run ;
- le rapport de santé part tous les jours même quand tout va bien, donc il n'est
  plus lu le jour où il vire au rouge ;
- une conversation privée Telegram **ne peut pas être mise en sourdine
  partiellement** : impossible de filtrer côté client.

**Le fond du problème :** l'autopilot est autonome. Notifier chacune de ses
décisions en direct est du bruit par construction, puisque l'utilisateur ne peut
rien en faire.

## Objectif

Le téléphone ne vibre que sur ce qui appelle une action dans l'heure :
**une opportunité de marché** ou **une panne**. Tout le reste reste consultable,
dans un fil séparé et muet.

## Principe directeur

Chaque notification est classée selon **« puis-je agir dessus dans l'heure ? »**,
jamais selon son origine technique.

## Architecture — trois canaux, trois groupes Telegram

Option retenue : **trois groupes Telegram distincts**, même bot, un `chat_id` par
groupe. (Les *topics* d'un supergroupe unique ont été envisagés puis écartés au
profit d'une mise en œuvre plus directe : un dictionnaire canal → `chat_id`,
sans notion de fil.)

| Canal | Groupe | Sonore | Contenu |
|---|---|---|---|
| `value` | 🎯 Ev0 Value | oui | mouvements de marché exploitables |
| `incidents` | 🚨 Ev0 Incidents | oui | pannes nécessitant une intervention |
| `autopilot` | 🤖 Ev0 Autopilot | **non** (sourdine côté client) | activité autonome + plomberie |

### Table de routage

Lignes indicatives, relevées sur `main` au 2026-08-16 — elles bougeront après le
merge Transfermarkt (qui modifie `worker.py`).

| Point d'appel | Canal actuel | Nouveau canal |
|---|---|---|
| `recommendation_service.py:776` — VALUE BET | `recos` | `value` |
| `worker.py:1664` — digest recos expirant | `recos` | **supprimé** |
| `worker.py:519` — autopilot position prise | `recos` | `autopilot` *(groupée)* |
| `worker.py:739` — autopilot paris réglés | `ops` | `autopilot` |
| `worker.py:764` — autopilot fine-tune | `ops` | `autopilot` |
| `worker.py:893` — settlement automatique | défaut `ops` | `autopilot` |
| `worker.py:974` — auto-finish fixtures | défaut `ops` | `autopilot` |
| `worker.py:918` — settlement bloqué >48h | défaut `ops` | `incidents` |
| `worker.py:1758` — santé quotidienne | `ops` | `incidents` si rouge, sinon `autopilot` |
| `worker.py:2069` — job APScheduler en exception | `ops` | `incidents` |
| `alerts.py` — `ErrorAlertHandler.emit` | `ops` | `incidents` |

## Canal `value`

### Déclencheurs

Deux règles, et rien d'autre.

1. **Nouvelle value** — une cote franchit le seuil d'edge pour la première fois.
   Comportement actuel, conservé.
2. **Nouveau plus haut** — une reco déjà signalée voit sa meilleure cote dépasser
   d'au moins **5 %** la cote sur laquelle l'utilisateur a été alerté la dernière
   fois.

**Aucune notification quand la value se dégrade ou disparaît.** La fenêtre de
disponibilité d'une value appartient aux bookmakers, pas à l'utilisateur :
informer d'une expiration ou d'une baisse n'ouvre aucune action.

### Mémoire du niveau alerté

Nouvelle colonne `recommendations.alerted_odds FLOAT NULL` :

- posée à la cote au moment de la première alerte ;
- relevée à chaque nouvelle alerte ;
- **jamais abaissée**, même si la cote redescend.

Condition de déclenchement : `best_odds >= alerted_odds * 1.05`.

Effet recherché : une cote qui fait le yo-yo entre 2.50 et 2.60 ne sonne qu'une
fois ; seule une progression vers un vrai record redéclenche. Une cote qui monte
2.50 → 3.00 dans la journée produit 3 à 4 alertes, ce qui est le comportement
voulu.

### Groupement obligatoire

Le scraping de cotes (`job_odds_scheduler_tick`) tourne **toutes les 60
secondes** et réévalue toutes les recos d'un coup. Sans groupement, dix cotes qui
montent dans le même cycle produisent dix vibrations en trois secondes.

Règle : **au plus un message par cycle de scraping**, listant tous les mouvements
du cycle. Aucun message si le cycle ne produit rien.

### Format

```
🎯 3 mouvements

▲ NOUVELLE VALUE
Dembélé — Buteur
PSG vs Marseille (21:00)
2.85 Betclic | edge +12.4%

▲ Mbappé — Buteur
Real vs Girona (21:00)
3.10 → 3.40 Unibet | edge +8.1% → +14.2%

▲ Saka — Passeur
Arsenal vs Chelsea (18:30)
4.20 → 4.50 Betclic | edge +6.0% → +9.8%
```

## Canal `incidents`

Contenu : job APScheduler en exception, tout log `ERROR` du worker, settlement
bloqué depuis plus de 48h, rapport de santé quotidien lorsqu'un indicateur est
au rouge.

### Rapport de santé — changement de nature

Aujourd'hui : six lignes tous les jours à 8h, que tout aille bien ou non.
Demain : mêmes indicateurs calculés, mais **le message ne part sur `incidents`
que si au moins un seuil est franchi**. Sinon il tombe sur `autopilot`,
consultable.

Seuils déclenchant le rouge :

| Indicateur | Seuil rouge |
|---|---|
| `last_player_odds` | > 24h, ou jamais |
| `last_match_odds` | > 24h, ou jamais |
| `backlog_settle` | > 20 décisions |
| `recs_24h` | == 0 |

Le message rouge nomme explicitement le ou les indicateurs en cause.

### Plafond anti-crashloop

`ErrorAlertHandler` relaie **tout** log `ERROR`. La déduplication existante ne
couvre que 15 minutes et ne protège pas d'un bug qui varie son message : un
crashloop peut sonner en continu.

Règle : **au-delà de 3 alertes d'erreur dans une fenêtre glissante d'une heure,
on n'envoie plus qu'un message de synthèse** (`⚠️ +N erreurs supprimées, voir les
logs`), au plus une fois par heure. Le compteur repart à zéro après une heure
sans erreur.

## Canal `autopilot`

Contenu : positions prises, paris réglés et P&L, fine-tune, settlement
automatique, auto-finish fixtures, santé quotidienne quand tout va bien.

### Positions groupées par run

`notify_autopilot_position` (une notif par pari) est remplacée par
`notify_autopilot_run` : **un seul message par run d'autopilot**, listant les
paris pris, avec le **scorecard affiché une seule fois** en pied de message.
Si le run ne prend aucune position, aucun message.

## Mécanique d'envoi

### Configuration

Trois nouvelles variables d'environnement :

- `TELEGRAM_CHAT_ID_VALUE`
- `TELEGRAM_CHAT_ID_INCIDENTS`
- `TELEGRAM_CHAT_ID_AUTOPILOT`

`TELEGRAM_CHAT_ID` est conservée et change de rôle : elle devient le **filet de
sécurité**.

`send_telegram_alert(message, channel)` prend désormais le canal et résout le
`chat_id` via une table canal → identifiant.

### Filet de sécurité — jamais d'échec silencieux

Trois modes de panne, une seule réponse :

- le `chat_id` du canal n'est pas configuré ;
- le bot a été retiré du groupe ;
- Telegram refuse l'envoi.

Dans les trois cas, le message part sur **`TELEGRAM_CHAT_ID`** (chat historique),
**préfixé du canal visé** (`[value] …`), accompagné d'un log `WARNING`.

Justification : un système de notifications qui perd des messages est pire que
pas de système. Le bruit qui réapparaît dans l'ancien chat *est* le signal que
quelque chose est cassé — c'est volontairement visible.

### WhatsApp — secours partiel

WhatsApp (CallMeBot) reste en fallback pour `value` et `incidents` : y rater un
message a un coût. Mapping : `value` → téléphone `recos`, `incidents` →
téléphone `ops`.

Le canal `autopilot` **n'a aucun fallback WhatsApp**. Une panne Telegram
déverserait sinon tout le flood autopilot sur WhatsApp — exactement ce que cette
refonte élimine. Ces messages sont perdus, et c'est acceptable : ils sont
purement consultables.

### Rythme

`_last_channel_send` est déjà indexé par canal : passer de deux à trois canaux
suffit à isoler les rythmes. Une rafale `autopilot` ne peut plus retarder une
alerte `value` de 20 secondes. La déduplication (même message ignoré 15 minutes)
reste inchangée.

### Suppression du canal par défaut

`send_alert(message, channel="ops")` devient `send_alert(message, channel)` :
**le canal est obligatoire**. C'est le défaut implicite qui a laissé la plomberie
(auto-finish, settlement batch) se mélanger aux vraies alertes. Un appel sans
canal doit échouer à l'écriture du code, pas silencieusement à l'exécution.

## Changement de schéma

**Migration 050** (après la 049 Transfermarkt) :
`ALTER TABLE recommendations ADD COLUMN alerted_odds FLOAT NULL` + colonne au
modèle ORM. `downgrade` complet.

Pas de backfill : les recos existantes ont `alerted_odds = NULL` et se
comporteront comme des nouvelles values au premier passage.

## Suppressions

- Job `recos_expiry_digest` (toutes les 15 min) et sa fonction
  `job_recos_expiry_digest`.
- Fonction `notify_autopilot_position`, remplacée par `notify_autopilot_run`.
- Canaux `"ops"` et `"recos"` — plus aucune occurrence dans le code.

Le job `expire_recommendations` (nettoyage des recos passées au coup d'envoi) est
**conservé** : c'est de l'hygiène de données, il ne notifie rien.

## Tests

- **Routage** : chacun des dix points d'appel restants atterrit sur le canal
  attendu.
- **Filet** : `chat_id` absent → message envoyé sur le chat historique avec
  préfixe `[canal]` + log `WARNING` ; Telegram en erreur → même repli.
- **Nouveau plus haut** : +3 % ne sonne pas ; +6 % sonne ; yo-yo 2.50 → 2.60 →
  2.45 → 2.58 ne sonne qu'une fois ; `alerted_odds` n'est jamais abaissée.
- **Groupement value** : dix mouvements dans un cycle → un seul message
  contenant dix lignes ; cycle vide → aucun message.
- **Groupement autopilot** : run à 12 paris → un message, scorecard présent une
  seule fois ; run à 0 pari → aucun message.
- **Santé** : tous indicateurs verts → `autopilot` ; un indicateur rouge →
  `incidents`, avec l'indicateur nommé.
- **Plafond erreurs** : 3 erreurs distinctes en une heure → 3 alertes ; la 4ᵉ →
  message de synthèse unique ; après une heure sans erreur, compteur remis à zéro.
- **WhatsApp** : Telegram KO sur `value` → tentative WhatsApp ; Telegram KO sur
  `autopilot` → aucune tentative WhatsApp.

## Mise en service

À la charge de l'utilisateur, environ cinq minutes :

1. Créer trois groupes Telegram (🎯 Value, 🚨 Incidents, 🤖 Autopilot).
2. Ajouter le bot à chacun.
3. Récupérer les trois `chat_id` (négatifs pour un groupe).
4. Mettre `🤖 Autopilot` en sourdine côté client.

Déploiement — **rappel critique Dokploy** : les trois variables doivent être
posées **à la fois** dans `/etc/dokploy/compose/ev0-compose-z5hvqt/code/.env`
**et** dans la colonne `env` de la table `compose` de la base Dokploy
(`composeId = 'bpQY8Yr986JiwJRR_b0sk'`). Sinon le déploiement suivant les écrase.

Redéploiement backend + worker sans toucher à la base :

```bash
cd /etc/dokploy/compose/ev0-compose-z5hvqt/code
docker compose -p ev0-compose-z5hvqt --env-file .env \
  up -d --build --no-deps --remove-orphans backend worker
```

## Hors périmètre

- Topics Telegram (option A écartée).
- Notification quand une value se dégrade ou disparaît.
- Notification calendaire liée au coup d'envoi.
- Fallback WhatsApp sur le canal `autopilot`.
- Récapitulatif quotidien fusionné (le canal muet joue déjà ce rôle).
