"""Regle du nouveau plus haut : une value deja signalee ne resonne que sur record."""
from app.services.recommendation_service import ALERT_RISE_RATIO, should_alert


def test_never_alerted_always_alerts():
    assert should_alert(None, 2.50) is True


def test_small_rise_stays_silent():
    # 2.50 -> 2.57 = +2.8 %, sous le seuil
    assert should_alert(2.50, 2.57) is False


def test_rise_above_threshold_alerts():
    # Seuil = 2.50 * 1.05 = 2.625. On teste de part et d'autre SANS toucher la
    # valeur exacte : 2.5 * 1.05 vaut 2.6250000000000004 en binaire, une
    # comparaison sur la borne serait un test fragile.
    assert should_alert(2.50, 2.62) is False
    assert should_alert(2.50, 2.63) is True
    assert should_alert(2.50, 2.70) is True


def test_drop_stays_silent():
    assert should_alert(2.50, 2.10) is False


def test_ratio_is_five_percent():
    assert ALERT_RISE_RATIO == 1.05


def test_yoyo_only_alerts_once_when_level_never_beaten():
    """2.50 (alerte) -> 2.60 -> 2.45 -> 2.58 : aucun nouveau plus haut suffisant."""
    alerted = 2.50
    for odds in (2.60, 2.45, 2.58):
        assert should_alert(alerted, odds) is False


def test_staircase_alerts_at_each_new_high():
    """Une montee franche redeclenche a chaque palier franchi."""
    alerted = 2.50
    fired = []
    for odds in (2.55, 2.70, 2.80, 3.00):
        if should_alert(alerted, odds):
            fired.append(odds)
            alerted = odds
    assert fired == [2.70, 3.00]
