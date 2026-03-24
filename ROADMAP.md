# Ev0 Roadmap

| Item | Statut |
|------|--------|
| ✅ Modèle C — remplacer FBref par Understat + Sofascore (npxG/xA anchor + quality/creation multiplier) | `[x]` |
| ✅ Importer les données Sofascore manuellement (script ops/update_sofascore.sh) | `[x]` |
| ✅ Ajouter les 3 autres championnats du Big 5 (Bundesliga, La Liga, Serie A) | `[x]` |
| ✅ Filtres dans la page recommendations (date, marché, seuil edge) | `[x]` |
| ✅ Ajouter mention VOID dans l'historique des bets | `[x]` |
| ✅ Dissocier approbation/rejet et résultat dans l'historique (onglets Approuvés + AutoFlat, badge Running) | `[x]` |
| ✅ Corriger l'historique (P&L 10€ fixe, settlement cohérent) | `[x]` |
| ✅ Auto-settle GitHub Actions (cron 30 min, scraping Understat via Playwright, import PMM, settlement automatique) | `[x]` |
| ✅ Fuzzy matching des noms de joueurs (hyphens, apostrophes, espaces, prénoms intermédiaires : Al-Tamari == Al Tamari, Idrissa Gueye == Idrissa Gana Gueye) | `[x]` |
| ✅ Migration source match events FotMob → ESPN (toutes leagues, pipeline auto-finish + ESPN + settle toutes les 30 min, délai max kickoff+2h30) | `[x]` |
| Ajouter compo + remplacements (effectifs à jour, amélioration modèle joueurs) | `[ ]` |
| Autopilot apprend de auto flat ? (feature/réflexion) | `[ ]` |
| ✅ Corriger "PL" dans match (display bug, Premier League uniquement) | `[x]` |
| ✅ Lier match → calculateur (clic sur un match ouvre le calculateur avec cotes + %) | `[x]` |
| ✅ Recommendations expirées : section collapsible + job d'expiration automatique toutes les 5 min | `[x]` |
| ✅ Actualisation live — décisions persistantes au rechargement, polling 10s multi-user, matchs passés filtrés, page Matches scindée en deux sections | `[x]` |
| Recalibrer le scraping odds (sources, fréquences, seuils) | `[ ]` |

**Légende :** `[ ]` à faire · `[~]` en cours · `[x]` fait
