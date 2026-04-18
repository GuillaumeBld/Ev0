# Limitations connues

## Ligues couvertes

Ev0 couvre actuellement **5 ligues** + la Ligue des Champions :

| Ligue | Statut |
|-------|--------|
| Premier League | ✅ Actif |
| Ligue 1 | ✅ Actif |
| Bundesliga | ✅ Actif |
| Serie A | ✅ Actif |
| La Liga | ✅ Actif |
| Champions League | ✅ Actif (phases finales) |

Les ligues hors Big 5 (Eredivisie, Liga Portugal, etc.) ne sont pas couvertes.

---

## Marchés couverts

- ✅ Anytime Goalscorer (buteur)
- ✅ Anytime Assist (passeur décisif)
- ❌ First Goalscorer (pas de modèle d'ordre)
- ❌ Over/Under buts (pas de modèle de match)
- ❌ Handicap (pas implémenté)

---

## Disponibilité des cotes de marché (xG équipe)

Le moteur top-down nécessite des cotes H2H + Over/Under pour calculer `λ_team`. Si ces cotes sont absentes ou périmées (> 4h), le pricing n'est pas calculé et le match est ignoré dans les recommandations.

Cela peut arriver pour :
- Des matchs très éloignés (> 7 jours) dont OddsPortal ne liste pas encore les cotes
- Des matchs de coupes ou phases de groupes peu suivis par les bookmakers

---

## Composition d'équipe

Les compositions officielles ne sont disponibles qu'1h avant le match. Avant ça, les minutes attendues sont estimées sur la base de l'historique de titularisation. Erreur possible de ±15 min → impact modéré sur le calcul lambda.

Une fois la compo saisie dans l'onglet Compos, le calculateur redistribue le xG uniquement entre les titulaires pour des cotes plus précises.

---

## Correspondance joueurs / bookmakers

Le nom d'un joueur dans la base Bzzoiro peut différer du nom affiché par le bookmaker (accents, ordre prénom/nom, abréviations). La correspondance se fait par normalisation de nom. Des ratés sont possibles, notamment pour des joueurs avec des noms composés ou des caractères spéciaux.

---

## Données Bzzoiro — délai d'initialisation

Après un nouveau déploiement ou une réinitialisation de la base de données, les tables Bzzoiro sont vides jusqu'à la première exécution des jobs de sync (04:00–06:30 UTC). Pendant ce délai, les recommandations ne peuvent pas être générées.

**Solution** : forcer les jobs manuellement via le worker ou attendre la prochaine exécution planifiée.

---

## Match events — import manuel

Les buts et passes décisives (utilisés pour le settlement et l'affichage des events) sont importés manuellement depuis Sofascore car l'API est bloquée sur le VPS (Cloudflare 403). Le settlement automatique n'est donc pas disponible — il doit être fait manuellement depuis l'onglet Historique.

---

## Mode Live Autopilot

Le mode `live` de l'autopilot n'est **pas encore implémenté**. Seul le mode `paper` est actif : les décisions sont enregistrées mais aucun pari n'est placé automatiquement.
