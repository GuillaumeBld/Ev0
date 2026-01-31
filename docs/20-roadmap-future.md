# Améliorations et Perspectives d'Évolution

Ce document recense les axes de recherche et développement pour les versions futures du moteur Ev0. Il sert de "Backlog" stratégique.

---

## 🚀 Axe 1 : Calibration Data-Driven (Passeurs)
**État actuel** : Poids experts (xA 45%, Key Passes 25%...).
**Évolution** : Utiliser une **Régression Logistique** sur un dataset historique massif (>10 000 matchs).
*   **Objectif** : Déterminer mathématiquement le poids optimal de chaque métrique.
*   **Indicateur de succès** : Réduction du Brier Score sur le module Passeur.

## 🛡️ Axe 2 : Analyse de Duel Direct (Micro-Matchup)
**État actuel** : Ajustement via xGA global de l'adversaire.
**Évolution** : Intégrer la faiblesse spécifique de la zone d'évolution du joueur.
*   **Concept** : Si l'attaquant joue à gauche, pondérer son Lambda par les stats défensives du Latéral Droit adverse (dribbles subis, tacles manqués).
*   **Objectif** : Exploiter les "maillons faibles" tactiques non détectés par les cotes globales.

## 📊 Axe 3 : Extension aux Marchés de Volume (Tirs Cadrés)
**État actuel** : Buteur, Passeur.
**Évolution** : Pricing des **Shots on Target (SoT)**.
*   **Concept** : Utiliser la même logique Poisson (Lambda_SoT = Shots/90 * SoT_Rate).
*   **Pourquoi ?** Moins de variance que les buts, liquidité élevée sur les bookmakers FR.

## 🔗 Axe 4 : Corrélation et Bet-Builders
**État actuel** : Paris simples uniquement.
**Évolution** : Pricing des probabilités conjointes (ex: Buteur + Résultat Match).
*   **Concept** : Exploiter les erreurs de tarification des bookmakers sur les corrélations dépendantes du Game State.

## 🕒 Axe 5 : Modélisation du "Game State"
**État actuel** : Intensité constante sur 90 min.
**Évolution** : Intégrer l'impact du score sur l'intensité offensive.
*   **Concept** : Réduire le Lambda des favoris s'ils mènent au score (gestion de l'effort) et augmenter celui des outsiders s'ils sont menés (prise de risque).

---

## 📝 Historique des idées proposées
*   [2026-01-30] : Création du document.
*   [2026-01-30] : Ajout de la logique de pondération par poste (Intégré en V1).
*   [2026-01-30] : Ajout de l'Override Manuel via xG Market (Intégré en V1).
