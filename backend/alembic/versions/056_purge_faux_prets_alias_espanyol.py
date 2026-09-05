"""Purge les etiquettes de pret devinees, et ancre Espanyol sur Transfermarkt.

Deux reparations de donnees, consequences du meme correctif : Transfermarkt
redevient la seule autorite sur la composition d'un effectif.

1. `bzz_players.loan_team_*` etait rempli par `sync_loan_teams` (supprime),
   qui deduisait le club d'un joueur en comptant les equipes vues sur ses
   feuilles de match. Apres UN SEUL match, le club du joueur et son adversaire
   sont a egalite (1 partout) et le depart etait tire au hasard : le 05/09/2026,
   Porro, Kudus et van de Ven (Tottenham) etaient etiquetes "pretes a Newcastle"
   sur la foi du seul Tottenham-Newcastle du 29 aout. La page Joueurs affichant
   un joueur sur son club actuel OU son club de pret, les deux effectifs se
   melangeaient. 1 035 etiquettes posees, 348 decidees sur un unique match,
   dans les cinq championnats.

   Ces colonnes n'ont plus aucun ecrivain : la page effectif Transfermarkt
   liste deja un joueur prete dans son club d'accueil, donc
   `current_team_api_id` suffit. On les vide.

2. "RCD Espanyol Barcelona" (Transfermarkt) ne s'appariait a aucun club
   canonique de facon univoque : ses tokens contiennent a la fois "Espanyol"
   et "Barcelona", donc deux candidats -> le matcher refusait de deviner (et
   il a raison). L'alias exact leve l'ambiguite au premier niveau de matching.

Revision ID: 056
Revises: 055
Create Date: 2026-09-05
"""
from collections.abc import Sequence

from alembic import op

revision: str = "056"
down_revision: str | None = "055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALIAS_ESPANYOL = "RCD Espanyol Barcelona"


def upgrade() -> None:
    op.execute(
        """
        UPDATE bzz_players
           SET loan_team_api_id = NULL,
               loan_team_name   = NULL
         WHERE loan_team_api_id IS NOT NULL
            OR loan_team_name IS NOT NULL
        """
    )
    op.execute(
        f"""
        UPDATE canonical_teams
           SET aliases = array_append(aliases, '{_ALIAS_ESPANYOL}')
         WHERE name_fr = 'Espanyol'
           AND NOT ('{_ALIAS_ESPANYOL}' = ANY(aliases))
        """
    )


def downgrade() -> None:
    # Les etiquettes de pret purgees ne sont pas restaurables : elles etaient
    # devinees, pas mesurees. Seul l'alias est reversible.
    op.execute(
        f"""
        UPDATE canonical_teams
           SET aliases = array_remove(aliases, '{_ALIAS_ESPANYOL}')
         WHERE name_fr = 'Espanyol'
        """
    )
