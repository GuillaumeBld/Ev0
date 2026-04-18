# Autopilot

## C'est quoi ?

L'Autopilot est le module de décision automatique d'Ev0. Pour chaque recommandation du jour, il analyse les caractéristiques du pari et choisit quoi faire : passer, miser prudemment, ou miser plus fort.

Il apprend de ses résultats passés et ajuste sa stratégie en continu.

---

## Comment il décide ?

L'agent analyse les informations suivantes sur chaque pari pour prendre sa décision :

| Information | Ce que ça signifie |
|-------------|-------------------|
| Edge | L'avantage sur le bookmaker (plus c'est élevé, mieux c'est) |
| Confiance | À quel point Ev0 est certain de son calcul (0.25 à 0.85) |
| Probabilité implicite | Ce que la cote du bookmaker dit sur les chances du joueur |
| Probabilité fair | Ce qu'Ev0 estime être la vraie probabilité |
| Intensité lambda | Les buts attendus pour ce joueur dans ce match |
| Minutes attendues | Combien de temps le joueur devrait jouer |
| Marché | Buteur ou passeur décisif |
| Ligue | Quelle compétition |
| Poste | Attaquant, milieu ou défenseur |
| Rang de l'edge | Ce pari est-il le meilleur du jour ou un pari moyen ? |
| Nombre de paris du jour | Plus il y en a, plus l'agent est sélectif |

À partir de ces informations, l'agent choisit une de ces 4 actions :

| Action | Ce que ça fait |
|--------|----------------|
| Passer | Ne pas parier |
| Quart Kelly | Mise prudente (25 % de la mise optimale) |
| Demi Kelly | Mise modérée (50 % de la mise optimale) |
| Kelly | Mise pleine |

---

## Comment il apprend ?

### Phase 1 : Pré-entraînement

Avant de décider en conditions réelles, l'agent est entraîné sur les données historiques. Il rejoue tous les matchs passés, fait ses choix, et ajuste sa stratégie en fonction des résultats — en respectant l'ordre chronologique (pas de triche via le futur).

### Phase 2 : Fine-tuning continu

Une fois en service, l'agent continue d'apprendre après chaque settlement. Après 10 nouveaux résultats, il met automatiquement à jour sa stratégie.

---

## Mode Paper vs Live

- **Paper** (par défaut) : l'agent prend ses décisions et les enregistre, mais aucun vrai pari n'est placé. Mode simulation pour valider les performances avant d'engager de l'argent réel.
- **Live** : les décisions mènent à de vrais paris. Non disponible pour l'instant.

---

## Optimisation automatique

L'Autopilot peut s'auto-optimiser pour trouver les meilleurs réglages possibles. Le système teste automatiquement de nombreuses combinaisons de paramètres et garde la meilleure.

### Ce qui est optimisé

- **Vitesse d'apprentissage** : trop rapide = instable, trop lente = n'apprend pas
- **Exploration** : temps pendant lequel l'agent teste des stratégies nouvelles avant d'exploiter ce qu'il connaît
- **Régularisation** : empêche l'agent de devenir trop complexe (un modèle simple généralise mieux)
- **Sélection des features** : certaines informations peuvent être du bruit — l'optimisation peut les désactiver
- **Force du prior** : à quel point l'agent fait confiance à la règle de base "mise plus quand l'edge est grand"

### Les métriques affichées

| Métrique | Ce que ça signifie |
|----------|-------------------|
| Log-Wealth | Croissance du capital — métrique principale. Plus c'est élevé, mieux la stratégie performe sur la durée |
| DSR | Fiabilité statistique du résultat. Au-dessus de 1 = résultat significatif |
| Features actives | Nombre d'informations utilisées. Moins = plus simple = souvent plus robuste |
| ROI | Pourcentage de gain par rapport aux mises |

### Quand ça tourne ?

- **Automatiquement** chaque dimanche à 3h du matin
- **À la demande** via le bouton "Lancer Optimisation" dans le dashboard (≈ 30 à 60 secondes)

---

## Tâches automatiques

| Tâche | Fréquence | Ce qu'elle fait |
|-------|-----------|-----------------|
| Évaluation | Toutes les 2h | Regarde les recommandations du jour et décide quoi parier |
| Settlement | Tous les jours à 9h | Vérifie les résultats des matchs et met à jour les paris |
| Re-optimisation | Chaque dimanche à 3h | Cherche les meilleurs réglages et met à jour l'agent |

---

## Qualité des prédictions

- **Brier Score** : mesure la précision des probabilités prédites. En dessous de 0.25 = mieux que de tirer à pile ou face.
- **Calibration** : quand l'agent dit "30 % de chances", est-ce que ça arrive vraiment 30 % du temps ? Le graphique de calibration compare les prédictions aux résultats réels.
