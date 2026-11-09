"""SQLAlchemy models."""

from app.models.autopilot import AutopilotDecision
from app.models.bankroll import BankrollEntry
from app.models.base import Base
from app.models.bzz_lineup_slots import BzzLineupSlot  # noqa: F401
from app.models.canonical_teams import CanonicalTeam
from app.models.career_import import CareerImportAttempt
from app.models.data_coverage import DataCoverageRun
from app.models.fixtures import Fixture
from app.models.leagues import League
from app.models.lineups import TeamLineup, TeamLineupPlayer  # noqa: F401
from app.models.match_events import MatchEvent
from app.models.match_odds import MatchOddsSnapshot
from app.models.model_pricing import ModelPricingSnapshot
from app.models.odds_scrape_state import OddsScrapeState
from app.models.player_career import PlayerCareerSeason
from app.models.player_match_minutes import PlayerMatchMinutes
from app.models.player_odds_snapshot import PlayerOddsSnapshot
from app.models.player_tm_id import PlayerTmId
from app.models.poll_state import OddsPortalPollState
from app.models.recommendations import Recommendation
from app.models.selection_stats import PlayerSelectionSeasonStat
from app.models.settings import UserSettings
from app.models.squad_sync import SquadSyncRun
from app.models.team_xg import TeamXgEstimate
from app.models.wc2026 import WC2026SquadPlayer
from app.models.wc2026_advancement import WC2026TeamAdvancement  # noqa: F401
from app.models.wc2026_lineups import WC2026ExpectedLineup, WC2026ExpectedLineupPlayer  # noqa: F401
from app.models.wc2026_odds import WC2026OutrightOdd  # noqa: F401
from app.models.wc2026_pricing import WC2026PlayerPricing  # noqa: F401

__all__ = [
    "AutopilotDecision",
    "Base",
    "BankrollEntry",
    "BzzLineupSlot",
    "CanonicalTeam",
    "CareerImportAttempt",
    "DataCoverageRun",
    "Fixture",
    "League",
    "MatchEvent",
    "MatchOddsSnapshot",
    "ModelPricingSnapshot",
    "OddsPortalPollState",
    "OddsScrapeState",
    "PlayerCareerSeason",
    "PlayerMatchMinutes",
    "PlayerOddsSnapshot",
    "PlayerSelectionSeasonStat",
    "PlayerTmId",
    "Recommendation",
    "SquadSyncRun",
    "TeamLineup",
    "TeamLineupPlayer",
    "TeamXgEstimate",
    "UserSettings",
    "WC2026ExpectedLineup",
    "WC2026ExpectedLineupPlayer",
    "WC2026OutrightOdd",
    "WC2026PlayerPricing",
    "WC2026SquadPlayer",
    "WC2026TeamAdvancement",
]
