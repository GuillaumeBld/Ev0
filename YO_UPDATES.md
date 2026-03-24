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
Le pipeline de settlement automatique était cassé sur plusieurs points. FotMob (source historique) retourne des erreurs 403/404. Les bets approuvés restaient bloqués en "Running" indéfiniment.

### Ce qui a été fait

**1. Fix curl → python3 dans GitHub Actions**
Le container Docker n'avait pas `curl`. Remplacé par `urllib.request` natif Python avec timeout et gestion des codes d'erreur.

**2. Migration FotMob → ESPN pour les match events**
FotMob retourne 403 sur `/api/matchDetails` depuis le VPS (Cloudflare). ESPN couvre toutes les leagues supportées via son API publique (Ligue 1, PL, Bundesliga, La Liga, Serie A, LDC). Migration complète effectuée.

**3. Pipeline de settlement chaîné (toutes les 30 min)**
Avant : 3 jobs indépendants → délai max **4h30** entre fin du match et settlement.
Après : 1 seul pipeline `auto-finish → ESPN events → settle` toutes les 30 min → délai max **2h30**.

**4. Fix matchs 0-0**
ESPN retournait une liste vide pour les matchs sans buts, indiscernable d'un "match non trouvé". Ajout d'un sentinel `match_processed` en DB pour débloquer le settlement (tous goalscorer/assist → LOST).

**5. Fuzzy matching étendu aux prénoms intermédiaires**
ESPN supprime parfois les prénoms intermédiaires : `Idrissa Gueye` ≠ `Idrissa Gana Gueye`. Ajout d'une fonction `_names_match()` qui vérifie prénom + nom de famille indépendamment du prénom intermédiaire.

**6. Fix logique settlement (PMM optionnel)**
Le settlement bloquait si `PlayerMatchMinutes = 0`, même quand les MatchEvents étaient disponibles. PMM est maintenant optionnel (utilisé uniquement pour détecter les VOID), le settlement procède directement WON/LOST si les events existent.

### Résultat
- **91 bets settlés, 0 en attente**
- 13 WON · 65 LOST · 13 VOID
- PnL total : -351,30€

### Prochain chantier
→ **Mode compo** : intégrer les compositions d'équipe et remplacements pour améliorer le modèle de prédiction joueur (minutes attendues, titulariat).
