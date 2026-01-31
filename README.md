# Ev0 - Prematch Value Engine (Documentation)

Ce dépôt contient la spécification technique complète, les modèles mathématiques et les protocoles opérationnels du moteur de pricing **Ev0**.

Il sert de référence unique ("Source of Truth") pour le développement du système.

## 📚 Table des Matières

### Vision & Architecture
*   [00-Overview](docs/00-overview.md) : Vision produit, objectifs et périmètre.
*   [01-Architecture](docs/01-architecture.md) : Diagrammes de flux, services et stack technique.
*   [17-PRD](docs/17-prd.md) : Product Requirements Document (Problème/Solution).

### Données
*   [02-Data Sources](docs/02-data-sources.md) : Sources (FBref, Bookmakers) et protocoles de robustesse.
*   [18-Data Contracts](docs/18-data-contracts.md) : Schémas JSON stricts (Input/Output/Logs).
*   [Data Dictionary](docs/data-dictionary.md) : Définitions précises des métriques (npxG, xA, etc.).

### Cœur Mathématique (Pricing)
*   [03-Modeling](docs/03-modeling.md) : Les formules mathématiques (Poisson, Ajustements).
*   [04-Odds Normalization](docs/04-odds-normalization.md) : Retrait de marge et nettoyage des cotes.
*   [06-Strategy](docs/06-strategy.md) : Règles de sélection, filtres (Titulaires) et calcul d'Edge.

### Validation & Risque
*   [05-Backtesting](docs/05-backtesting.md) : Protocole scientifique de validation (Walk-forward).
*   [07-Risk Management](docs/07-risk-management.md) : Gestion de bankroll et exposition.
*   [13-Security Compliance](docs/13-security-compliance.md) : Sécurité des clés et respect des sources.

### Opérationnel
*   [08-Monitoring](docs/08-monitoring.md) : Alertes et surveillance de la qualité des données.
*   [09-UI/UX](docs/09-ui-ux.md) : Spécifications de l'interface opérateur.
*   [10-API Spec](docs/10-api-spec.md) : Endpoints de l'API Backend.
*   [11-Database Schema](docs/11-database-schema.md) : Modèle de données relationnel.
*   [12-Dev Setup](docs/12-dev-setup.md) : Guide de mise en place de l'environnement.
*   [14-Roadmap](docs/14-roadmap-todo.md) : Plan de développement immédiat.
*   [20-Roadmap-Future](docs/20-roadmap-future.md) : Améliorations et perspectives d'évolutions (V2+).
*   [Ops/Runbook](ops/runbook.md) : Procédures d'incident.

## 🏗️ Statut du Projet

*   **Phase Actuelle** : Spécification (V1.0 Locked)
*   **Prochaine Phase** : Implémentation (Repo séparé)

## ⚖️ Principes Clés

1.  **Qualité > Quantité** : On ne parie que sur les configurations statistiquement robustes (Titulaires, >500 matchs backtestés).
2.  **Transparence** : Chaque probabilité sortie par le modèle doit être explicable par ses inputs (npxG, forme, adversaire).
3.  **Rigueur** : Pas de "Leakage" dans les backtests. Pas de pari si données manquantes.