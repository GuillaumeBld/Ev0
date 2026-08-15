"""Garde-fou anti-mort-silencieuse pour la reconciliation d'effectifs
Transfermarkt (`sync_squads`).

Sur un run `SquadSyncRun` en echec (`status in {"failed", "partial"}`),
`surface_failure` s'assure que RIEN ne passe en silence :

  a. `logger.error` systematique avec le resume du run (clubs KO/total,
     statut, echantillon de clubs KO) — toujours emis, meme sans canal
     GitHub disponible.
  b. Si `settings.github_token` est renseigne, ouvre (de facon idempotente,
     un seul run par jour calendaire) une issue GitHub decrivant l'echec,
     puis tente en best-effort une PR "correctif d'attente" : un fixture
     HTML capture (un des clubs en echec) + un test de non-regression qui
     `assert`e que `parse_squad` retombe sur ses pattes sur ce fixture
     (donc ROUGE tant que le parseur n'est pas corrige, VERT une fois le
     correctif applique).
  c. Si `settings.github_token` est vide, `logger.critical` explicite :
     l'echec squad-sync n'est track nulle part ailleurs.
  d. Toute exception reseau/API GitHub est capturee (`logger.critical`,
     `exc_info=True`) : ce garde-fou ne doit JAMAIS faire planter le worker
     qui l'appelle, ni propager d'exception.

Un run `status == "ok"` est un no-op.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import settings as _default_settings
from app.models.squad_sync import SquadSyncRun

logger = logging.getLogger(__name__)

API_BASE = "https://api.github.com"

# Nombre de clubs KO affiches dans le resume `logger.error` (evite de gonfler
# les logs si beaucoup de clubs echouent en meme temps).
LOG_SAMPLE_SIZE = 5

# Longueur max de l'extrait HTML colle dans le corps de l'issue GitHub.
ISSUE_HTML_EXCERPT_MAX = 2000

_FAILING_STATUSES = {"failed", "partial"}


def _failed_clubs(run: SquadSyncRun) -> list[dict[str, Any]]:
    detail = run.detail or {}
    failed = detail.get("failed_clubs") or []
    return list(failed)


def _run_date(run: SquadSyncRun) -> str:
    """Date calendaire (ISO, `YYYY-MM-DD`) du run, utilisee comme cle
    d'idempotence pour l'issue et la branche de correctif d'attente (un
    seul couple issue/PR par jour, jamais de spam sur des runs repetes le
    meme jour)."""
    started_at = run.started_at or datetime.now(UTC)
    return started_at.date().isoformat()


def _log_error_summary(run: SquadSyncRun) -> None:
    failed_clubs = _failed_clubs(run)
    sample = [
        {"transfermarkt_club_id": c.get("transfermarkt_club_id"), "status": c.get("status")}
        for c in failed_clubs[:LOG_SAMPLE_SIZE]
    ]
    logger.error(
        "squad-sync run id=%s status=%s : %s/%s clubs KO. Echantillon clubs KO: %s",
        run.id, run.status, run.clubs_failed, run.clubs_total, sample,
    )


def _issue_title(run: SquadSyncRun, run_date: str) -> str:
    return f"[squad-sync] echec ({run.clubs_failed}/{run.clubs_total} clubs) — {run_date}"


def _issue_body(run: SquadSyncRun, samples: dict[int, str], run_date: str) -> str:
    failed_clubs = _failed_clubs(run)
    lines = [
        f"## Echec squad-sync — {run_date}",
        "",
        f"**Statut du run** : `{run.status}` (id={run.id})",
        f"**Clubs KO** : {run.clubs_failed}/{run.clubs_total}",
        "",
        "### Clubs en echec",
    ]
    if failed_clubs:
        for club in failed_clubs:
            lines.append(
                f"- club Transfermarkt `{club.get('transfermarkt_club_id')}` "
                f"(canonical_team_id={club.get('canonical_team_id')}) — "
                f"type d'erreur : `{club.get('status')}`"
            )
    else:
        lines.append("- (detail non disponible sur ce run)")

    if samples:
        sample_club_id, sample_html = next(iter(samples.items()))
        excerpt = sample_html[:ISSUE_HTML_EXCERPT_MAX]
        lines += [
            "",
            f"### Extrait HTML capture (club `{sample_club_id}`, tronque a "
            f"{ISSUE_HTML_EXCERPT_MAX} caracteres)",
            "```html",
            excerpt,
            "```",
        ]

    lines += [
        "",
        "---",
        "_Issue creee automatiquement par le garde-fou squad-sync "
        "(`app.ingestion.transfermarkt.failure_surface`)._",
    ]
    return "\n".join(lines)


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }


async def _find_existing_issue_number(
    client: httpx.AsyncClient, headers: dict[str, str], repo: str, run_date: str
) -> int | None:
    query = f'repo:{repo} is:issue is:open in:title "[squad-sync]" "{run_date}"'
    resp = await client.get(f"{API_BASE}/search/issues", headers=headers, params={"q": query})
    resp.raise_for_status()
    payload = resp.json()
    items = payload.get("items") or []
    if not items:
        return None
    return items[0]["number"]


async def _create_issue(
    client: httpx.AsyncClient, headers: dict[str, str], repo: str, title: str, body: str
) -> None:
    resp = await client.post(
        f"{API_BASE}/repos/{repo}/issues", headers=headers, json={"title": title, "body": body}
    )
    resp.raise_for_status()


async def _ensure_issue(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    repo: str,
    run: SquadSyncRun,
    samples: dict[int, str],
    run_date: str,
) -> None:
    existing = await _find_existing_issue_number(client, headers, repo, run_date)
    if existing is not None:
        logger.info(
            "squad-sync: issue GitHub deja ouverte pour le %s (#%s), pas de doublon.",
            run_date, existing,
        )
        return
    await _create_issue(
        client, headers, repo, _issue_title(run, run_date), _issue_body(run, samples, run_date)
    )
    logger.info("squad-sync: issue GitHub creee pour l'echec du %s.", run_date)


async def _get_default_branch(client: httpx.AsyncClient, headers: dict[str, str], repo: str) -> str:
    resp = await client.get(f"{API_BASE}/repos/{repo}", headers=headers)
    resp.raise_for_status()
    return resp.json()["default_branch"]


async def _ref_sha(
    client: httpx.AsyncClient, headers: dict[str, str], repo: str, ref: str
) -> str | None:
    """SHA du ref `heads/<ref>`, ou `None` s'il n'existe pas (404 attendu,
    pas une erreur). Toute autre reponse en echec leve (capturee en amont)."""
    resp = await client.get(f"{API_BASE}/repos/{repo}/git/ref/heads/{ref}", headers=headers)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()["object"]["sha"]


async def _base_tree_sha(
    client: httpx.AsyncClient, headers: dict[str, str], repo: str, commit_sha: str
) -> str:
    resp = await client.get(f"{API_BASE}/repos/{repo}/git/commits/{commit_sha}", headers=headers)
    resp.raise_for_status()
    return resp.json()["tree"]["sha"]


async def _create_blob(
    client: httpx.AsyncClient, headers: dict[str, str], repo: str, content: str
) -> str:
    resp = await client.post(
        f"{API_BASE}/repos/{repo}/git/blobs",
        headers=headers,
        json={"content": content, "encoding": "utf-8"},
    )
    resp.raise_for_status()
    return resp.json()["sha"]


async def _create_tree(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    repo: str,
    base_tree_sha: str,
    entries: list[dict[str, str]],
) -> str:
    resp = await client.post(
        f"{API_BASE}/repos/{repo}/git/trees",
        headers=headers,
        json={"base_tree": base_tree_sha, "tree": entries},
    )
    resp.raise_for_status()
    return resp.json()["sha"]


async def _create_commit(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    repo: str,
    message: str,
    tree_sha: str,
    parent_sha: str,
) -> str:
    resp = await client.post(
        f"{API_BASE}/repos/{repo}/git/commits",
        headers=headers,
        json={"message": message, "tree": tree_sha, "parents": [parent_sha]},
    )
    resp.raise_for_status()
    return resp.json()["sha"]


async def _create_branch_ref(
    client: httpx.AsyncClient, headers: dict[str, str], repo: str, branch: str, sha: str
) -> None:
    resp = await client.post(
        f"{API_BASE}/repos/{repo}/git/refs",
        headers=headers,
        json={"ref": f"refs/heads/{branch}", "sha": sha},
    )
    resp.raise_for_status()


async def _create_pull_request(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    repo: str,
    title: str,
    head: str,
    base: str,
    body: str,
) -> None:
    resp = await client.post(
        f"{API_BASE}/repos/{repo}/pulls",
        headers=headers,
        json={"title": title, "head": head, "base": base, "body": body},
    )
    resp.raise_for_status()


def _fixture_path(run_date: str) -> str:
    return f"backend/tests/fixtures/tm_failure_{run_date}.html"


def _regression_test_path(run_date: str) -> str:
    return f"backend/tests/test_parse_regression_{run_date}.py"


def _regression_test_source(run_date: str, club_id: int) -> str:
    return (
        f'"""Test de non-regression genere automatiquement par le garde-fou '
        f"squad-sync (echec du {run_date}).\n\n"
        "ROUGE tant que `parse_squad` ne parse pas correctement le fixture "
        "capture ci-contre ; VERT une fois le parseur corrige pour ce cas.\n"
        '"""\n'
        "from pathlib import Path\n\n"
        "from app.ingestion.transfermarkt.squad_scraper import MIN_SQUAD, parse_squad\n\n"
        f'FIXTURE = Path(__file__).parent / "fixtures" / "tm_failure_{run_date}.html"\n\n\n'
        "def test_parse_squad_regression():\n"
        "    html_text = FIXTURE.read_text()\n"
        f"    result = parse_squad(html_text, club_id={club_id})\n"
        '    assert result.status == "ok"\n'
        "    assert len(result.players) >= MIN_SQUAD\n"
    )


def _pr_body(run: SquadSyncRun, run_date: str) -> str:
    return (
        f"## Correctif d'attente automatique — squad-sync {run_date}\n\n"
        f"Genere par le garde-fou anti-mort-silencieuse suite a un run "
        f"`{run.status}` ({run.clubs_failed}/{run.clubs_total} clubs KO).\n\n"
        f"- Ajoute `{_fixture_path(run_date)}` : extrait HTML capture d'un club "
        "en echec.\n"
        f"- Ajoute `{_regression_test_path(run_date)}` : `assert`e que "
        "`parse_squad` traite ce fixture avec `status == \"ok\"` et "
        "`len(players) >= MIN_SQUAD`.\n\n"
        "Ce test est **ROUGE** tant que le parseur n'est pas corrige pour ce "
        "cas — il sert de garde-fou de non-regression une fois le correctif "
        "applique. Voir l'issue `[squad-sync]` correspondante pour le detail "
        "de l'echec."
    )


async def _ensure_pull_request(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    repo: str,
    run: SquadSyncRun,
    samples: dict[int, str],
    run_date: str,
) -> None:
    if not samples:
        logger.warning(
            "squad-sync: aucun echantillon HTML capture pour l'echec du %s — "
            "PR correctif d'attente ignoree (rien a fixturer).",
            run_date,
        )
        return

    branch = f"squad-sync/parse-failure-{run_date}"

    if (await _ref_sha(client, headers, repo, branch)) is not None:
        logger.info(
            "squad-sync: branche %s deja presente, PR correctif d'attente ignoree (idempotent).",
            branch,
        )
        return

    default_branch = await _get_default_branch(client, headers, repo)
    base_sha = await _ref_sha(client, headers, repo, default_branch)
    if base_sha is None:
        logger.critical(
            "squad-sync: impossible de resoudre le ref de la branche par defaut %s de %s.",
            default_branch, repo,
        )
        return
    base_tree_sha = await _base_tree_sha(client, headers, repo, base_sha)

    club_id, raw_html = next(iter(samples.items()))
    fixture_sha = await _create_blob(client, headers, repo, raw_html)
    test_sha = await _create_blob(
        client, headers, repo, _regression_test_source(run_date, club_id)
    )

    tree_sha = await _create_tree(
        client,
        headers,
        repo,
        base_tree_sha,
        [
            {"path": _fixture_path(run_date), "mode": "100644", "type": "blob", "sha": fixture_sha},
            {
                "path": _regression_test_path(run_date),
                "mode": "100644",
                "type": "blob",
                "sha": test_sha,
            },
        ],
    )
    commit_sha = await _create_commit(
        client,
        headers,
        repo,
        f"test(squad-sync): non-regression correctif d'attente — echec {run_date}",
        tree_sha,
        base_sha,
    )
    await _create_branch_ref(client, headers, repo, branch, commit_sha)
    await _create_pull_request(
        client,
        headers,
        repo,
        title=f"[squad-sync] correctif d'attente — echec {run_date}",
        head=branch,
        base=default_branch,
        body=_pr_body(run, run_date),
    )
    logger.info("squad-sync: PR correctif d'attente creee (branche %s).", branch)


async def _surface_via_github(
    client: httpx.AsyncClient,
    repo: str,
    token: str,
    run: SquadSyncRun,
    samples: dict[int, str],
    run_date: str,
) -> None:
    headers = _github_headers(token)

    try:
        await _ensure_issue(client, headers, repo, run, samples, run_date)
    except Exception:
        logger.critical(
            "squad-fail-guard: echec creation/verification de l'issue GitHub pour le run id=%s "
            "(echec squad-sync %s NON tracke en issue).",
            run.id, run_date,
            exc_info=True,
        )

    try:
        await _ensure_pull_request(client, headers, repo, run, samples, run_date)
    except Exception:
        logger.critical(
            "squad-fail-guard: echec creation de la PR correctif d'attente pour le run id=%s.",
            run.id,
            exc_info=True,
        )


async def surface_failure(
    run: SquadSyncRun,
    samples: dict[int, str],
    *,
    settings: Any = None,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Garde-fou anti-mort-silencieuse : sur un run `failed`/`partial`,
    trace TOUJOURS un `logger.error`, puis ouvre une issue + PR GitHub si
    `settings.github_token` est configure (`logger.critical` sinon). Ne
    leve JAMAIS d'exception — un incident sur ce garde-fou ne doit jamais
    faire planter le worker squad-sync qui l'appelle.

    `samples` : `{club_id: raw_html}` des clubs KO (structure_error/empty)
    dont la page a ete capturee, utilise pour l'extrait dans l'issue et le
    fixture de la PR de correctif d'attente.
    """
    if run.status not in _FAILING_STATUSES:
        return

    _log_error_summary(run)

    cfg = settings if settings is not None else _default_settings
    token = cfg.github_token
    repo = cfg.github_repo

    if not token:
        logger.critical(
            "garde-fou: aucun github_token configure — echec squad-sync NON tracke en "
            "issue/PR: run id=%s status=%s %s/%s clubs KO.",
            run.id, run.status, run.clubs_failed, run.clubs_total,
        )
        return

    run_date = _run_date(run)

    try:
        if client is not None:
            await _surface_via_github(client, repo, token, run, samples, run_date)
        else:
            async with httpx.AsyncClient(timeout=20) as owned_client:
                await _surface_via_github(owned_client, repo, token, run, samples, run_date)
    except Exception:
        # Ceinture + bretelles : _surface_via_github encapsule deja chacune de
        # ses deux etapes, mais toute erreur inattendue (ex: ouverture du
        # client HTTP lui-meme) ne doit jamais remonter au-dela de ce garde-fou.
        logger.critical(
            "squad-fail-guard: echec inattendu du garde-fou GitHub pour le run id=%s.",
            run.id,
            exc_info=True,
        )
