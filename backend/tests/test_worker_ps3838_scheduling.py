"""C2 : l'ancrage PS3838 doit tourner au demarrage et toutes les heures,
pas seulement via un cron quotidien a 06:00 UTC (seize heures de trou sinon
si le worker est deploye l'apres-midi, et aucun ancrage pour une fixture
creee le matin pour un match du soir)."""
from apscheduler.triggers.interval import IntervalTrigger

from app.worker import create_scheduler


def test_resolve_ps3838_anchors_job_runs_hourly():
    scheduler = create_scheduler()
    job = scheduler.get_job("resolve_ps3838_anchors")
    assert job is not None
    assert isinstance(job.trigger, IntervalTrigger)
    assert job.trigger.interval.total_seconds() == 3600
    assert job.max_instances == 1
    assert job.coalesce is True
