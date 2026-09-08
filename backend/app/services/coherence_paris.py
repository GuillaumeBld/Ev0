"""Controle de coherence des cotes et des recommandations.

Meme demarche que `coherence_calendrier` : les garde-fous du projet demandent
tous "est-ce que les donnees arrivent encore ?" — fraicheur des cotes, nombre
de recommandations en 24h, exceptions, synchro d'effectifs en echec. Aucun ne
demandait "est-ce que ce qui est arrive tient debout ?". Le 07/09/2026, 126
matchs de C1 perimes ont produit 3 582 cotes et 523 recommandations
SUPPLEMENTAIRES : tous les indicateurs de vitalite sont passes au vert, et
d'autant plus verts que le bug etait gros.

Ce module pose la seconde question sur les deux tables qui portent l'argent.

Le choix des regles
-------------------
Comme pour le calendrier, on ne retient que l'IMPOSSIBLE, jamais le douteux.
Deux familles :

  - des impossibilites de marche : une cote decimale inferieure ou egale a 1
    rendrait moins que la mise ; un bookmaker ne propose pas son propre marche
    a trois issues avec une marge negative, ce serait payer pour prendre le
    risque ;
  - des contradictions internes : une recommandation stocke a la fois la
    probabilite du modele, la cote juste qui en decoule, la cote du marche et
    l'avantage. Ces quatre nombres sont lies par deux formules exactes
    (`fair_odds = 1 / fair_probability` et `edge = best_odds / fair_odds - 1`).
    Quand ils se contredisent, la ligne est fausse — sans avoir a savoir quoi
    que ce soit du football.

Une regle qui demanderait du jugement ("cette cote parait haute pour ce
joueur") produirait des faux positifs et finirait ignoree. On s'en tient a ce
qui se demontre.

Ce que le module ne fait pas
----------------------------
Il ne juge pas la qualite du pricing. Un modele mal calibre produit des
recommandations parfaitement coherentes entre elles ; c'est le role du harnais
d'evaluation, pas d'un controle d'integrite.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

# Tolerance sur les comparaisons de flottants. On cherche une contradiction
# franche, jamais un arrondi.
TOLERANCE = 1e-4

# `fair_odds` est stocke arrondi a deux decimales (verifie : 8 935 lignes sur
# 9 724 en production). Comparer 1/probabilite a la valeur stockee exige donc
# d'absorber ce pas d'arrondi, sinon la regle signale 2 485 lignes parfaitement
# saines — le genre de faux positif qui fait ignorer une alerte.
TOLERANCE_COTE_JUSTE = 0.01

# L'avantage est stocke en pleine precision, mais il se DEDUIT d'une cote
# juste arrondie : l'arrondi se propage. `avantage = cote_marche / cote_juste - 1`
# a pour derivee `-cote_marche / cote_juste^2`, donc un demi-pas d'arrondi sur la
# cote juste deplace l'avantage de `cote_marche * PAS / cote_juste^2`. A cote
# juste 1,26 et cote marche 17,25 cela fait deja 0,054 — signaler un tel ecart
# comme une contradiction serait accuser l'arrondi. On compare donc chaque ligne
# a SA propre marge d'arrondi, jamais a un seuil unique.
TOLERANCE_AVANTAGE = 0.005
PAS_ARRONDI_COTE_JUSTE = 0.005


def tolerance_avantage(cote_juste: float, cote_marche: float) -> float:
    """Ecart d'avantage qu'un simple arrondi de la cote juste peut expliquer."""
    if cote_juste <= 0:
        return TOLERANCE_AVANTAGE
    return max(
        TOLERANCE_AVANTAGE,
        cote_marche * PAS_ARRONDI_COTE_JUSTE / (cote_juste ** 2),
    )

# Une cote decimale rend `mise x cote`. A 1.0 le parieur recupere exactement sa
# mise, en dessous il perd en gagnant : aucun bookmaker ne propose cela.
COTE_MINIMALE = 1.0

# Les trois issues d'un marche 1X2. La somme de leurs probabilites implicites
# est la marge du bookmaker : elle depasse toujours 100 %, c'est son metier.
ISSUES_1X2 = frozenset({"home", "draw", "away"})


@dataclass(frozen=True)
class CoteAControler:
    identifiant: int
    match_id: int
    bookmaker: str
    marche: str
    issue: str | None
    cote: float
    releve_le: datetime


@dataclass(frozen=True)
class RecommandationAControler:
    identifiant: int
    match_id: int
    joueur: str
    probabilite_modele: float
    cote_juste: float
    cote_marche: float
    avantage: float


@dataclass(frozen=True)
class Violation:
    regle: str
    motif: str
    identifiants: frozenset[int]


# ---------------------------------------------------------------------------
# Cotes
# ---------------------------------------------------------------------------


def cote_impossible(cotes: list[CoteAControler]) -> list[Violation]:
    """Une cote decimale inferieure ou egale a 1 rend au mieux la mise."""
    fautives = [c for c in cotes if c.cote <= COTE_MINIMALE]
    if not fautives:
        return []
    return [
        Violation(
            regle="cote_impossible",
            motif=(
                f"{len(fautives)} cote(s) <= {COTE_MINIMALE} "
                f"(la plus basse : {min(c.cote for c in fautives):.3f} chez "
                f"{min(fautives, key=lambda c: c.cote).bookmaker})"
            ),
            identifiants=frozenset(c.identifiant for c in fautives),
        )
    ]


def marche_a_marge_negative(cotes: list[CoteAControler]) -> list[Violation]:
    """Un bookmaker ne price pas son marche 1X2 sous les 100 %.

    On ne juge qu'un marche COMPLET — les trois issues presentes pour le meme
    bookmaker au meme releve. Un marche partiel ne prouve rien : il manque une
    issue, la somme est mecaniquement plus basse.
    """
    groupes: dict[tuple[int, str, datetime], list[CoteAControler]] = defaultdict(list)
    for cote in cotes:
        if cote.marche == "h2h" and cote.issue in ISSUES_1X2:
            groupes[(cote.match_id, cote.bookmaker, cote.releve_le)].append(cote)

    violations: list[Violation] = []
    for (match_id, bookmaker, releve), lot in sorted(
        groupes.items(), key=lambda item: (item[0][0], item[0][1], item[0][2])
    ):
        if {c.issue for c in lot} != ISSUES_1X2:
            continue
        if any(c.cote <= 0 for c in lot):
            continue  # deja signale par `cote_impossible`
        somme = sum(1 / c.cote for c in lot)
        if somme < 1 - TOLERANCE:
            violations.append(
                Violation(
                    regle="marche_a_marge_negative",
                    motif=(
                        f"match {match_id}, {bookmaker} : le 1X2 totalise "
                        f"{somme * 100:.1f} % au releve du "
                        f"{releve:%d/%m/%Y %H:%M} — un bookmaker ne price pas "
                        f"sous 100 %"
                    ),
                    identifiants=frozenset(c.identifiant for c in lot),
                )
            )
    return violations


# ---------------------------------------------------------------------------
# Recommandations
# ---------------------------------------------------------------------------


def probabilite_hors_bornes(recos: list[RecommandationAControler]) -> list[Violation]:
    """Une probabilite vit dans ]0, 1[. A 0 le pari ne peut pas gagner, a 1 il
    ne peut pas perdre : dans les deux cas il n'y a rien a recommander."""
    fautives = [r for r in recos if not (0 < r.probabilite_modele < 1)]
    if not fautives:
        return []
    return [
        Violation(
            regle="probabilite_hors_bornes",
            motif=(
                f"{len(fautives)} recommandation(s) avec une probabilite hors "
                f"]0,1[ (ex. {fautives[0].joueur} : {fautives[0].probabilite_modele})"
            ),
            identifiants=frozenset(r.identifiant for r in fautives),
        )
    ]


def prix_contradictoire(recos: list[RecommandationAControler]) -> list[Violation]:
    """Les quatre nombres d'une recommandation doivent se repondre.

    `cote_juste = 1 / probabilite_modele` et
    `avantage = cote_marche / cote_juste - 1`. Ce sont les formules qui les ont
    produits (`calculate_edge`) : s'ils ne se verifient plus, la ligne a ete
    ecrite par morceaux incoherents, et l'avantage affiche ne correspond a rien.
    """
    fautives: list[RecommandationAControler] = []
    exemple = ""
    for reco in recos:
        if not (0 < reco.probabilite_modele < 1) or reco.cote_juste <= 0:
            continue  # deja couvert par `probabilite_hors_bornes`
        cote_attendue = 1 / reco.probabilite_modele
        avantage_attendu = reco.cote_marche / reco.cote_juste - 1
        ecart_cote = abs(reco.cote_juste - cote_attendue)
        ecart_avantage = abs(reco.avantage - avantage_attendu)
        seuil_avantage = tolerance_avantage(reco.cote_juste, reco.cote_marche)
        if ecart_cote > TOLERANCE_COTE_JUSTE or ecart_avantage > seuil_avantage:
            fautives.append(reco)
            if not exemple:
                exemple = (
                    f"{reco.joueur} : probabilite {reco.probabilite_modele:.4f} "
                    f"appelle une cote juste de {cote_attendue:.3f}, la ligne "
                    f"porte {reco.cote_juste:.3f} ; avantage annonce "
                    f"{reco.avantage:+.4f} contre {avantage_attendu:+.4f} attendu"
                )
    if not fautives:
        return []
    return [
        Violation(
            regle="prix_contradictoire",
            motif=f"{len(fautives)} recommandation(s) incoherentes — {exemple}",
            identifiants=frozenset(r.identifiant for r in fautives),
        )
    ]


REGLES_COTES = (cote_impossible, marche_a_marge_negative)
REGLES_RECOMMANDATIONS = (probabilite_hors_bornes, prix_contradictoire)


def controler_cotes(cotes: list[CoteAControler]) -> list[Violation]:
    violations: list[Violation] = []
    for regle in REGLES_COTES:
        violations.extend(regle(cotes))
    return violations


def controler_recommandations(
    recos: list[RecommandationAControler],
) -> list[Violation]:
    violations: list[Violation] = []
    for regle in REGLES_RECOMMANDATIONS:
        violations.extend(regle(recos))
    return violations


def resumer(violations: list[Violation], quoi: str, maximum: int = 5) -> str:
    """Message court pour le canal `incidents`."""
    if not violations:
        return f"{quoi} : rien a signaler"

    touches: set[int] = set()
    for violation in violations:
        touches |= violation.identifiants

    lignes = [
        f"{quoi} : {len(violations)} violation(s) sur {len(touches)} ligne(s)."
    ]
    lignes.extend(f"- {v.motif}" for v in violations[:maximum])
    if len(violations) > maximum:
        lignes.append(f"- ... et {len(violations) - maximum} autre(s)")
    return "\n".join(lignes)
