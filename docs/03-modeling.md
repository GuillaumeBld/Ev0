# Modélisation

## Philosophie
Deux modules de pricing distincts avec des approches adaptées à la complexité de chaque marché.

---

## 🆕 Mode Override (Top-Down)

Par défaut, le modèle est **Bottom-Up** (somme des joueurs).
L'opérateur peut activer le mode **Top-Down** en fournissant le Team xG.

> **Rationale Stratégique** : Le marché (Pinnacle) est efficient sur le nombre de buts total d'une équipe, mais souvent absent ou inefficace sur les buteurs individuels.
> En calant le total de l'équipe sur la "Vérité Marché", on élimine le biais de prédiction du match pour ne chasser que l'inefficience de la **répartition** (Market Share) entre les joueurs.

---

## 🎯 Module Buteur (Anytime Goalscorer)

### Approche
Modèle Poisson mixte : **Open Play + Pénaltys**.

*(Section Buteur inchangée)*

---

## 🅰️ Module Passeur (Anytime Assist)

### Approche "Smart Weights" (Poids par Profil)
Au lieu d'un modèle unique, nous utilisons des pondérations adaptées au rôle tactique du joueur.

### 1. Profils de Pondération (Hypothèse V1)

Ces poids sont des **hyperparamètres initiaux**. Ils ont vocation à être ajustés (fine-tuning) selon les résultats observés en Backtest.

| Métrique | **Créateur Axial** (MF/AM) | **Ailier / Latéral** (W/FB) | **Attaquant** (FW) |
|----------|----------------------------|-----------------------------|--------------------|
| `xA_per_90` | **40%** | **35%** | **50%** |
| `Key Passes` | **30%** | **20%** | **20%** |
| `SCA` | **20%** | **10%** | **30%** |
| `Passes Surface` | **10%** | **10%** | **0%** |
| `Centres` | **0%** | **25%** | **0%** |

> **Note sur le SCA (15-20%)** : Ce poids est volontairement limité pour éviter l'effet "Hockey Assist" (valoriser un joueur qui fait l'avant-dernière passe mais rarement la dernière). Seule la calibration post-backtest permettra de valider si 15% est le point d'équilibre optimal.

### 2. Détection du Profil
*   **Par Défaut** : Basé sur le poste FBref (`MF`, `DF`, `FW`).
*   **Ajustement War Room (OOP)** : Si un joueur change de poste (ex: Latéral aligné Ailier), l'opérateur force le profil `Ailier` dans l'interface Lineup.

### Formule adaptative

```python
def calculate_assist_lambda(player_stats, position_profile):
    # Récupérer les poids selon le profil
    weights = GET_WEIGHTS(position_profile) 
    
    # Somme pondérée normalisée
    creation_score = sum(
        weights[metric] * (player_stats[metric] / league_avg[metric])
        for metric in weights
    )
    
    return creation_score * CALIBRATION_CONSTANT
```

### Paramètres recommandés

| Paramètre | Valeur | Justification |
|-----------|--------|---------------|
| Fenêtre forme | 15-20 matchs | Variance élevée des assists |
| Decay λ (forme) | 0.017 | Half-life 40 jours |

### Confiance: **80% HIGH** ✅
Nettement améliorée grâce à la spécialisation par poste.

---

## 💰 Retrait de Marge & Calibration

*(Voir sections précédentes inchangées)*
