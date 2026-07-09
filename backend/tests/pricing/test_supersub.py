"""Tests de calculate_supersub_prob — sémantique du commit 61d72ea (03/07/2026).

P = P(CE joueur marque/passe, compte tenu de son risque de sortie à t_sub).
λ_B est exclu : un pari « joueur X marque » n'est pas gagné si son remplaçant
marque. Conséquence : le risque de substitution RÉDUIT la probabilité
(supersub <= standard), contrairement à l'ancienne formule.
"""
import math

import pytest

from app.pricing.assist import calculate_supersub_prob_assist
from app.pricing.goalscorer import calculate_supersub_prob


class TestCalculateSupersubProb:

    def test_no_sub_equals_standard(self):
        lambda_A = 0.30
        result = calculate_supersub_prob(lambda_A=lambda_A, p_sub=0.0, t_sub=65.0, lambda_B_sub=0.18)
        expected = 1 - math.exp(-lambda_A)
        assert result == pytest.approx(expected, abs=1e-6)

    def test_sub_risk_reduces_probability(self):
        # Le risque d'être remplacé réduit les minutes espérées → prob <= standard
        lambda_A = 0.20
        standard = 1 - math.exp(-lambda_A)
        supersub = calculate_supersub_prob(lambda_A=lambda_A, p_sub=0.60, t_sub=65.0, lambda_B_sub=0.25)
        assert supersub <= standard

    def test_probability_decreases_with_p_sub(self):
        lambda_A = 0.15
        p_low_risk = calculate_supersub_prob(lambda_A, p_sub=0.30, t_sub=65.0, lambda_B_sub=0.18)
        p_high_risk = calculate_supersub_prob(lambda_A, p_sub=0.70, t_sub=65.0, lambda_B_sub=0.18)
        assert p_high_risk < p_low_risk

    def test_probability_decreases_with_earlier_sub(self):
        # Sorti plus tôt = moins de minutes pour marquer
        lambda_A = 0.15
        p_late = calculate_supersub_prob(lambda_A, p_sub=0.60, t_sub=80.0, lambda_B_sub=0.18)
        p_early = calculate_supersub_prob(lambda_A, p_sub=0.60, t_sub=55.0, lambda_B_sub=0.18)
        assert p_early < p_late

    def test_formula_exact(self):
        lambda_A = 0.20
        p_sub = 0.60
        t_sub = 65.0
        lA_until_sub = lambda_A * (t_sub / 90)
        expected = (
            (1 - p_sub) * (1 - math.exp(-lambda_A))
            + p_sub * (1 - math.exp(-lA_until_sub))
        )
        result = calculate_supersub_prob(lambda_A, p_sub, t_sub, lambda_B_sub=0.18)
        assert result == pytest.approx(expected, abs=1e-9)

    def test_lambda_b_has_no_effect(self):
        # λ_B exclu de la formule : le remplaçant ne gagne pas le pari du titulaire
        lambda_A = 0.20
        a = calculate_supersub_prob(lambda_A, p_sub=0.5, t_sub=65.0, lambda_B_sub=0.05)
        b = calculate_supersub_prob(lambda_A, p_sub=0.5, t_sub=65.0, lambda_B_sub=0.90)
        assert a == pytest.approx(b, abs=1e-12)


class TestCalculateSupersubProbAssist:

    def test_no_sub_equals_standard_assist(self):
        lambda_A = 0.12
        result = calculate_supersub_prob_assist(lambda_A=lambda_A, p_sub=0.0, t_sub=65.0, lambda_B_sub=0.10)
        expected = 1 - math.exp(-lambda_A)
        assert result == pytest.approx(expected, abs=1e-6)

    def test_assist_sub_risk_reduces_probability(self):
        lambda_A = 0.10
        standard = 1 - math.exp(-lambda_A)
        supersub = calculate_supersub_prob_assist(lambda_A=lambda_A, p_sub=0.50, t_sub=65.0, lambda_B_sub=0.10)
        assert supersub <= standard
