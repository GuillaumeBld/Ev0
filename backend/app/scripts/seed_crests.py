"""Seed idempotent des ecussons de clubs/selections depuis le fichier
versionne `app/data/crests.json` ({bzz_team_id: crest_url}).

Les 854 ecussons ont ete resolus en production (footylogos.com, chaque URL
verifiee 200 avant d'etre posee) puis exportes dans ce fichier pour etre
ANCRES DANS LE CODE : une restauration de base ou une reconstruction du
referentiel (`rebuild_team_registry.rebuild()` / `attach_european()`) ne
doit jamais pouvoir les perdre.

La mise a jour se fait STRICTEMENT par `bzz_team_id` -- jamais par nom. Deux
clubs peuvent porter des libelles quasi identiques ("AC Milan" et un
doublon historique au meme nom) sans designer le meme club : seul
`bzz_team_id` (l'identifiant qui fait foi, /api/events/ home_team_obj.id)
garantit qu'un ecusson n'est jamais attribue au mauvais club. Une ligne dont
le `bzz_team_id` n'apparait pas dans le fichier n'est jamais touchee.

Idempotent : relancer le seed plusieurs fois produit le meme etat, sans
effet de bord ni erreur.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# Chemin par defaut, relatif au package -- robuste quel que soit le CWD
# d'execution (python -m app.scripts.seed_crests depuis n'importe ou,
# appel programmatique depuis un worker, etc).
DEFAULT_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "crests.json"


def _load_crests(data_path: Path) -> dict[int, str]:
    """Charge le fichier {bzz_team_id (str) -> crest_url} et convertit les
    cles en entiers, pour etre comparees directement a
    `CanonicalTeam.bzz_team_id`."""
    raw: dict[str, str] = json.loads(data_path.read_text(encoding="utf-8"))
    return {int(bzz_id): url for bzz_id, url in raw.items()}


async def seed_crests(
    session: AsyncSession, *, data_path: Path | None = None
) -> dict[str, object]:
    """Applique les ecussons de `crests.json` en base, par `bzz_team_id`.

    Pour chaque `bzz_team_id` du fichier : met a jour `crest_url` de la
    ligne canonique dont le `bzz_team_id` correspond exactement -- jamais
    d'appariement par nom. Un `bzz_team_id` du fichier absent en base est
    compte dans `bzz_ids_absents_en_base` ; aucune ligne n'est creee pour
    lui, et aucune autre ligne n'est touchee.

    Idempotent : rejouable sans effet de bord (une ligne deja a jour est
    simplement reecrite avec la meme valeur).

    Rend {"appliques": int, "bzz_ids_absents_en_base": list[int]}.
    """
    from app.models.canonical_teams import CanonicalTeam

    path = data_path if data_path is not None else DEFAULT_DATA_PATH
    crests = _load_crests(path)

    appliques = 0
    absents: list[int] = []

    for bzz_id, crest_url in crests.items():
        ligne = (
            await session.execute(
                select(CanonicalTeam).where(CanonicalTeam.bzz_team_id == bzz_id)
            )
        ).scalar_one_or_none()

        if ligne is None:
            absents.append(bzz_id)
            continue

        ligne.crest_url = crest_url
        appliques += 1

    await session.commit()

    rapport = {"appliques": appliques, "bzz_ids_absents_en_base": absents}
    logger.info(
        "Seed ecussons : %d appliques, %d bzz_team_id absents en base",
        appliques, len(absents),
    )
    return rapport


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Reapplique les ecussons ancres dans app/data/crests.json sur "
            "canonical_teams.crest_url, par bzz_team_id."
        )
    )
    parser.add_argument(
        "--data-path",
        dest="data_path",
        type=Path,
        default=None,
        help="Chemin alternatif vers un fichier crests.json (defaut : app/data/crests.json).",
    )
    return parser.parse_args(argv)


async def _main(argv: list[str] | None = None) -> None:
    from app.db import async_session

    args = _parse_args(argv)
    async with async_session() as session:
        await seed_crests(session, data_path=args.data_path)


if __name__ == "__main__":
    asyncio.run(_main())
