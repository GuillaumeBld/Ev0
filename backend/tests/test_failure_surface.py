"""Tests du garde-fou anti-mort-silencieuse squad-sync (`failure_surface`) :
issue + PR "correctif d'attente" GitHub sur un run failed/partial, avec
degradation propre (CRITICAL, jamais de crash) si aucun token n'est
configure ou si l'API GitHub est en echec."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from app.ingestion.transfermarkt.failure_surface import surface_failure
from app.models.squad_sync import SquadSyncRun

REPO = "GuillaumeBld/Ev0"
RUN_DATE = "2026-08-15"
BRANCH = f"squad-sync/parse-failure-{RUN_DATE}"

SAMPLES = {100: "<html>club 100 KO</html>"}


def _run(*, status="failed", clubs_failed=2, clubs_total=5, detail=None) -> SquadSyncRun:
    return SquadSyncRun(
        id=1,
        started_at=datetime(2026, 8, 15, 3, 0, tzinfo=UTC),
        finished_at=datetime(2026, 8, 15, 3, 5, tzinfo=UTC),
        mode="daily",
        clubs_total=clubs_total,
        clubs_ok=clubs_total - clubs_failed,
        clubs_failed=clubs_failed,
        players_updated=0,
        players_detached=0,
        status=status,
        detail=detail
        if detail is not None
        else {
            "failed_clubs": [
                {"canonical_team_id": 1, "transfermarkt_club_id": 100, "status": "structure_error"},
                {"canonical_team_id": 2, "transfermarkt_club_id": 200, "status": "empty"},
            ],
        },
    )


def _settings(token: str = "tok", repo: str = REPO) -> SimpleNamespace:
    return SimpleNamespace(github_token=token, github_repo=repo)


class FakeResponse:
    def __init__(self, status_code: int = 200, json_data: dict | None = None):
        self.status_code = status_code
        self._json = json_data or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)  # type: ignore[arg-type]

    def json(self) -> dict:
        return self._json


class FakeGithubClient:
    """Client GitHub factice : route GET/POST par pattern d'URL, enregistre
    tous les appels, simule l'idempotence (issue/branche deja existante) et
    peut simuler une panne reseau/API."""

    def __init__(self, *, existing_issue: bool = False, existing_branch: str | None = None,
                 blow_up: bool = False):
        self.calls: list[tuple[str, str, dict]] = []
        self.existing_issue = existing_issue
        self.existing_branch = existing_branch
        self.blow_up = blow_up
        self._blob_counter = 0

    async def get(self, url, headers=None, params=None):
        self.calls.append(("GET", url, {"headers": headers, "params": params}))
        if self.blow_up:
            raise RuntimeError("reseau mort")
        if url.endswith("/search/issues"):
            items = [{"number": 42}] if self.existing_issue else []
            return FakeResponse(200, {"items": items})
        if "/git/ref/heads/" in url:
            branch = url.rsplit("/git/ref/heads/", 1)[1]
            if branch == "main":
                return FakeResponse(200, {"object": {"sha": "base-sha"}})
            if self.existing_branch and branch == self.existing_branch:
                return FakeResponse(200, {"object": {"sha": "branch-sha"}})
            return FakeResponse(404, {})
        if "/git/commits/" in url:
            return FakeResponse(200, {"tree": {"sha": "base-tree-sha"}})
        if url == f"https://api.github.com/repos/{REPO}":
            return FakeResponse(200, {"default_branch": "main"})
        raise AssertionError(f"GET inattendu: {url}")

    async def post(self, url, headers=None, json=None):
        self.calls.append(("POST", url, {"headers": headers, "json": json}))
        if self.blow_up:
            raise RuntimeError("reseau mort")
        if url.endswith("/issues"):
            return FakeResponse(201, {"number": 99})
        if url.endswith("/git/blobs"):
            self._blob_counter += 1
            return FakeResponse(201, {"sha": f"blob-sha-{self._blob_counter}"})
        if url.endswith("/git/trees"):
            return FakeResponse(201, {"sha": "tree-sha"})
        if url.endswith("/git/commits"):
            return FakeResponse(201, {"sha": "commit-sha"})
        if url.endswith("/git/refs"):
            return FakeResponse(201, {})
        if url.endswith("/pulls"):
            return FakeResponse(201, {"number": 7})
        raise AssertionError(f"POST inattendu: {url}")


@pytest.mark.asyncio
async def test_failed_run_with_token_creates_issue_and_waiting_pr(caplog):
    run = _run(status="failed")
    client = FakeGithubClient()

    with caplog.at_level(logging.INFO):
        await surface_failure(run, SAMPLES, settings=_settings(), client=client)

    post_urls = [c[1] for c in client.calls if c[0] == "POST"]
    assert post_urls.count(f"https://api.github.com/repos/{REPO}/issues") == 1
    assert post_urls.count(f"https://api.github.com/repos/{REPO}/git/blobs") == 2
    assert post_urls.count(f"https://api.github.com/repos/{REPO}/git/trees") == 1
    assert post_urls.count(f"https://api.github.com/repos/{REPO}/git/commits") == 1
    assert post_urls.count(f"https://api.github.com/repos/{REPO}/git/refs") == 1
    assert post_urls.count(f"https://api.github.com/repos/{REPO}/pulls") == 1

    issue_call = next(c for c in client.calls if c[0] == "POST" and c[1].endswith("/issues"))
    issue_json = issue_call[2]["json"]
    assert issue_json["title"] == f"[squad-sync] echec (2/5 clubs) — {RUN_DATE}"
    assert "structure_error" in issue_json["body"]
    assert "empty" in issue_json["body"]
    assert "club 100 KO" in issue_json["body"]  # extrait html tronque

    pull_call = next(c for c in client.calls if c[0] == "POST" and c[1].endswith("/pulls"))
    pull_json = pull_call[2]["json"]
    assert pull_json["head"] == BRANCH
    assert pull_json["base"] == "main"

    tree_call = next(c for c in client.calls if c[0] == "POST" and c[1].endswith("/git/trees"))
    paths = {entry["path"] for entry in tree_call[2]["json"]["tree"]}
    assert paths == {
        f"backend/tests/fixtures/tm_failure_{RUN_DATE}.html",
        f"backend/tests/test_parse_regression_{RUN_DATE}.py",
    }

    ref_call = next(c for c in client.calls if c[0] == "POST" and c[1].endswith("/git/refs"))
    assert ref_call[2]["json"]["ref"] == f"refs/heads/{BRANCH}"

    # Le garde-fou trace toujours un logger.error de resume, meme si le
    # canal GitHub a fonctionne.
    assert any(r.levelname == "ERROR" for r in caplog.records)


@pytest.mark.asyncio
async def test_partial_run_opens_issue_but_never_a_pr():
    """`partial` n'est pas un echec total : issue GitHub oui, PR non (I2)."""
    run = _run(status="partial")
    client = FakeGithubClient()

    await surface_failure(run, SAMPLES, settings=_settings(), client=client)

    post_urls = [c[1] for c in client.calls if c[0] == "POST"]
    assert post_urls.count(f"https://api.github.com/repos/{REPO}/issues") == 1
    assert not any(u.endswith("/pulls") for u in post_urls)
    assert not any(u.endswith("/git/blobs") for u in post_urls)
    assert not any(u.endswith("/git/refs") for u in post_urls)


@pytest.mark.asyncio
async def test_idempotent_issue_skipped_when_already_open():
    """Dedup sur "une seule issue [squad-sync] ouverte a la fois" — pas de
    filtre par date dans la recherche GitHub (I2) : un `partial` du jour
    avec une issue deja ouverte (peu importe sa date) ne recree rien."""
    run = _run(status="partial")
    client = FakeGithubClient(existing_issue=True)

    await surface_failure(run, SAMPLES, settings=_settings(), client=client)

    post_urls = [c[1] for c in client.calls if c[0] == "POST"]
    assert not any(u.endswith("/issues") for u in post_urls)
    # `partial` -> jamais de PR, meme quand l'issue est deja ouverte.
    assert not any(u.endswith("/pulls") for u in post_urls)

    search_call = next(c for c in client.calls if c[0] == "GET" and c[1].endswith("/search/issues"))
    query = search_call[2]["params"]["q"]
    assert RUN_DATE not in query
    assert "[squad-sync]" in query


@pytest.mark.asyncio
async def test_idempotent_pr_skipped_when_branch_already_exists():
    run = _run(status="failed")
    client = FakeGithubClient(existing_branch=BRANCH)

    await surface_failure(run, SAMPLES, settings=_settings(), client=client)

    post_urls = [c[1] for c in client.calls if c[0] == "POST"]
    assert any(u.endswith("/issues") for u in post_urls)
    assert not any(u.endswith("/git/blobs") for u in post_urls)
    assert not any(u.endswith("/git/trees") for u in post_urls)
    assert not any(u.endswith("/git/commits") for u in post_urls)
    assert not any(u.endswith("/git/refs") for u in post_urls)
    assert not any(u.endswith("/pulls") for u in post_urls)


@pytest.mark.asyncio
async def test_no_token_logs_critical_makes_no_http_calls_and_does_not_raise(caplog):
    run = _run(status="failed")
    client = FakeGithubClient()

    with caplog.at_level(logging.CRITICAL):
        await surface_failure(run, SAMPLES, settings=_settings(token=""), client=client)

    assert client.calls == []
    critical_records = [r for r in caplog.records if r.levelname == "CRITICAL"]
    assert len(critical_records) == 1
    assert "github_token" in critical_records[0].getMessage()


@pytest.mark.asyncio
async def test_github_api_failure_logs_critical_and_does_not_raise(caplog):
    run = _run(status="failed")
    client = FakeGithubClient(blow_up=True)

    with caplog.at_level(logging.CRITICAL):
        # Ne doit jamais lever, meme si toutes les requetes GitHub echouent.
        await surface_failure(run, SAMPLES, settings=_settings(), client=client)

    critical_records = [r for r in caplog.records if r.levelname == "CRITICAL"]
    assert len(critical_records) >= 1


@pytest.mark.asyncio
async def test_ok_run_is_a_noop(caplog):
    run = _run(status="ok", clubs_failed=0)
    client = FakeGithubClient()

    with caplog.at_level(logging.DEBUG):
        await surface_failure(run, {}, settings=_settings(), client=client)

    assert client.calls == []
    assert caplog.records == []
