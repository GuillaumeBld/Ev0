"""Métriques d'évaluation — valeurs vérifiées à la main."""

import math

import pytest

from app.evaluation.metrics import (
    brier_score,
    calibration_bins,
    log_loss,
    paired_delta_log_loss,
)


class TestLogLoss:
    def test_prediction_parfaite(self):
        assert log_loss([1.0, 0.0], [True, False]) == pytest.approx(0.0, abs=1e-9)

    def test_valeur_calculee_a_la_main(self):
        # -(ln(0.8) + ln(1-0.4)) / 2
        expected = -(math.log(0.8) + math.log(0.6)) / 2
        assert log_loss([0.8, 0.4], [True, False]) == pytest.approx(expected)

    def test_proba_zero_sur_succes_clippee_pas_infinie(self):
        assert math.isfinite(log_loss([0.0], [True]))

    def test_listes_incoherentes(self):
        with pytest.raises(ValueError):
            log_loss([0.5], [True, False])
        with pytest.raises(ValueError):
            log_loss([], [])


class TestBrier:
    def test_valeur_a_la_main(self):
        # ((0.8-1)^2 + (0.4-0)^2) / 2 = (0.04 + 0.16)/2 = 0.10
        assert brier_score([0.8, 0.4], [True, False]) == pytest.approx(0.10)


class TestCalibrationBins:
    def test_regroupement_et_frequences(self):
        probs = [0.05, 0.15, 0.15, 0.95]
        outcomes = [False, True, False, True]
        bins = calibration_bins(probs, outcomes, n_bins=10)
        b0 = next(b for b in bins if b.count and b.low == pytest.approx(0.0))
        assert b0.count == 1 and b0.hit_rate == 0.0
        b1 = next(b for b in bins if b.count == 2)
        assert b1.low == pytest.approx(0.1)
        assert b1.avg_prob == pytest.approx(0.15)
        assert b1.hit_rate == pytest.approx(0.5)

    def test_proba_1_tombe_dans_le_dernier_bin(self):
        bins = calibration_bins([1.0], [True], n_bins=10)
        assert bins[-1].count == 1


class TestPairedDelta:
    def test_b_meilleur_delta_positif(self):
        # A dit 0.5, B dit 0.9, issue True → perte A > perte B → delta > 0
        res = paired_delta_log_loss([0.5], [0.9], [True])
        assert res.n == 1
        assert res.mean_delta > 0
        assert res.deltas[0] == pytest.approx(-math.log(0.5) + math.log(0.9))

    def test_modeles_identiques_delta_nul(self):
        res = paired_delta_log_loss([0.3, 0.7], [0.3, 0.7], [False, True])
        assert res.mean_delta == pytest.approx(0.0, abs=1e-12)
