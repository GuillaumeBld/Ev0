import math
import pytest
from app.pricing.goalscorer import calculate_supersub_prob
from app.pricing.assist import calculate_supersub_prob_assist


class TestCalculateSupersubProb:

    def test_no_sub_equals_standard(self):
        lambda_A = 0.30
        result = calculate_supersub_prob(lambda_A=lambda_A, p_sub=0.0, t_sub=65.0, lambda_B_sub=0.18)
        expected = 1 - math.exp(-lambda_A)
        assert result == pytest.approx(expected, abs=1e-6)

    def test_supersub_always_gte_standard(self):
        # lambda_B_sub doit être > lambda_A pour que supersub >= standard
        # (le remplacement doit apporter un lambda effectif >= lambda du titulaire)
        lambda_A = 0.20
        standard = 1 - math.exp(-lambda_A)
        supersub = calculate_supersub_prob(lambda_A=lambda_A, p_sub=0.60, t_sub=65.0, lambda_B_sub=0.25)
        assert supersub >= standard

    def test_sub_premium_increases_with_p_sub(self):
        lambda_A = 0.15
        p1 = calculate_supersub_prob(lambda_A, p_sub=0.30, t_sub=65.0, lambda_B_sub=0.18)
        p2 = calculate_supersub_prob(lambda_A, p_sub=0.70, t_sub=65.0, lambda_B_sub=0.18)
        assert p2 > p1

    def test_sub_premium_increases_with_earlier_sub(self):
        lambda_A = 0.15
        p_late  = calculate_supersub_prob(lambda_A, p_sub=0.60, t_sub=80.0, lambda_B_sub=0.18)
        p_early = calculate_supersub_prob(lambda_A, p_sub=0.60, t_sub=55.0, lambda_B_sub=0.18)
        assert p_early > p_late

    def test_formula_exact(self):
        lambda_A = 0.20; p_sub = 0.60; t_sub = 65.0; lambda_B = 0.18
        lA_adj = lambda_A * (t_sub / 90)
        lB_adj = lambda_B * ((90 - t_sub) / 90)
        expected = (1 - p_sub) * (1 - math.exp(-lambda_A)) + p_sub * (1 - math.exp(-(lA_adj + lB_adj)))
        result = calculate_supersub_prob(lambda_A, p_sub, t_sub, lambda_B)
        assert result == pytest.approx(expected, abs=1e-9)


class TestCalculateSupersubProbAssist:

    def test_no_sub_equals_standard_assist(self):
        lambda_A = 0.12
        result = calculate_supersub_prob_assist(lambda_A=lambda_A, p_sub=0.0, t_sub=65.0, lambda_B_sub=0.10)
        expected = 1 - math.exp(-lambda_A)
        assert result == pytest.approx(expected, abs=1e-6)

    def test_assist_supersub_gte_standard(self):
        lambda_A = 0.10
        standard = 1 - math.exp(-lambda_A)
        supersub = calculate_supersub_prob_assist(lambda_A=lambda_A, p_sub=0.50, t_sub=65.0, lambda_B_sub=0.10)
        assert supersub >= standard
