"""Suivi des tentatives d'import de carriere (Transfermarkt).

Sans cette trace, un joueur que Transfermarkt ne sait pas identifier serait
retente a chaque passage du job, indefiniment, sur un site qu'on scrape a
2 secondes par requete. Une ligne par joueur, mise a jour a chaque tentative.

Elle tient aussi lieu de cache persistant : le worker n'a aucun volume, donc
le fichier de cache de `transfermarkt_career.py` ne survit pas a une
reconstruction d'image.
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Issues possibles d'une tentative.
OUTCOME_OK = "ok"              # carriere importee
OUTCOME_UNMATCHED = "unmatched"  # Transfermarkt n'a pas su identifier le joueur
OUTCOME_ERROR = "error"        # panne reseau / HTTP — reessayable plus tot


class CareerImportAttempt(Base):
    """Derniere tentative d'import de carriere pour un joueur."""

    __tablename__ = "career_import_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # bzz_players.api_id — pas de cle etrangere : bzz_players est resynchronise
    # par la synchro des effectifs, et une carriere ne doit pas bloquer cela.
    player_api_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)

    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    outcome: Mapped[str] = mapped_column(String(10), nullable=False)

    # Raison rendue par le resolveur Transfermarkt quand outcome != ok.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # tm_id resolu, conserve meme en cas d'echec ulterieur : evite de refaire
    # la recherche par nom, l'etape la plus couteuse.
    tm_id: Mapped[str | None] = mapped_column(String(20), nullable=True)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
