"""WC2026 tournament pricing endpoints."""
from __future__ import annotations

import time
import unicodedata
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.wc2026 import WC2026SquadPlayer
from app.models.wc2026_odds import WC2026OutrightOdd
from app.models.wc2026_pricing import WC2026PlayerPricing
from app.pricing.wc2026_tournament import compute_tournament_pricing

router = APIRouter(prefix="/wc2026/pricing", tags=["wc2026"])

_NATION_MARKETS = ("winner", "top4", "top8", "group_stage")
_BOOKMAKERS = ("unibet", "pmu", "betclic")


def _norm_name(name: str) -> str:
    n = unicodedata.normalize("NFKD", (name or "").lower().strip())
    return "".join(c for c in n if not unicodedata.combining(c))


# ── Player pricing ────────────────────────────────────────────────────────────

class PlayerPricingOut(BaseModel):
    nation: str
    player_name: str
    position: str | None
    expected_games: float | None = None
    lambda_goals: float
    lambda_assists: float
    lambda_remaining_goals: float | None = None
    lambda_remaining_assists: float | None = None
    wc_goals: int | None = None
    wc_assists: int | None = None
    wc_minutes: int | None = None
    wc_xg_per_90: float | None = None
    prior_xg_p90: float | None = None
    blended_xg_p90: float | None = None
    p_1g: float | None
    p_2g: float | None
    p_3g: float | None
    p_4g: float | None
    fair_1g: float | None
    fair_2g: float | None
    fair_3g: float | None
    fair_4g: float | None
    p_1a: float | None
    p_2a: float | None
    p_3a: float | None
    fair_1a: float | None
    fair_2a: float | None
    fair_3a: float | None
    p_top_scorer: float | None
    p_top_assister: float | None
    fair_top_scorer: float | None
    fair_top_assister: float | None
    p_most_decisive: float | None = None
    fair_most_decisive: float | None = None
    p_top3_decisive: float | None = None
    fair_top3_decisive: float | None = None
    p_top3_scorer: float | None = None
    fair_top3_scorer: float | None = None
    p_top3_assister: float | None = None
    fair_top3_assister: float | None = None
    computed_at: datetime | None = None
    bk_top_scorer: float | None = None          # meilleure cote toutes books
    bk_top_scorer_unibet: float | None = None
    bk_top_scorer_betclic: float | None = None
    bk_top_scorer_pmu: float | None = None
    bk_top_assister: float | None = None        # meilleure cote toutes books
    bk_top_assister_unibet: float | None = None
    bk_top_assister_betclic: float | None = None
    bk_top_assister_pmu: float | None = None
    edge_top_scorer: float | None = None
    edge_top_assister: float | None = None


class ComputeResult(BaseModel):
    players_computed: int
    nations_computed: int
    duration_s: float


@router.post("/compute", response_model=ComputeResult)
async def compute_pricing(session: AsyncSession = Depends(get_db)) -> ComputeResult:
    """Recompute all WC2026 player tournament pricing. Truncates and reinserts the table."""
    t0 = time.monotonic()
    rows = await compute_tournament_pricing(session)
    await session.execute(text("TRUNCATE TABLE wc2026_player_pricing RESTART IDENTITY"))
    for row in rows:
        session.add(WC2026PlayerPricing(**row))
    await session.commit()
    nations = len({r["nation"] for r in rows})
    return ComputeResult(
        players_computed=len(rows),
        nations_computed=nations,
        duration_s=round(time.monotonic() - t0, 2),
    )


@router.get("/players", response_model=list[PlayerPricingOut])
async def get_pricing_players(
    nation: str | None = None,
    position: str | None = None,
    min_lambda: float | None = None,
    session: AsyncSession = Depends(get_db),
) -> list[PlayerPricingOut]:
    """Return priced players ordered by lambda_goals desc, enriched with bookmaker edge."""
    q = select(WC2026PlayerPricing)
    if nation:
        q = q.where(WC2026PlayerPricing.nation == nation)
    if position:
        q = q.where(WC2026PlayerPricing.position == position)
    if min_lambda is not None:
        q = q.where(WC2026PlayerPricing.lambda_goals >= min_lambda)
    q = q.order_by(WC2026PlayerPricing.lambda_goals.desc())

    result = await session.execute(q)
    players = result.scalars().all()

    ts_res = await session.execute(
        select(WC2026OutrightOdd.player_name, WC2026OutrightOdd.bookmaker, WC2026OutrightOdd.odds)
        .where(WC2026OutrightOdd.market_type == "top_scorer")
        .where(WC2026OutrightOdd.player_name.isnot(None))
        .where(WC2026OutrightOdd.is_active.is_(True))
    )
    bk_scorer: dict[str, dict[str, float]] = {}
    for name, bookmaker, odds in ts_res.all():
        bk_scorer.setdefault(_norm_name(name), {})[bookmaker] = odds

    ta_res = await session.execute(
        select(WC2026OutrightOdd.player_name, WC2026OutrightOdd.bookmaker, WC2026OutrightOdd.odds)
        .where(WC2026OutrightOdd.market_type == "top_assister")
        .where(WC2026OutrightOdd.player_name.isnot(None))
        .where(WC2026OutrightOdd.is_active.is_(True))
    )
    bk_assister: dict[str, dict[str, float]] = {}
    for name, bookmaker, odds in ta_res.all():
        bk_assister.setdefault(_norm_name(name), {})[bookmaker] = odds

    out = []
    for p in players:
        key = _norm_name(p.player_name)
        ts = bk_scorer.get(key, {})
        ta = bk_assister.get(key, {})
        bk_ts_best = max(ts.values()) if ts else None
        bk_ta_best = max(ta.values()) if ta else None
        edge_ts = round((bk_ts_best / p.fair_top_scorer) - 1, 4) if bk_ts_best and p.fair_top_scorer else None
        edge_ta = round((bk_ta_best / p.fair_top_assister) - 1, 4) if bk_ta_best and p.fair_top_assister else None
        out.append(PlayerPricingOut(
            nation=p.nation,
            player_name=p.player_name,
            position=p.position,
            expected_games=p.expected_games,
            lambda_goals=p.lambda_goals,
            lambda_assists=p.lambda_assists,
            lambda_remaining_goals=p.lambda_remaining_goals,
            lambda_remaining_assists=p.lambda_remaining_assists,
            wc_goals=p.wc_goals,
            wc_assists=p.wc_assists,
            wc_minutes=p.wc_minutes,
            wc_xg_per_90=p.wc_xg_per_90,
            prior_xg_p90=p.prior_xg_p90,
            blended_xg_p90=p.blended_xg_p90,
            p_1g=p.p_1g, p_2g=p.p_2g, p_3g=p.p_3g, p_4g=p.p_4g,
            fair_1g=p.fair_1g, fair_2g=p.fair_2g, fair_3g=p.fair_3g, fair_4g=p.fair_4g,
            p_1a=p.p_1a, p_2a=p.p_2a, p_3a=p.p_3a,
            fair_1a=p.fair_1a, fair_2a=p.fair_2a, fair_3a=p.fair_3a,
            p_top_scorer=p.p_top_scorer,
            p_top_assister=p.p_top_assister,
            fair_top_scorer=p.fair_top_scorer,
            fair_top_assister=p.fair_top_assister,
            p_most_decisive=p.p_most_decisive,
            fair_most_decisive=p.fair_most_decisive,
            p_top3_decisive=p.p_top3_decisive,
            fair_top3_decisive=p.fair_top3_decisive,
            p_top3_scorer=p.p_top3_scorer,
            fair_top3_scorer=p.fair_top3_scorer,
            p_top3_assister=p.p_top3_assister,
            fair_top3_assister=p.fair_top3_assister,
            computed_at=p.computed_at,
            bk_top_scorer=bk_ts_best,
            bk_top_scorer_unibet=ts.get("unibet"),
            bk_top_scorer_betclic=ts.get("betclic"),
            bk_top_scorer_pmu=ts.get("pmu"),
            bk_top_assister=bk_ta_best,
            bk_top_assister_unibet=ta.get("unibet"),
            bk_top_assister_betclic=ta.get("betclic"),
            bk_top_assister_pmu=ta.get("pmu"),
            edge_top_scorer=edge_ts,
            edge_top_assister=edge_ta,
        ))
    return out


# ── Nation outright odds ──────────────────────────────────────────────────────

class BookmakerOddEntry(BaseModel):
    odds: float | None = None
    is_active: bool = True
    last_seen_at: datetime | None = None
    odds_changed_at: datetime | None = None
    republished_at: datetime | None = None


class MarketOdds(BaseModel):
    unibet: BookmakerOddEntry = BookmakerOddEntry()
    pmu: BookmakerOddEntry = BookmakerOddEntry()
    betclic: BookmakerOddEntry = BookmakerOddEntry()


class NationOddsRow(BaseModel):
    nation: str
    group_letter: str | None
    flag_emoji: str | None
    winner: MarketOdds
    top4: MarketOdds
    top8: MarketOdds
    group_stage: MarketOdds


@router.get("/nations", response_model=list[NationOddsRow])
async def get_nation_odds(
    session: AsyncSession = Depends(get_db),
) -> list[NationOddsRow]:
    """Return nation-level outright odds per market × bookmaker, with suspension status."""
    odds_res = await session.execute(
        select(
            WC2026OutrightOdd.nation,
            WC2026OutrightOdd.market_type,
            WC2026OutrightOdd.bookmaker,
            WC2026OutrightOdd.odds,
            WC2026OutrightOdd.is_active,
            WC2026OutrightOdd.last_seen_at,
            WC2026OutrightOdd.odds_changed_at,
            WC2026OutrightOdd.republished_at,
        ).where(
            WC2026OutrightOdd.nation.isnot(None),
            WC2026OutrightOdd.market_type.in_(_NATION_MARKETS),
        )
    )
    # Pivot : { nation → { market_type → { bookmaker → BookmakerOddEntry } } }
    pivot: dict[str, dict[str, dict[str, BookmakerOddEntry]]] = {}
    for nation, mtype, bk, odds, is_active, last_seen_at, odds_changed_at, republished_at in odds_res.all():
        entry = BookmakerOddEntry(
            odds=odds,
            is_active=is_active,
            last_seen_at=last_seen_at,
            odds_changed_at=odds_changed_at,
            republished_at=republished_at,
        )
        pivot.setdefault(nation, {}).setdefault(mtype, {})[bk] = entry

    # Métadonnées nations — dedup en Python
    meta_res = await session.execute(
        select(
            WC2026SquadPlayer.nation,
            WC2026SquadPlayer.group_letter,
            WC2026SquadPlayer.flag_emoji,
        )
    )
    meta: dict[str, dict] = {}
    for r in meta_res.all():
        if r.nation not in meta:
            meta[r.nation] = {"group_letter": r.group_letter, "flag_emoji": r.flag_emoji}

    all_nations = sorted(
        meta.keys() | pivot.keys(),
        key=lambda n: (meta.get(n, {}).get("group_letter", "Z"), n),
    )

    def _market(nation: str, mtype: str) -> MarketOdds:
        bk = pivot.get(nation, {}).get(mtype, {})
        return MarketOdds(
            unibet=bk.get("unibet", BookmakerOddEntry()),
            pmu=bk.get("pmu", BookmakerOddEntry()),
            betclic=bk.get("betclic", BookmakerOddEntry()),
        )

    rows = []
    for nation in all_nations:
        m = meta.get(nation, {})
        rows.append(NationOddsRow(
            nation=nation,
            group_letter=m.get("group_letter"),
            flag_emoji=m.get("flag_emoji"),
            winner=_market(nation, "winner"),
            top4=_market(nation, "top4"),
            top8=_market(nation, "top8"),
            group_stage=_market(nation, "group_stage"),
        ))
    return rows


# ── Store odds (reçoit des cotes pré-scrapées depuis un client local) ────────

class OddsInput(BaseModel):
    nation: str | None = None
    player_name: str | None = None
    market_type: str
    bookmaker: str
    odds: float


class StoreOddsResult(BaseModel):
    bookmaker: str
    scraped: int
    deactivated: int


@router.post("/store-odds", response_model=StoreOddsResult)
async def store_odds(
    bookmaker: str,
    payload: list[OddsInput],
    session: AsyncSession = Depends(get_db),
) -> StoreOddsResult:
    """Reçoit des cotes pré-scrapées (ex: Betclic depuis local) et les stocke."""
    if bookmaker not in _BOOKMAKERS:
        raise HTTPException(400, f"bookmaker doit être parmi {_BOOKMAKERS}")
    from app.ingestion.wc2026.sync_wc_outrights import store_wc_outrights_for_bookmaker
    rows = [r.model_dump() for r in payload]
    stats = await store_wc_outrights_for_bookmaker(session, rows, bookmaker)
    return StoreOddsResult(bookmaker=bookmaker, scraped=stats["scraped"], deactivated=stats["deactivated"])


# ── Sync odds (déclenche le scraper depuis l'API) ─────────────────────────────

class SyncOddsResult(BaseModel):
    bookmaker: str
    scraped: int
    deactivated: int
    duration_s: float
    note: str | None = None


@router.post("/sync-odds", response_model=SyncOddsResult)
async def sync_odds(
    bookmaker: str,
    session: AsyncSession = Depends(get_db),
) -> SyncOddsResult:
    """Déclenche le scraping des cotes outrights pour un bookmaker donné.

    PMU et Unibet fonctionnent depuis le VPS (httpx pur).
    Betclic nécessite Playwright — retourne une erreur explicite si absent.
    """
    if bookmaker not in _BOOKMAKERS:
        raise HTTPException(400, f"bookmaker doit être parmi {_BOOKMAKERS}")

    from app.ingestion.wc2026.sync_wc_outrights import (
        scrape_betclic_wc_outrights,
        scrape_pmu_wc_outrights,
        scrape_unibet_wc_outrights,
        store_wc_outrights_for_bookmaker,
    )

    scraper_map = {
        "unibet": scrape_unibet_wc_outrights,
        "pmu": scrape_pmu_wc_outrights,
        "betclic": scrape_betclic_wc_outrights,
    }

    t0 = time.monotonic()
    note = None

    try:
        rows = await scraper_map[bookmaker]()
    except Exception as exc:
        err_str = str(exc)
        # Playwright installé mais navigateur non téléchargé / IP restreinte
        if bookmaker == "betclic" and (
            "Executable doesn't exist" in err_str
            or "playwright install" in err_str
            or "BrowserType.launch" in err_str
        ):
            return SyncOddsResult(
                bookmaker=bookmaker,
                scraped=0,
                deactivated=0,
                duration_s=round(time.monotonic() - t0, 2),
                note="Chromium absent sur ce serveur — lancez le script localement.",
            )
        raise HTTPException(502, f"Scraping {bookmaker} échoué : {exc}") from exc

    if bookmaker == "betclic" and not rows:
        # Playwright non installé → scraper retourne [] silencieusement
        try:
            from playwright.async_api import async_playwright  # noqa: F401
        except ImportError:
            return SyncOddsResult(
                bookmaker=bookmaker,
                scraped=0,
                deactivated=0,
                duration_s=round(time.monotonic() - t0, 2),
                note="Playwright non installé sur ce serveur — lancez le script localement.",
            )

    stats = await store_wc_outrights_for_bookmaker(session, rows, bookmaker)

    return SyncOddsResult(
        bookmaker=bookmaker,
        scraped=stats["scraped"],
        deactivated=stats["deactivated"],
        duration_s=round(time.monotonic() - t0, 2),
        note=note,
    )
