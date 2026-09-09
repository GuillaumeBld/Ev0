"""BzzLineupSlot — la compo d'un match Bzzoiro, resolue joueur par joueur.

bzz_events.lineups archive la reponse brute de l'API. Elle n'est pas
directement exploitable : sur 1 468 matchs -- du 15/08/2025 au 13/04/2026,
soit les deux premiers tiers de la saison en cours -- Bzzoiro renvoie
`"id": null` pour chaque joueur et ne laisse qu'un nom court. Impossible d'y
relier une statistique.

Cette table porte le resultat de la resolution : un joueur par place, avec
son identifiant quand on a su le retrouver. On ne reecrit jamais l'archive
brute, et on ne jette jamais une place qu'on n'a pas su resoudre --
player_api_id reste NULL et le nom d'origine demeure lisible. Un trou doit se
voir, pas disparaitre.

`resolution` dit par quel chemin le lien a ete etabli, pour qu'une mesure
puisse toujours se restreindre au plus sur.
"""
from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class BzzLineupSlot(Base, TimestampMixin):
    """Une place de compo : un joueur, un camp, titulaire ou remplacant."""

    __tablename__ = "bzz_lineup_slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_api_id: Mapped[int] = mapped_column(Integer, index=True)
    is_home: Mapped[bool] = mapped_column(Boolean)
    is_starter: Mapped[bool] = mapped_column(Boolean)

    # Nom tel que Bzzoiro le donne : complet quand l'identifiant est fourni,
    # abrege ("A. Mac Allister") quand il ne l'est pas. Conserve dans les deux
    # cas : c'est la seule trace lisible d'une place non resolue.
    player_name: Mapped[str] = mapped_column(String(200))
    # NULL quand la resolution a echoue. Volontaire : voir le module.
    player_api_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)

    # "id"          -- Bzzoiro donnait l'identifiant, aucun rapprochement
    # "short_name"  -- retrouve par nom court parmi les joueurs de ce match
    # "name"        -- retrouve par nom complet parmi les joueurs de ce match
    # "introuvable" -- aucune correspondance
    # "ambigu"      -- plusieurs correspondances, aucune retenue
    resolution: Mapped[str] = mapped_column(String(12))

    position: Mapped[str | None] = mapped_column(String(4), nullable=True)
    jersey_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        # Une place par joueur nomme et par camp. Le nom fait partie de la cle
        # car player_api_id est nullable : deux places non resolues du meme
        # match doivent pouvoir coexister.
        UniqueConstraint(
            "event_api_id", "is_home", "player_name",
            name="uq_bzz_lineup_slot",
        ),
    )
