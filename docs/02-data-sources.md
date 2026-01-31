# Sources de données et Protocoles de Robustesse

## Sources de Données Primaires

### 🥇 FBref (StatsBomb)
*   **Usage** : Source de vérité pour les metrics xG, xA, Minutes.
*   **Fréquence** : Quotidienne (J+1 après match).
*   **Critère de Qualité** : Doit contenir `npxG` et `Minutes`.

### 🥈 Bookmakers FR (Betclic, Unibet)
*   **Usage** : Source des cotes "Soft" à battre.
*   **Fréquence** : Horaire (ou On-Demand).

---

## Infrastructure & Réseau (Politique Locale)

Pour la version V1, l'exécution se fait **localement** (IP Résidentielle).

*   **Pourquoi ?** FBref et les Bookmakers bloquent agressivement les IPs de Datacenter (AWS, GCP, DigitalOcean).
*   **Contrainte** : Le script d'ingestion doit tourner sur une machine physique (Laptop ou Mini-PC) connectée à un FAI résidentiel.
*   **Proxies** : Pas de Smart Proxies coûteux pour la V1. Si blocage, pause de 24h.

---

## Protocoles de Fallback (Gestion des Pannes)

### Cas 1 : FBref indisponible ou bloqué
**Impact** : Impossible de mettre à jour les moyennes xG/90 des joueurs après le dernier match.

**Procédure Automatique** :
1.  **Mode Dégradé (Stale Data)** : Le système continue de pricer avec les snapshots de la veille (`T-1`).
2.  **Alerte** : Flag `DATA_STALE` ajouté à toutes les recommandations.
3.  **Limite** : Si panne > 7 jours, arrêt complet du pricing (les formes ne sont plus à jour).

### Cas 2 : Cotes indisponibles sur un Bookmaker
**Impact** : Impossible de calculer l'Edge exact pour ce book.

**Règle de Gestion** :
1.  Ignorer le bookmaker défaillant.
2.  Si aucun bookmaker FR n'est disponible : **ARRÊT**. Pas de pari sans contrepartie.

---

## Validation de la Donnée (Sanity Checks)

Chaque pipeline d'ingestion doit passer ces tests bloquants :

| Test | Condition | Action si Échec |
|------|-----------|-----------------|
| **Sum Probabilities** | Somme des probas implicites bookmaker > 1.0 (Overround normal) et < 1.30. | Rejet Snapshot (Erreur parsing) |
| **Outliers xG** | Un joueur a > 3.0 xG sur un match. | Flag Warning (Possible mais rare) |
| **Negative Stats** | npxG < 0 ou Minutes < 0. | Rejet Snapshot (Bug source) |