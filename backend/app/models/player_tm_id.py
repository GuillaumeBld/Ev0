"""Liaison joueur -> identifiant Transfermarkt, tracable par source.

Fondation du contournement du rate-limit Transfermarkt : les jalons suivants
(effectifs, Wikidata) remplissent cette table, et l'import carriere la lit
pour sauter la recherche quand un tm_id est deja connu pour le joueur.
"""

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PlayerTmId(Base, TimestampMixin):
    """Un joueur = une ligne (PK = player_api_id)."""

    __tablename__ = "player_tm_ids"

    player_api_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bzz_players.api_id", ondelete="CASCADE"),
        primary_key=True,
    )
    tm_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)

    def __repr__(self) -> str:
        return f"<PlayerTmId player_api_id={self.player_api_id} tm_id={self.tm_id} source={self.source}>"
