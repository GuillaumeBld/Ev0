"""Surveillance de l'expiration des certificats TLS dans le rapport de sante."""
from datetime import UTC, datetime, timedelta

from app.worker import _CERT_WARN_DAYS, _cert_red_flags, _cert_status

NOW = datetime(2026, 8, 19, 8, 0, tzinfo=UTC)


def _fetcher(mapping):
    """fetch(host) -> notAfter, pilote par un dict host -> jours restants (ou None)."""
    def fetch(host: str):
        days = mapping.get(host, "absent")
        if days == "absent":
            raise AssertionError(f"hote inattendu: {host}")
        return None if days is None else NOW + timedelta(days=days)
    return fetch


def test_threshold_is_three_weeks():
    assert _CERT_WARN_DAYS == 21


def test_healthy_certificates_raise_no_flag():
    st = _cert_status(["a.test", "b.test"], now=NOW, fetch=_fetcher({"a.test": 89, "b.test": 45}))
    assert st == [("a.test", 89), ("b.test", 45)]
    assert _cert_red_flags(st) == []


def test_certificate_just_above_threshold_stays_silent():
    st = _cert_status(["a.test"], now=NOW, fetch=_fetcher({"a.test": 21}))
    assert _cert_red_flags(st) == []


def test_certificate_below_threshold_is_red():
    st = _cert_status(["a.test"], now=NOW, fetch=_fetcher({"a.test": 20}))
    flags = _cert_red_flags(st)
    assert len(flags) == 1
    assert "a.test" in flags[0]
    assert "20" in flags[0]


def test_expired_certificate_is_red():
    st = _cert_status(["a.test"], now=NOW, fetch=_fetcher({"a.test": -3}))
    assert st == [("a.test", -3)]
    assert len(_cert_red_flags(st)) == 1


def test_unreachable_host_does_not_cry_wolf():
    """Un echec de lecture ne doit pas declencher d'alerte : Traefik renouvelle
    a 30 jours et on verifie tous les jours, quelques echecs sont sans gravite."""
    st = _cert_status(["a.test"], now=NOW, fetch=_fetcher({"a.test": None}))
    assert st == [("a.test", None)]
    assert _cert_red_flags(st) == []


def test_only_the_failing_domain_is_flagged():
    st = _cert_status(
        ["ok.test", "ko.test"], now=NOW, fetch=_fetcher({"ok.test": 60, "ko.test": 5})
    )
    flags = _cert_red_flags(st)
    assert len(flags) == 1
    assert "ko.test" in flags[0]


def test_no_domains_configured_is_a_noop():
    assert _cert_status([], now=NOW, fetch=_fetcher({})) == []
    assert _cert_red_flags([]) == []
