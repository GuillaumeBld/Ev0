# Avancées de Yohan — Ev0

> Fichier mis à jour en temps réel par Yohan. Guillaume peut suivre ici ce qui est fait, en cours, et à venir.

---

## Roadmap globale

| Item | Statut |
|------|--------|
| Modèle C — remplacer FBref par Understat + Sofascore (npxG/xA anchor + quality/creation multiplier) | ✅ |
| Importer les données Sofascore manuellement (script ops/update_sofascore.sh) | ✅ |
| Ajouter les 3 autres championnats du Big 5 (Bundesliga, La Liga, Serie A) | ✅ |
| Filtres dans la page recommendations (date, marché, seuil edge) | ✅ |
| Ajouter mention VOID dans l'historique des bets | ✅ |
| Dissocier approbation/rejet et résultat dans l'historique (onglets Approuvés + AutoFlat, badge Running) | ✅ |
| Corriger l'historique (P&L 10€ fixe, settlement cohérent) | ✅ |
| Auto-settle (cron 30 min, settlement automatique) | ✅ |
| Fuzzy matching des noms de joueurs (hyphens, apostrophes, prénoms intermédiaires) | ✅ |
| Corriger "PL" dans match (display bug, Premier League uniquement) | ✅ |
| Lier match → calculateur (clic sur un match ouvre le calculateur avec cotes + %) | ✅ |
| Recommendations expirées : section collapsible + job d'expiration automatique | ✅ |
| Actualisation live — décisions persistantes, polling 10s multi-user | ✅ |
| **Ajouter compo + remplacements (effectifs à jour, amélioration modèle)** | 🔜 |
| Autopilot apprend de auto flat ? | 🔜 |
| Recalibrer le scraping odds (sources, fréquences, seuils) | 🔜 |

---

## Session du 24/03/2026 — Pipeline de settlement autonome

### Contexte
Le pipeline de settlement automatique était cassé sur plusieurs points. FotMob (source historique) retourne des erreurs 403/404 depuis le VPS. Les bets approuvés restaient bloqués en "Running" indéfiniment — 91 bets en attente au début de la session.

---

### Ce qui a été fait

**1. Fix `curl` → `python3` dans GitHub Actions**
Le container Docker backend n'a pas `curl`. Le step "Trigger auto-settle" du workflow crashait silencieusement. Remplacé par `urllib.request` natif Python avec timeout (30s), Content-Type header et vérification du code retour.

**2. Migration FotMob → ESPN pour les match events (Big 5 + LDC)**
FotMob retourne 403 sur `/api/matchDetails` depuis le VPS (Cloudflare block). Understat a changé son architecture entre-temps (données JSON plus embarquées dans le HTML, chargées via JS). ESPN couvre toutes les leagues supportées via son API publique gratuite :
- Ligue 1 → `fra.1`
- Premier League → `eng.1`
- Bundesliga → `ger.1`
- La Liga → `esp.1`
- Serie A → `ita.1`
- Champions League → `uefa.champions`

Migration complète effectuée dans `job_sync_match_events`.

**3. Pipeline de settlement chaîné toutes les 30 min**
Avant : 3 jobs indépendants — `auto_finish_fixtures` (30 min), `sync_match_events` (1x/jour à 08h00), `auto_settle` (toutes les 3h) → délai max **4h30** entre fin du match et settlement.

Après : 1 seul `job_settle_pipeline` qui enchaîne les 3 en séquence toutes les 30 min → délai max **2h30** (kickoff + 2h + 30 min worst case).

**4. Fix logique settlement — PMM optionnel**
Le settlement bloquait entièrement si `PlayerMatchMinutes = 0` pour une fixture, même quand les MatchEvents étaient déjà en DB. PMM est maintenant optionnel : utilisé uniquement pour détecter les VOID (joueur n'a pas joué). Si les MatchEvents existent, le settlement procède directement en WON/LOST.

**5. Fix matchs 0-0**
ESPN retournait `[]` à la fois pour "match non trouvé" et "match trouvé, 0 buts". Résultat : les matchs 0-0 restaient bloqués car aucun event en DB → settlement infini. Fix : `get_match_events` retourne maintenant `None` (non trouvé) vs `[]` (trouvé, 0 buts). Quand `[]`, un sentinel `event_type="match_processed"` est stocké en DB pour débloquer le settlement → tous les bets goalscorer/assist passent en LOST.

**6. Fuzzy matching étendu aux prénoms intermédiaires**
ESPN supprime parfois les prénoms intermédiaires : `Idrissa Gueye` (ESPN) ≠ `Idrissa Gana Gueye` (DB). Le matching exact normalisé échouait. Nouvelle fonction `_names_match()` : vérifie prénom + nom de famille indépendamment du prénom intermédiaire, en plus des règles existantes (hyphens, apostrophes, espaces).

Correction manuelle appliquée : bet de **Idrissa Gana Gueye (assist vs Chelsea)** settléLOST par erreur → corrigé en **WON (+45€)**.

**7. Fix container backend en état "Created"**
Après le rebuild du worker, le container backend était resté en état `Created` (non démarré). Site inaccessible. Redémarré et stabilisé avec `docker compose up -d --no-build --no-deps backend worker`.

---

### Résultat final
- **91 bets settlés, 0 en attente**
- 13 WON · 65 LOST · 13 VOID
- PnL total : **-351,30€**
- Pipeline opérationnel : prochain settlement automatique dans ≤ 30 min après la fin d'un match

---

---

## Prochaines sessions

**1. Mode compo** *(priorité 1)*
Intégrer les compositions d'équipe et remplacements pour améliorer le modèle de prédiction : minutes attendues, titulariat, impact des remplacements sur les probabilités de scorer/assister.

**2. Revoir le scraping xG** *(priorité 2)*
Les xG des matchs sont souvent faux. Revoir les sources et la méthode de scraping pour améliorer la fiabilité des données d'entrée du modèle.

**3. Adapter le scraping des books FR après fusion PSEL / Unibet** *(priorité 3 — urgent fin mars)*
La fusion ParionsSport (PSEL) et Unibet est annoncée pour fin mars 2026. La fréquence de scraping et les endpoints devront être revus une fois la fusion effective.
