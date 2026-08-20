import math

import pytest

from app.services.market_xg import (
    _p_poisson_over_2_5,
    solve_lambda_t,
    solve_lambda_t_from_line,
)


def _poisson_cdf(k: int, lam: float) -> float:
    return sum(math.exp(-lam) * lam**i / math.factorial(i) for i in range(k + 1))


def test_half_integer_line_matches_legacy_solver():
    """Sur 2.5, le nouveau solveur doit redonner exactement l'ancien."""
    for p in (0.35, 0.50, 0.62):
        assert solve_lambda_t_from_line(p, 2.5) == pytest.approx(solve_lambda_t(p), abs=1e-6)


def test_half_integer_line_3_5():
    lam = solve_lambda_t_from_line(0.40, 3.5)
    assert 1 - _poisson_cdf(3, lam) == pytest.approx(0.40, abs=1e-6)


def test_integer_line_excludes_the_push():
    """Ligne 3.0 : over = total >= 4, under = total <= 2, total == 3 rembourse.
    Le devig a deux issues donne P(over | pas de remboursement)."""
    lam = solve_lambda_t_from_line(0.45, 3.0)
    p_hi = 1 - _poisson_cdf(3, lam)
    p_lo = _poisson_cdf(2, lam)
    assert p_hi / (p_hi + p_lo) == pytest.approx(0.45, abs=1e-6)


def test_integer_line_differs_from_naive_half_integer_treatment():
    """Traiter 3.0 comme 2.5 donnerait un lambda sensiblement different."""
    assert solve_lambda_t_from_line(0.45, 3.0) != pytest.approx(
        solve_lambda_t_from_line(0.45, 2.5), abs=0.05
    )


def test_cross_validation_uses_the_actual_line():
    """Un ajustement correct sur une ligne 3.0 ne doit pas etre signale a tort."""
    from app.services.market_xg import cross_validate_line, p_over_model

    lam = solve_lambda_t_from_line(0.45, 3.0)
    lh = lam * 0.6
    la = lam - lh
    p_home = __import__("app.services.market_xg", fromlist=["_poisson_home_win"])._poisson_home_win(lh, la)
    ok, reason = cross_validate_line(lh, la, 0.45, p_home, 3.0)
    assert ok, reason
    # La prediction doit bien etre calculee dans la convention de la ligne 3.0
    assert p_over_model(lam, lh, la, 3.0) == pytest.approx(0.45, abs=1e-6)


def test_unreachable_probability_raises():
    with pytest.raises(ValueError):
        solve_lambda_t_from_line(0.999999, 2.5)


def test_regression_atletico_malaga_real_odds():
    """Les vraies cotes Pinnacle du 19/08 doivent redonner un Atletico tres
    favori — et surtout pas le 1.07 / 1.02 produit par les cotes Betclic
    erronees."""
    from app.services.market_xg import (
        multiplicative_devig,
        solve_lambda_home_from_h2h,
    )

    p_home, _, _ = multiplicative_devig([1.347, 5.35, 9.46])
    p_over = multiplicative_devig([1.854, 2.04])[0]
    lt = solve_lambda_t_from_line(p_over, 3.0)
    lh = solve_lambda_home_from_h2h(lt, p_home)
    la = lt - lh
    assert lh > 1.6, f"lambda domicile trop faible: {lh}"
    assert la < 0.9, f"lambda exterieur trop eleve: {la}"
    assert lh / la > 2.0
