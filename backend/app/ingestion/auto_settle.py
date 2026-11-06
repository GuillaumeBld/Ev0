"""Reglement automatique des recommandations approuvees.

LES MINUTES GOUVERNENT. Pour chaque recommandation d'un match termine :

  1. minutes jouees == 0, ou joueur absent du groupe  -> ANNULE
  2. minutes > 0 et buts du match complets            -> GAGNE ou PERDU
  3. minutes > 0 mais buts pas encore ingeres          -> en attente
  4. aucune donnee de minutes                          -> en attente

L'ordre compte : un remplacant jamais entre est ANNULE, jamais PERDU. Sa
mise est rendue. Trancher sur les buts avant de regarder les minutes
inscrivait au registre des pertes qui n'avaient pas eu lieu.

Les minutes viennent de `bzz_player_match_stats` (1 441 029 lignes, 99 % des
matchs termines) complete par `player_match_minutes` (470 lignes, prioritaire
la ou il existe). Les incidents de match ne peuvent pas les remplacer : un
joueur absent des incidents est soit un titulaire reste 90 minutes sans rien
faire, soit un remplacant non utilise -- deux verdicts opposes.

La couverture des buts doit etre EXACTE (nb d'events but == score) avant de
trancher gagne/perdu : regler sur des buts partiels marque PERDU des paris
gagnants, constate le 09/07/2026 sur 151 matchs a 40 % de couverture.
"""

import logging
import unicodedata
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.sync_incidents import SENTINEL_UNAVAILABLE_TYPE
from app.models.fixtures import Fixture
from app.models.match_events import MatchEvent
from app.models.player_match_minutes import PlayerMatchMinutes
from app.models.recommendations import Recommendation

logger = logging.getLogger(__name__)


def _normalize_name(name: str) -> str:
    """Normalize a player name for fuzzy matching.

    Removes accents, hyphens, apostrophes, spaces, lowercases.
    Examples: 'Al-Tamari' == 'Al Tamari' == 'Altamari'
              "N'Diaye" == 'Ndiaye' == 'N Diaye'
              'Gueye' == 'Guey\u00e9'
    """
    normalized = unicodedata.normalize("NFKD", name.lower().strip())
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    normalized = normalized.replace("-", "").replace("'", "").replace("\u2019", "").replace(" ", "")
    return normalized


def _names_match(a: str, b: str) -> bool:
    """Check if two player names refer to the same person.

    Handles cases where one source drops a middle name:
      'Idrissa Gueye' == 'Idrissa Gana Gueye'
      'N'Diaye' == 'Ndiaye'
    """
    na, nb = _normalize_name(a), _normalize_name(b)
    if na == nb:
        return True
    # One normalized form contains the other (covers suffix/prefix middle names)
    if na in nb or nb in na:
        return True
    # First name + last name match even if middle name differs
    a_words = [w for w in a.lower().split() if w]
    b_words = [w for w in b.lower().split() if w]
    if len(a_words) >= 2 and len(b_words) >= 2:
        if (
            _normalize_name(a_words[0]) == _normalize_name(b_words[0])
            and _normalize_name(a_words[-1]) == _normalize_name(b_words[-1])
        ):
            return True
    return False


async def _minutes_du_match(db: AsyncSession, fixture) -> dict[str, float] | None:
    """Minutes jouees par joueur sur ce match : {nom -> minutes}.

    Rend None quand AUCUNE donnee de minutes n'existe : on ne peut alors rien
    trancher, et surtout pas supposer qu'un joueur a joue.

    POURQUOI. Le reglement lisait `player_match_minutes`, qui compte 470
    lignes. `bzz_player_match_stats` en compte 1 441 029, couvre 1 718 des
    1 728 matchs termines des 30 derniers jours (99 %), et distingue
    explicitement 461 402 lignes a zero minute -- les joueurs du groupe qui ne
    sont jamais entres.

    Sans cette distinction, un remplacant jamais entre est marque PERDU au
    lieu d'ANNULE : une perte inscrite au registre alors que la mise aurait du
    etre rendue. Les incidents de match ne peuvent pas la fournir -- un joueur
    absent des incidents est soit un titulaire reste 90 minutes sans rien
    faire, soit un remplacant non utilise, deux verdicts opposes que rien ne
    separe.

    `player_match_minutes` est applique par-dessus : la ou il existe, il fait
    autorite.
    """
    minutes: dict[str, float] = {}

    ext = getattr(fixture, "external_id", None) or ""
    if ext.startswith("bzz_"):
        try:
            bzz_event_id = int(ext[len("bzz_"):])
        except ValueError:
            bzz_event_id = None
        if bzz_event_id is not None:
            rows = (await db.execute(
                sa_text("""
                    SELECT p.name, m.minutes_played
                    FROM bzz_player_match_stats m
                    JOIN bzz_players p ON p.api_id = m.player_api_id
                    WHERE m.event_api_id = :ev
                """),
                {"ev": bzz_event_id},
            )).all()
            for nom, mn in rows:
                if nom:
                    minutes[nom] = float(mn or 0)

    pmm_rows = (await db.execute(
        select(PlayerMatchMinutes).where(PlayerMatchMinutes.fixture_id == fixture.id)
    )).scalars().all()
    for pmm in pmm_rows:
        if pmm.player_name:
            minutes[pmm.player_name] = float(pmm.minutes_played or 0)

    return minutes or None


def _minutes_du_joueur(minutes: dict[str, float] | None, player_name: str) -> float | None:
    """Minutes d'un joueur, ou None si le match n'en dit rien.

    Rend 0.0 quand le match a des donnees mais que le joueur n'y figure pas :
    il n'etait pas dans le groupe, donc il n'a pas joue -- un ANNULE, pas un
    perdu.
    """
    if minutes is None:
        return None
    for nom, mn in minutes.items():
        if _names_match(nom, player_name):
            return mn
    return 0.0


# Un 1N2 se regle sur le temps reglementaire. Le soir d'un match prolonge, la
# synchro peut capter le score apres prolongation, que Bzzoiro remplace ensuite
# par le score a 90' : on attend que le score soit stabilise. Le 12/10/2026,
# Belgique–Senegal (2-2 a 90', 3-2 a.p.) et Celje–Slovan (1-1, 1-2 a.p.)
# avaient ete regles gagnants a tort.
_H2H_SETTLE_DELAY = timedelta(hours=6)

# market_type → list of valid MatchEvent.event_type values
_MARKET_TO_EVENTS: dict[str, list[str]] = {
    "goalscorer": ["goal", "penalty_goal"],
    "anytime_score": ["goal", "penalty_goal"],
    "assist": ["assist"],
    "anytime_assist": ["assist"],
}

# bet_type → event types (for standard/supersub recs that use bet_type, not market_type)
_BET_TYPE_TO_EVENTS: dict[str, list[str]] = {
    "goal": ["goal", "penalty_goal"],
    "assist": ["assist"],
}


async def settle_approved_recommendations(db: AsyncSession) -> dict:
    """Regle les recommandations approuvees des matchs termines.

    Les minutes jouees decident ANNULE ; les buts decident GAGNE/PERDU.
    Rend un dict : settled, won, lost, void, stuck_fixture_ids.
    """
    # 1. Find all approved recs with result=None + finished fixture
    stmt = (
        select(Recommendation, Fixture)
        .join(Fixture, Recommendation.fixture_id == Fixture.id)
        .where(
            Recommendation.status == "approved",
            Recommendation.result.is_(None),
            Fixture.status == "finished",
        )
    )
    rows = (await db.execute(stmt)).all()

    if not rows:
        logger.info("auto_settle: no unsettled approved recs with finished fixtures")
        return {"settled": 0, "won": 0, "lost": 0, "void": 0, "stuck_fixture_ids": []}

    logger.info("auto_settle: %d recs to settle", len(rows))

    # fixture_id -> {nom: minutes}, ou None si le match n'a aucune donnee
    _minutes_cache: dict[int, dict[str, float] | None] = {}
    # event_types distincts par fixture (set vide = aucun event synchronisé)
    _events_cache: dict[int, set] = {}

    settled = 0
    won_count = 0
    lost_count = 0
    void_count = 0
    stuck_fixture_ids: set[int] = set()
    for rec, fixture in rows:
        market = rec.market_type.value if hasattr(rec.market_type, "value") else str(rec.market_type)

        # ── H2H: settle against match score ────────────────────────────────────
        if market == "h2h":
            if (
                fixture.kickoff_utc is not None
                and datetime.now(UTC) < fixture.kickoff_utc + _H2H_SETTLE_DELAY
            ):
                logger.debug(
                    "auto_settle: h2h rec %d — score not settled yet (kickoff %s), skipping",
                    rec.id, fixture.kickoff_utc,
                )
                continue
            if fixture.home_score is None or fixture.away_score is None:
                stuck_fixture_ids.add(fixture.id)
                logger.debug(
                    "auto_settle: h2h rec %d — fixture %d has no score yet, skipping",
                    rec.id, fixture.id,
                )
                continue

            outcome = rec.player_name  # team name or "Nul"
            hs, as_ = fixture.home_score, fixture.away_score

            if _normalize_name(outcome) in ("nul", "draw", "x"):
                won = hs == as_
            elif _names_match(outcome, fixture.home_team or ""):
                won = hs > as_
            elif _names_match(outcome, fixture.away_team or ""):
                won = as_ > hs
            else:
                logger.warning(
                    "auto_settle: h2h rec %d — cannot match outcome '%s' to '%s' vs '%s'",
                    rec.id, outcome, fixture.home_team, fixture.away_team,
                )
                continue

            result = "won" if won else "lost"
            pnl = round(10.0 * (rec.best_odds - 1), 2) if won else -10.0
            rec.result = result
            rec.pnl = pnl
            rec.settled_utc = datetime.now(UTC)
            settled += 1
            if won:
                won_count += 1
            else:
                lost_count += 1
            logger.info(
                "auto_settle: h2h rec %d (%s) → %s (score %d-%d) pnl=%.2f",
                rec.id, outcome, result, hs, as_, pnl,
            )
            continue

        # ── Standard / Supersub: use bet_type to find event types ──────────────
        if market in ("standard", "supersub"):
            bet = rec.bet_type.value if hasattr(rec.bet_type, "value") else str(rec.bet_type)
            event_types = _BET_TYPE_TO_EVENTS.get(bet, ["goal", "penalty_goal"])
        else:
            event_types = _MARKET_TO_EVENTS.get(market)

        if event_types is None:
            logger.warning("auto_settle: unknown market_type '%s' for rec %d", rec.market_type, rec.id)
            continue

        # --- Minutes jouees : qui a joue, qui n'a pas joue ---
        # C'est la donnee qui separe un PERDU d'un ANNULE. Elle gouverne le
        # reglement avant toute lecture des buts : un joueur qui n'est pas
        # entre n'a pas perdu son pari, sa mise est rendue.
        if fixture.id not in _minutes_cache:
            _minutes_cache[fixture.id] = await _minutes_du_match(db, fixture)

        minutes_du_match = _minutes_cache[fixture.id]
        minutes_joueur = _minutes_du_joueur(minutes_du_match, rec.player_name)

        # --- Load MatchEvents (types + nb d'events "but") ---
        if fixture.id not in _events_cache:
            from sqlalchemy import func as sa_func

            ev_res = await db.execute(
                select(MatchEvent.event_type, sa_func.count())
                .where(MatchEvent.fixture_id == fixture.id)
                .group_by(MatchEvent.event_type)
            )
            counts = dict(ev_res.all())
            _events_cache[fixture.id] = {
                "types": set(counts),
                "goals": sum(counts.get(t, 0) for t in ("goal", "own_goal", "penalty_goal")),
            }

        ev_info = _events_cache[fixture.id]
        fixture_event_types = ev_info["types"]

        # Couverture events complète = nb d'events "but" == score du match.
        # Régler WON/LOST sur des events partiels marque LOST des paris
        # gagnants (constaté le 09/07 : ~40% de couverture sur 151 fixtures).
        events_complete = (
            fixture.home_score is not None
            and fixture.away_score is not None
            and ev_info["goals"] == fixture.home_score + fixture.away_score
        )

        # Incidents définitivement indisponibles (404 Bzzoiro) et couverture
        # incomplète : impossible de vérifier le résultat → VOID plutôt que
        # LOST ou blocage éternel.
        if not events_complete and SENTINEL_UNAVAILABLE_TYPE in fixture_event_types:
            rec.result = "void"
            rec.pnl = 0.0
            rec.settled_utc = datetime.now(UTC)
            settled += 1
            void_count += 1
            logger.info(
                "auto_settle: rec %d (%s %s) → VOID (incidents indisponibles pour fixture %d)",
                rec.id, rec.player_name, rec.market_type, fixture.id,
            )
            continue

        has_events = events_complete

        # LES MINUTES GOUVERNENT, AVANT TOUTE LECTURE DES BUTS.
        #
        # Un joueur qui n'est pas entre n'a pas perdu son pari : sa mise est
        # rendue. Marquer PERDU un remplacant non utilise inscrit une perte
        # qui n'a jamais eu lieu.
        #
        # Le cas s'applique aussi quand le joueur ne figure pas du tout dans
        # les minutes du match alors que le match en a : il n'etait pas dans
        # le groupe (`_minutes_du_joueur` rend 0.0 dans ce cas).
        #
        # Minutes inconnues (le match n'a aucune donnee) : on ne tranche pas
        # ici, les branches suivantes laissent la reco en attente plutot que
        # de supposer qu'il a joue.
        if minutes_joueur is not None and minutes_joueur <= 0:
            rec.result = "void"
            rec.pnl = 0.0
            rec.settled_utc = datetime.now(UTC)
            settled += 1
            void_count += 1
            logger.info(
                "auto_settle: rec %d (%s %s) → VOID (%.0f minute jouee)",
                rec.id, rec.player_name, rec.market_type, minutes_joueur,
            )
            continue

        if has_events:
            all_events_result = await db.execute(
                select(MatchEvent).where(
                    MatchEvent.fixture_id == fixture.id,
                    MatchEvent.event_type.in_(event_types),
                )
            )
            fixture_events = all_events_result.scalars().all()
            won = any(_names_match(ev.player_name, rec.player_name) for ev in fixture_events)

            # Un but prouve la participation : GAGNE se tranche sans les
            # minutes. PERDU, non -- il faut avoir etabli que le joueur a
            # joue, sinon on inscrit une perte pour un joueur peut-etre
            # jamais entre. Minutes inconnues et pas de but : on attend.
            if not won and minutes_joueur is None:
                logger.debug(
                    "auto_settle: reco %d (%s) sans but et minutes inconnues — "
                    "laissee en attente plutot que marquee PERDU",
                    rec.id, rec.player_name,
                )
                stuck_fixture_ids.add(fixture.id)
                continue

            result = "won" if won else "lost"
            pnl = round(10.0 * (rec.best_odds - 1), 2) if won else -10.0

            rec.result = result
            rec.pnl = pnl
            rec.settled_utc = datetime.now(UTC)
            settled += 1
            if won:
                won_count += 1
            else:
                lost_count += 1

            logger.info(
                "auto_settle: rec %d (%s %s) → %s pnl=%.2f",
                rec.id, rec.player_name, rec.market_type, result, pnl,
            )

        elif minutes_joueur is not None:
            # Le joueur a joue (le cas 0 minute a deja ete tranche plus haut)
            # mais les buts ne sont pas encore la : on attend la synchro des
            # incidents plutot que de marquer PERDU un pari peut-etre gagnant.
            logger.debug(
                "auto_settle: fixture %d a les minutes mais pas les buts — "
                "reco %d laissee en attente",
                fixture.id, rec.id,
            )
            stuck_fixture_ids.add(fixture.id)

        else:
            # Ni buts ni minutes : rien ne permet de trancher, on laisse
            # courir. Supposer qu'il a joue marquerait PERDU a tort.
            logger.debug(
                "auto_settle: aucune donnee pour fixture %d (%s vs %s) — ignoree",
                fixture.id, fixture.home_team, fixture.away_team,
            )
            stuck_fixture_ids.add(fixture.id)

    await db.commit()
    logger.info("auto_settle: committed %d settlements", settled)
    return {
        "settled": settled,
        "won": won_count,
        "lost": lost_count,
        "void": void_count,
        "stuck_fixture_ids": list(stuck_fixture_ids),
    }
