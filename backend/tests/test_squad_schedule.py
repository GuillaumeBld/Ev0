"""Tests pour `schedule.should_run_today` (Tache 7 : cadence de la sync
effectifs Transfermarkt — quotidien en mercato, hebdo sinon).

Fonction pure (pas de DB/reseau) : tous les cas sont testes en memoire.
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.ingestion.transfermarkt.schedule import should_run_today

# --------------------------------------------------------------------------
# En fenetre mercato -> toujours "daily", quel que soit last_weekly.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("last_weekly", [None, dt.date(2026, 8, 1), dt.date(2026, 8, 14)])
def test_summer_window_is_daily(last_weekly):
    assert should_run_today(dt.date(2026, 8, 15), last_weekly) == "daily"


@pytest.mark.parametrize("last_weekly", [None, dt.date(2026, 1, 1), dt.date(2026, 1, 14)])
def test_winter_window_is_daily(last_weekly):
    assert should_run_today(dt.date(2026, 1, 15), last_weekly) == "daily"


# --------------------------------------------------------------------------
# Hors fenetre : hebdo si >= 7 jours depuis le dernier run reussi (ou jamais).
# --------------------------------------------------------------------------


def test_outside_window_weekly_when_last_run_8_days_ago():
    today = dt.date(2026, 10, 15)
    last_weekly = today - dt.timedelta(days=8)
    assert should_run_today(today, last_weekly) == "weekly"


def test_outside_window_weekly_when_last_run_exactly_7_days_ago():
    today = dt.date(2026, 10, 15)
    last_weekly = today - dt.timedelta(days=7)
    assert should_run_today(today, last_weekly) == "weekly"


def test_outside_window_none_when_last_run_3_days_ago():
    today = dt.date(2026, 10, 15)
    last_weekly = today - dt.timedelta(days=3)
    assert should_run_today(today, last_weekly) is None


def test_outside_window_weekly_when_never_run():
    assert should_run_today(dt.date(2026, 10, 15), None) == "weekly"


# --------------------------------------------------------------------------
# Bornes exactes des fenetres (incluses).
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "today",
    [dt.date(2026, 6, 10), dt.date(2026, 9, 2), dt.date(2026, 1, 1), dt.date(2026, 2, 3)],
)
def test_window_boundaries_are_inclusive(today):
    assert should_run_today(today, None) == "daily"


@pytest.mark.parametrize("today", [dt.date(2026, 6, 9), dt.date(2026, 9, 3)])
def test_day_just_outside_summer_window_is_not_daily(today):
    assert should_run_today(today, today) != "daily"


@pytest.mark.parametrize("today", [dt.date(2026, 12, 31), dt.date(2026, 2, 4)])
def test_day_just_outside_winter_window_is_not_daily(today):
    assert should_run_today(today, today) != "daily"
