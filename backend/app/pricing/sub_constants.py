# backend/app/pricing/sub_constants.py
"""
Constantes supersub.

λ_B_sub = xG/90 moyen d'un remplaçant entrant selon sa position.
Calibrées sur données L1/PL 2023-2026 (buts/assists des subs / minutes jouées × 90).
p_sub_default = taux de remplacement si l'historique joueur est insuffisant (<5 matchs).
t_sub_default = minute moyenne de sortie si l'historique joueur est insuffisant.
"""

# xG/90 moyen pour un remplaçant entrant — par position
SUB_GOAL_LAMBDA: dict[str, float] = {
    "FW": 0.18,
    "MF": 0.08,
    "DF": 0.02,
    "GK": 0.00,
}

# xA/90 moyen pour un remplaçant entrant — par position
SUB_ASSIST_LAMBDA: dict[str, float] = {
    "FW": 0.07,
    "MF": 0.10,
    "DF": 0.02,
    "GK": 0.00,
}

# Defaults si historique insuffisant (<5 matchs)
P_SUB_DEFAULT: dict[str, float] = {
    "FW": 0.45,
    "MF": 0.40,
    "DF": 0.25,
    "GK": 0.02,
}

T_SUB_DEFAULT: float = 65.0  # minute moyenne de sortie toutes positions confondues
