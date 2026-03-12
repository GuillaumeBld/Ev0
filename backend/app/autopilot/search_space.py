"""Optuna search space for agent hyperparameter optimization."""

SEARCH_SPACE = {
    # Agent hyperparameters
    "alpha": {"type": "loguniform", "low": 1e-4, "high": 0.1},
    "epsilon_decay": {"type": "uniform", "low": 0.98, "high": 0.999},
    "epsilon_init": {"type": "uniform", "low": 0.2, "high": 0.8},
    "l1_lambda": {"type": "loguniform", "low": 1e-6, "high": 0.01},

    # Feature toggles (booleans — which features to include)
    "use_edge": {"type": "categorical", "choices": [True]},       # always on
    "use_confidence": {"type": "categorical", "choices": [True]},  # always on
    "use_implied_prob": {"type": "categorical", "choices": [True, False]},
    "use_fair_prob": {"type": "categorical", "choices": [True, False]},
    "use_lambda": {"type": "categorical", "choices": [True, False]},
    "use_mins_ratio": {"type": "categorical", "choices": [True, False]},
    "use_is_goalscorer": {"type": "categorical", "choices": [True, False]},
    "use_is_premier_league": {"type": "categorical", "choices": [True, False]},
    "use_is_forward": {"type": "categorical", "choices": [True, False]},
    "use_edge_rank": {"type": "categorical", "choices": [True, False]},
    "use_n_recs_today": {"type": "categorical", "choices": [True, False]},
    "use_odds_movement": {"type": "categorical", "choices": [True, False]},

    # Training
    "n_epochs": {"type": "int", "low": 1, "high": 5},

    # Prior weights for supervised init
    "prior_edge_weight": {"type": "uniform", "low": 0.5, "high": 4.0},
    "prior_conf_weight": {"type": "uniform", "low": 0.2, "high": 2.0},
}
