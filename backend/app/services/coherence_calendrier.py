"""Controle de coherence du calendrier : ce qui est mathematiquement impossible.

Pourquoi ce module existe
-------------------------
Les garde-fous du projet posaient tous la meme question — est-ce que les
donnees continuent d'arriver ? Le rapport de sante quotidien verifie la
fraicheur des cotes et le nombre de recommandations ; le canal `incidents`
se declenche sur une exception ; `failure_surface` sur une synchro d'effectifs
en echec. Aucun ne demandait si ce qui arrive tient debout.

Le 07/09/2026, Bzzoiro a publie le TIRAGE de la phase de ligue de C1 — les 8
adversaires de chaque club — et la synchro l'a ingere comme un calendrier :
36 clubs x 8 adversaires / 2 = 144 matchs, tous estampilles "journee 1" et
entasses entre le 8 et le 10 septembre, avec Real Madrid jouant huit matchs au
meme coup d'envoi. Aucune exception levee, donc aucune alerte. Pire : ces
fantomes ont produit 3 582 cotes et 523 recommandations supplementaires, donc
tous les indicateurs de vitalite sont passes AU VERT. Un tuyau qui charrie du
faux sans broncher passe mieux ces tests qu'un tuyau qui s'arrete.

Les deux regles
---------------
Elles sont choisies pour etre des VERITES ARITHMETIQUES, jamais des
heuristiques : un match consomme exactement deux equipes distinctes, et une
equipe ne peut etre qu'a un endroit a la fois. Une violation est donc une
certitude, pas un soupcon — ce module ne peut pas produire de faux positif, et
peut donc bloquer sans risque plutot que se contenter d'alerter.

  1. `equipe_dedoublee` — une equipe apparait dans deux matchs au meme coup
     d'envoi. Impossible.
  2. `journee_surchargee` — une competition compte, sur une meme journee
     calendaire, plus de matchs que la moitie des equipes qui y jouent.
     Impossible : n matchs exigent 2n equipes distinctes.

Ce que le module ne fait pas
----------------------------
Il ne juge ni la plausibilite d'une date, ni la coherence d'un calendrier avec
le reglement d'une competition. Une regle qui demande du metier ("la C1 ne
joue pas pendant une treve internationale") produirait des faux positifs et
finirait ignoree. On ne retient ici que l'impossible.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MatchAControler:
    """Le strict necessaire pour appliquer les regles.

    Volontairement decouple de l'ORM : les regles sont des fonctions pures,
    testables sans base, et applicables aussi bien a `bzz_events` (la source)
    qu'a `fixtures` (ce que le site sert).
    """

    identifiant: int
    competition: str | int
    equipe_domicile: int | str | None
    equipe_exterieur: int | str | None
    coup_denvoi: datetime
    # Date de derniere publication par la source. Sert a departager un conflit :
    # entre deux matchs qui s'excluent, le plus recemment publie est le dernier
    # mot de la source. `None` = inconnu, on ne departage pas.
    mis_a_jour_le: datetime | None = None


@dataclass(frozen=True)
class Violation:
    """Une incoherence constatee, rattachee aux matchs qui la portent."""

    regle: str
    motif: str
    identifiants: frozenset[int]


def _equipes(match: MatchAControler) -> list[int | str]:
    return [e for e in (match.equipe_domicile, match.equipe_exterieur) if e is not None]


def equipe_dedoublee(matchs: list[MatchAControler]) -> list[Violation]:
    """Une equipe ne peut pas disputer deux matchs au meme coup d'envoi."""
    par_equipe_et_creneau: dict[tuple[int | str, datetime], list[int]] = defaultdict(list)
    for match in matchs:
        for equipe in _equipes(match):
            par_equipe_et_creneau[(equipe, match.coup_denvoi)].append(match.identifiant)

    violations: list[Violation] = []
    for (equipe, creneau), identifiants in sorted(
        par_equipe_et_creneau.items(), key=lambda item: (str(item[0][0]), item[0][1])
    ):
        if len(identifiants) > 1:
            violations.append(
                Violation(
                    regle="equipe_dedoublee",
                    motif=(
                        f"l'equipe {equipe} dispute {len(identifiants)} matchs "
                        f"au coup d'envoi du {creneau:%d/%m/%Y %H:%M}"
                    ),
                    identifiants=frozenset(identifiants),
                )
            )
    return violations


def journee_surchargee(matchs: list[MatchAControler]) -> list[Violation]:
    """Une competition ne peut pas aligner, sur une journee, plus de matchs que
    la moitie des equipes qui y jouent : n matchs exigent 2n equipes
    distinctes."""
    par_competition_et_jour: dict[tuple[str | int, date], list[MatchAControler]] = defaultdict(list)
    for match in matchs:
        par_competition_et_jour[(match.competition, match.coup_denvoi.date())].append(match)

    violations: list[Violation] = []
    for (competition, jour), lot in sorted(
        par_competition_et_jour.items(), key=lambda item: (str(item[0][0]), item[0][1])
    ):
        equipes: set[int | str] = set()
        for match in lot:
            equipes.update(_equipes(match))
        maximum = len(equipes) // 2
        if len(lot) > maximum:
            violations.append(
                Violation(
                    regle="journee_surchargee",
                    motif=(
                        f"{competition} compte {len(lot)} matchs le "
                        f"{jour:%d/%m/%Y} pour seulement {len(equipes)} equipes "
                        f"(maximum possible : {maximum})"
                    ),
                    identifiants=frozenset(m.identifiant for m in lot),
                )
            )
    return violations


REGLES = (equipe_dedoublee, journee_surchargee)

# Seules ces regles servent a ECARTER un match. Distinction volontaire :
# `equipe_dedoublee` designe precisement les matchs fautifs, tandis que
# `journee_surchargee` constate qu'une journee est impossible sans pouvoir
# dire lesquels de ses matchs sont en trop — elle englobe donc aussi des
# rencontres parfaitement valides. Filtrer sur elle supprimerait du vrai
# calendrier ; on la garde pour alerter, jamais pour bloquer.
REGLES_BLOQUANTES = (equipe_dedoublee,)


def controler(matchs: list[MatchAControler]) -> list[Violation]:
    """Applique toutes les regles. Liste vide = calendrier coherent.

    Destine a l'ALERTE : on veut y voir tout ce qui cloche, meme quand la
    regle ne sait pas isoler le coupable.
    """
    violations: list[Violation] = []
    for regle in REGLES:
        violations.extend(regle(matchs))
    return violations


def identifiants_incoherents(matchs: list[MatchAControler]) -> set[int]:
    """Matchs a ecarter avant qu'ils n'atteignent le calendrier.

    N'applique que `REGLES_BLOQUANTES` : on ne supprime que ce qui est fautif
    de facon certaine et nominative. Un doute alerte, il ne supprime pas.

    Departage par fraicheur. Un groupe en conflit contient presque toujours UN
    vrai match et des perimes : le 07/09/2026, Real Madrid apparaissait dans
    huit rencontres a 19h00, dont une seule — contre l'Inter — etait au vrai
    calendrier ; les sept autres dataient du 29 aout. Ecarter tout le groupe
    supprimerait donc aussi le vrai match (constate lors de la purge, qui a
    emporte 4 rencontres reelles avec les fantomes). On garde le plus
    recemment publie par la source et on ecarte le reste.

    Si la fraicheur est inconnue pour tout le groupe, on ne devine pas : le
    groupe entier est ecarte, le vrai match revenant a la synchro suivante.
    """
    par_conflit: dict[int, MatchAControler] = {m.identifiant: m for m in matchs}
    incoherents: set[int] = set()

    for regle in REGLES_BLOQUANTES:
        for violation in regle(matchs):
            groupe = [par_conflit[i] for i in violation.identifiants if i in par_conflit]
            datés = [m for m in groupe if m.mis_a_jour_le is not None]
            if not datés:
                incoherents |= violation.identifiants
                continue
            survivant = max(datés, key=lambda m: m.mis_a_jour_le)
            incoherents |= {
                m.identifiant for m in groupe if m.identifiant != survivant.identifiant
            }

    return incoherents


def resumer(violations: list[Violation], maximum: int = 5) -> str:
    """Message court pour le canal `incidents`. Les motifs sont deja lisibles ;
    on en montre quelques-uns et on compte le reste."""
    if not violations:
        return "calendrier coherent"

    matchs_touches = set()
    for violation in violations:
        matchs_touches |= violation.identifiants

    lignes = [
        f"Calendrier incoherent : {len(violations)} violation(s) sur "
        f"{len(matchs_touches)} match(s).",
    ]
    lignes.extend(f"- {v.motif}" for v in violations[:maximum])
    if len(violations) > maximum:
        lignes.append(f"- ... et {len(violations) - maximum} autre(s)")
    return "\n".join(lignes)
