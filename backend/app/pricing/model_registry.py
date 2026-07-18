"""Registre des modèles de pricing — champion/challenger (spec 2026-07-18, §3.1).

Alpha = moteur actuel (team_xg.py), gelé, seul en prod jusqu'à bascule.
Beta = challenger calibré (lot 3). Ajouter un modèle = une entrée ici.
"""

MODEL_ALPHA = "alpha"
MODEL_BETA = "beta"
KNOWN_MODELS: tuple[str, ...] = (MODEL_ALPHA, MODEL_BETA)
DEFAULT_MODEL = MODEL_ALPHA

# Marchés snapshotés — convention "avec sub" en tête (spec §2)
KNOWN_MARKETS: tuple[str, ...] = ("goal_with_sub", "assist_with_sub", "goal", "assist")
