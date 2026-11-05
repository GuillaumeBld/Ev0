"""Ingestion footylogos.com : resolution des ecussons de clubs
(`canonical_teams.crest_url`), gratuite et sans cle.

Voir `resolve_crests.py` pour l'algorithme complet (dictionnaire FR->EN
seed, slug direct, variantes d'affixe/suffixe, repli sur la page pays
footylogos avec match par similarite). Execution manuelle uniquement
(comme `app/scripts/build_full_referentiel.py`) — non cablee dans
`worker.py`.
"""
