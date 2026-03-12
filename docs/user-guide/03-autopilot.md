# Autopilot

## C'est quoi ?

L'Autopilot est le "cerveau" d'Ev0 qui prend les décisions de paris tout seul. Pour chaque recommandation du jour, il regarde les caractéristiques du pari (edge, confiance, cote, etc.) et choisit quoi faire : passer, miser prudemment, ou miser plus fort.

Il apprend de ses erreurs au fil du temps, exactement comme un parieur qui ajuste sa stratégie après avoir vu ce qui marche et ce qui ne marche pas.

## Comment il décide ?

L'agent analyse 13 informations sur chaque pari pour prendre sa décision :

| Information | Ce que ca veut dire |
|-------------|---------------------|
| Edge | L'avantage qu'on a sur le bookmaker (plus c'est haut, mieux c'est) |
| Confiance | A quel point Ev0 est sur de son calcul |
| Probabilite implicite | Ce que la cote du bookmaker "dit" sur les chances du joueur |
| Probabilite juste | Ce qu'Ev0 pense etre la vraie probabilite |
| Intensite lambda | A quel point le joueur est "dangereux" (buts attendus) |
| Minutes attendues | Combien de temps le joueur devrait jouer (un remplacant a moins de chances) |
| Marche buteur | Est-ce un pari buteur ? (vs passeur) |
| Premier League | Est-ce un match de Premier League ? |
| Attaquant | Le joueur joue-t-il en attaque ? |
| Rang de l'edge | Ce pari est-il le meilleur du jour ou un pari moyen ? |
| Nombre de paris du jour | Combien de paris sont disponibles aujourd'hui (plus il y en a, plus l'agent est selectif) |
| Mouvement de cote | La cote a-t-elle baisse recemment ? (signe que d'autres parieurs ont repere la valeur) |
| Biais | Valeur fixe qui aide l'agent a calibrer ses decisions |

A partir de ces informations, l'agent choisit une de ces 4 actions :

| Action | Ce que ca fait |
|--------|---------------|
| Passer | Ne pas parier (le risque ne vaut pas le coup) |
| Quart Kelly | Mise prudente (25% de la mise optimale) |
| Demi Kelly | Mise moderee (50% de la mise optimale) |
| Kelly | Mise pleine (mise optimale selon le critere de Kelly) |

## Comment il apprend ?

### Phase 1 : Pre-entrainement

Avant de le laisser decider en conditions reelles, on l'entraine sur les donnees historiques. L'agent rejoue tous les matchs passes, fait ses choix, et ajuste sa strategie en fonction de ce qui aurait marche. Le tout en respectant l'ordre chronologique -- il ne "triche" jamais en regardant dans le futur.

### Phase 2 : Fine-tuning en continu

Une fois en service, l'agent continue d'apprendre a chaque fois qu'un de ses paris est settle (gagne ou perdu). Apres 10 nouveaux resultats, il met automatiquement a jour sa strategie.

## Mode Paper vs Live

- **Paper** (par defaut) : l'agent prend ses decisions et on les enregistre, mais aucun vrai pari n'est place. C'est un mode "simulation" pour verifier que l'agent performe bien avant de lui confier de l'argent reel.
- **Live** : les decisions menent a de vrais paris. Non disponible pour l'instant.

## Optimisation automatique

L'Autopilot peut s'auto-optimiser pour trouver les meilleurs reglages possibles. Le systeme essaie automatiquement des centaines de combinaisons de parametres et garde uniquement la meilleure.

### Comment ca marche ?

Imaginons que l'agent a plusieurs "boutons de reglage" : a quelle vitesse il apprend, a quel point il explore de nouvelles strategies, quelles informations il prend en compte, etc. L'optimisation teste 100 combinaisons differentes de ces reglages et mesure laquelle fait le plus d'argent sur les donnees historiques.

Pour eviter de se tromper (un reglage qui marche sur le passe mais pas sur le futur), l'evaluation utilise une methode stricte :

- Les donnees sont decoupees en 5 periodes
- Pour chaque periode, l'agent est entraine uniquement sur ce qui s'est passe **avant**, puis teste sur cette periode
- Il y a un "trou" de 30 jours entre l'entrainement et le test, pour s'assurer qu'il ne profite pas d'informations trop proches

### Ce qui est optimise

- **Vitesse d'apprentissage** : trop vite = instable, trop lent = n'apprend pas
- **Exploration** : au debut l'agent essaie des choses au hasard pour decouvrir ce qui marche. L'optimisation regle combien de temps il explore vs exploite
- **Regularisation** : empeche l'agent de devenir trop complexe (un modele simple generalise mieux)
- **Selection des features** : certaines des 12 informations sont peut-etre du bruit. L'optimisation peut les desactiver pour simplifier la decision
- **Force du prior** : a quel point l'agent fait confiance a la regle de base "mise plus quand l'edge est grand"

### Les metriques affichees

| Metrique | Ce que ca veut dire |
|----------|---------------------|
| Log-Wealth | La croissance du capital -- c'est la metrique principale. Plus c'est haut, plus la strategie fait de l'argent de maniere durable |
| DSR | Indique si le resultat est statistiquement fiable ou si c'est peut-etre juste de la chance. Au-dessus de 1 = significatif |
| Features actives | Combien d'informations l'agent utilise sur les 12 disponibles. Moins = plus simple = souvent mieux |
| ROI | Le pourcentage de gain par rapport a ce qui est mise |

### Quand ca tourne ?

- **Automatiquement** chaque dimanche a 3h du matin -- l'agent se re-optimise avec les dernieres donnees
- **A la demande** via le bouton "Lancer Optimisation" dans le dashboard (prend environ 30 a 60 secondes)

Apres chaque optimisation, l'agent est automatiquement mis a jour avec les meilleurs reglages trouves.

### Amelioration manuelle par IA

En plus de l'optimisation des reglages, on peut lancer des sessions Claude Code pour modifier le code de l'agent lui-meme : ajouter de nouvelles informations, en retirer, changer la logique de decision. Chaque modification est testee, et on ne garde que ce qui ameliore les resultats. Le fichier `autoresearch_program.md` a la racine du projet contient les instructions pour ces sessions.

## Taches automatiques

| Tache | Frequence | Ce qu'elle fait |
|-------|-----------|-----------------|
| Evaluation | Toutes les 2h | L'agent regarde les recommandations du jour et decide quoi parier |
| Settlement | Tous les jours a 9h | Verifie les resultats des matchs et met a jour les paris (gagne/perdu) |
| Re-optimisation | Chaque dimanche a 3h | Cherche les meilleurs reglages et met a jour l'agent |

## Qualite des predictions

- **Brier Score** : mesure la precision des probabilites predites. En dessous de 0.25, c'est mieux que de tirer a pile ou face.
- **Calibration** : quand l'agent dit "ce joueur a 30% de chances de marquer", est-ce qu'il marque vraiment environ 30% du temps ? Le graphique de calibration compare les predictions aux resultats reels.
