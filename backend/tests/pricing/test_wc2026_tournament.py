import math
import pytest
from app.pricing.wc2026_tournament import poisson_ge, run_monte_carlo


def test_poisson_ge_k1_lambda1():
    # P(X >= 1) = 1 - e^(-1) for lambda=1
    assert abs(poisson_ge(1.0, 1) - (1 - math.exp(-1))) < 1e-10


def test_poisson_ge_k2_lambda1():
    # P(X >= 2) = 1 - e^(-1)(1 + 1) for lambda=1
    expected = 1 - math.exp(-1) * (1 + 1)
    assert abs(poisson_ge(1.0, 2) - expected) < 1e-10


def test_poisson_ge_k1_lambda0():
    assert poisson_ge(0.0, 1) == pytest.approx(0.0, abs=1e-10)


def test_poisson_ge_k4_lambda3():
    lam = 3.0
    cdf3 = math.exp(-lam) * (1 + lam + lam**2 / 2 + lam**3 / 6)
    assert abs(poisson_ge(lam, 4) - (1 - cdf3)) < 1e-10


def test_monte_carlo_top_scorer_sums_to_one():
    lambdas_g = [3.0, 2.0, 1.5, 1.0, 0.5, 0.2]
    lambdas_a = [1.5, 1.0, 0.8, 0.5, 0.3, 0.1]
    results = run_monte_carlo(lambdas_g, lambdas_a, n_sim=20_000, seed=42)
    assert abs(sum(r["p_top_scorer"]   for r in results) - 1.0) < 0.02
    assert abs(sum(r["p_top_assister"] for r in results) - 1.0) < 0.02


def test_monte_carlo_highest_lambda_wins_most():
    lambdas_g = [5.0, 1.0, 0.5]
    lambdas_a = [3.0, 1.0, 0.5]
    results = run_monte_carlo(lambdas_g, lambdas_a, n_sim=20_000, seed=42)
    assert results[0]["p_top_scorer"]   > results[1]["p_top_scorer"]
    assert results[0]["p_top_assister"] > results[1]["p_top_assister"]
