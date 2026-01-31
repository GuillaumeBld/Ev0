# Stratégie de sélection

## Vue d'ensemble

Le système est **Dynamique**. Il opère sur deux fenêtres temporelles distinctes, chacune avec ses propres règles de gestion de risque.

---

## 1. Les Deux Fenêtres de Tir

### Phase A : Early Market (Anticipation)
*   **Timing** : De J-2 à H-2 avant le match.
*   **Source Titulaire** : Projection Statistique (Moyenne temps de jeu récent).
*   **Objectif** : Prendre de la value sur des joueurs sous-cotés par les bookmakers avant que le marché ne s'ajuste (Opening Odds).
*   **Risque** : Le joueur ne démarre pas (Risque de Void ou Perte selon bookmaker).
*   **Action** : Mises réduites (0.5 à 0.75 Unité).

### Phase B : Lineup Release (Réaction)
*   **Timing** : H-1 (Dès la sortie des compos officielles).
*   **Source Titulaire** : **Confirmé** (Officiel).
*   **Objectif** : "Sniper" les erreurs de pricing immédiates.
    *   Ex: Un joueur incertain est finalement titulaire → Sa proba monte en flèche, mais la cote met 5-10 min à baisser.
    *   Ex: Un joueur change de poste (Ailier passe en Buteur) → Value immédiate.
*   **Risque** : Très faible sur le temps de jeu (Certitude 90+ min).
*   **Action** : Mises pleines (1.0 à 1.5 Unité).

---

## 2. Règles d'Annulation (Void Rules)

### Règles Bookmakers (France)
*   **Règle Générale** : Si un joueur participe au match (même 1 seconde), les paris "Buteur" sont **ACTIFS**.
*   **Exception** : Certains books remboursent si le joueur ne débute pas.

### Conséquence Stratégie
*   **En Phase A (Early)** : On accepte le risque de rotation. On ne joue que les titulaires "Indiscutables" (>90% start rate).
*   **En Phase B (Lineup)** : Risque éliminé. On ne parie QUE sur les titulaires confirmés sur la feuille de match.

---

## 3. Calcul de l'Edge

```python
edge_pct = (model_prob * bookmaker_odds) - 1
```

### Validation Latency (Phase B surtout)
En phase Lineup, la vitesse est clé. On cherche les cotes qui n'ont pas encore bougé suite à l'annonce.

---

## 4. Filtres de sélection (Dynamiques)

| Filtre | Phase A (Early) | Phase B (Lineup) | Justification |
|--------|-----------------|------------------|---------------|
| `min_edge` | **5%** | **3%** | En confirmé, on accepte moins de marge car moins d'incertitude. |
| `min_minutes` | **Projected > 70** | **Confirmed Start** | En Early, on veut une marge de sécurité. |
| `max_odds` | **15.00** | **12.00** | On évite les tickets de loto en confirmé. |
| `penalty_taker` | Rejet si doute | Rejet si absent | En confirmé, on sait qui joue. |

---

## 5. Stake sizing

### Gestion de Mise par Phase

*   **Mise Standard** : 1 Unité (1% Bankroll).
*   **Ajustement Early** : `0.75 * Unité` (Pour compenser le risque d'info manquante).
*   **Ajustement Lineup** : `1.0 * Unité` (Pleine confiance).

---

## 6. Gestion de l'exposition

| Type | Limite |
|------|--------|
| **Par Match** | Max 4 Unités |
| **Par Joueur** | Max 1.5 Unités (Si cumul Early + Lineup sur le même joueur) |
| **Par Jour** | Max 15 Unités |

---

## 7. Pipeline de décision

```python
def select_bets(predictions, odds, phase="EARLY"):
    bets = []
    for pred in predictions:
        # Filtres Dynamiques
        min_mins = 70 if phase == "EARLY" else 0 # Si confirmed, min_mins géré par le statut
        min_edge = 0.05 if phase == "EARLY" else 0.03
        
        # 1. Filtre Titulaire
        if phase == "EARLY" and pred['projected_mins'] < min_mins: continue
        if phase == "LINEUP" and not pred['is_confirmed_starter']: continue
        
        # 2. Filtre Edge
        if pred['edge'] < min_edge: continue
        
        # 3. Sizing
        stake_factor = 0.75 if phase == "EARLY" else 1.0
        
        bets.append(create_bet(pred, stake=FLAT_UNIT * stake_factor))
        
    return apply_exposure_caps(bets)
```
