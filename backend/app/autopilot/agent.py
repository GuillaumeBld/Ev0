"""Linear Q-learning agent for autopilot bet sizing.

Q(s, a) = W[a] @ features
Update: W[a] += alpha * (reward - Q(s,a)) * features

Actions map to Kelly fraction multipliers:
  0 → 0.0  (skip)
  1 → 0.25 (quarter Kelly)
  2 → 0.5  (half Kelly)
  3 → 1.0  (full Kelly)
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from app.autopilot.features import FEATURE_DIM, FEATURE_NAMES

logger = logging.getLogger(__name__)

ACTIONS: list[float] = [0.0, 0.25, 0.5, 1.0]
ACTION_LABELS: list[str] = ["skip", "quarter_kelly", "half_kelly", "kelly"]
N_ACTIONS = len(ACTIONS)


class LinearQAgent:
    """Pure-numpy linear Q-learning agent.

    The weight matrix W has shape (N_ACTIONS, FEATURE_DIM).
    Initialised with small random weights so early actions are nearly uniform.
    """

    def __init__(
        self,
        alpha: float = 0.01,
        gamma: float = 0.0,
        epsilon: float = 1.0,
        epsilon_min: float = 0.10,
        epsilon_decay: float = 0.995,
    ) -> None:
        self.alpha = alpha
        self.gamma = gamma
        self.epsilon = epsilon
        self.epsilon_min = epsilon_min
        self.epsilon_decay = epsilon_decay

        # Weight matrix — small random init
        rng = np.random.default_rng(42)
        self.W: np.ndarray = rng.normal(0, 0.01, size=(N_ACTIONS, FEATURE_DIM))

        # Training stats
        self.steps_trained: int = 0

    # ── Inference ────────────────────────────────────────────────

    def q_values(self, features: np.ndarray) -> np.ndarray:
        """Return Q(s, a) for all actions. Shape: (N_ACTIONS,)."""
        return self.W @ features  # (4, 10) @ (10,) → (4,)

    def act(self, features: np.ndarray, explore: bool = False) -> int:
        """Select action index.

        If explore=True: ε-greedy (used during training).
        If explore=False: greedy (used during live inference).
        """
        if explore and np.random.random() < self.epsilon:
            return int(np.random.randint(N_ACTIONS))
        return int(np.argmax(self.q_values(features)))

    # ── Learning ─────────────────────────────────────────────────

    def update(self, features: np.ndarray, action_idx: int, reward: float) -> float:
        """Single TD(0) update (γ=0 → bandit-style, no bootstrapping).

        Returns the absolute TD error for logging.
        """
        q = self.q_values(features)
        td_error = reward - q[action_idx]
        self.W[action_idx] += self.alpha * td_error * features

        # Decay epsilon
        if self.epsilon > self.epsilon_min:
            self.epsilon = max(self.epsilon_min, self.epsilon * self.epsilon_decay)

        self.steps_trained += 1
        return float(abs(td_error))

    # ── Persistence ──────────────────────────────────────────────

    def save(self, path: str | Path) -> None:
        """Persist weights + hyperparameters as a .npz archive."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            path,
            W=self.W,
            meta=np.array(
                [self.alpha, self.gamma, self.epsilon, self.epsilon_min, self.epsilon_decay],
                dtype=np.float64,
            ),
            steps=np.array([self.steps_trained], dtype=np.int64),
        )
        logger.debug("Agent saved to %s (steps=%d)", path, self.steps_trained)

    @classmethod
    def load(cls, path: str | Path) -> LinearQAgent:
        """Load from .npz file created by save()."""
        path = Path(path)
        data = np.load(path)
        meta = data["meta"]
        agent = cls(
            alpha=float(meta[0]),
            gamma=float(meta[1]),
            epsilon=float(meta[2]),
            epsilon_min=float(meta[3]),
            epsilon_decay=float(meta[4]),
        )
        agent.W = data["W"]
        agent.steps_trained = int(data["steps"][0])
        logger.debug("Agent loaded from %s (steps=%d)", path, agent.steps_trained)
        return agent

    # ── Interpretability ─────────────────────────────────────────

    def feature_importance(self) -> dict[str, float]:
        """Return |W[best_action]| per feature name (for the highest-Q action)."""
        # Use the action weights most associated with positive decisions (skip excluded)
        active_w = self.W[1:]  # actions 1-3 (skip=0 excluded)
        mean_abs = np.abs(active_w).mean(axis=0)
        return {name: float(v) for name, v in zip(FEATURE_NAMES, mean_abs, strict=False)}

    def to_dict(self) -> dict:
        """Serialise for API responses."""
        return {
            "alpha": self.alpha,
            "epsilon": round(self.epsilon, 4),
            "steps_trained": self.steps_trained,
            "feature_importance": self.feature_importance(),
        }
