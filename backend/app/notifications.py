"""Telegram notification helpers."""
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Chat ID configurable par env TELEGRAM_CHAT_ID (défaut = ID perso historique)
TELEGRAM_CHAT_ID = settings.telegram_chat_id

_ACTION_LABELS = {
    0: "SKIP",
    1: "Half Kelly (0.5×)",
    2: "Full Kelly (1×)",
    3: "Aggressive (1.5×)",
}

# Live-readiness thresholds
_THRESH_SETTLED = 150
_THRESH_WIN_RATE = 0.27   # goalscorer base rate ~25-30%
_THRESH_ROI = 0.0
_THRESH_FINE_TUNE = 3
_THRESH_QUOTA = 50


async def send_telegram_alert(message: str) -> bool:
    """Send a message to the Ev0 Telegram group. Returns True on success."""
    token = getattr(settings, "telegram_bot_token", None)
    if not token:
        logger.debug("TELEGRAM_BOT_TOKEN not set — skipping notification")
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
                timeout=10,
            )
            r.raise_for_status()
            return True
    except Exception as exc:
        logger.warning("Telegram notification failed: %s", exc)
        return False


def _scorecard(
    settled: int,
    won: int,
    total_pnl: float,
    staked_total: float,
    fine_tune_runs: int,
    odds_api_remaining: int | None = None,
) -> str:
    """Build a live-readiness scorecard block."""
    win_rate = won / settled if settled > 0 else 0.0
    roi = total_pnl / staked_total if staked_total > 0 else 0.0

    def ck(ok: bool) -> str:
        return "✅" if ok else "❌"

    lines = [
        "",
        "<b>── Scorecard live ──</b>",
        f"{ck(settled >= _THRESH_SETTLED)} Paris réglés : {settled} / {_THRESH_SETTLED}",
        f"{ck(win_rate >= _THRESH_WIN_RATE)} Win rate : {win_rate:.0%}  (seuil ≥{_THRESH_WIN_RATE:.0%})",
        f"{ck(roi >= _THRESH_ROI)} ROI : {roi:+.1%}  (seuil ≥0%)",
        f"{ck(fine_tune_runs >= _THRESH_FINE_TUNE)} Fine-tune runs : {fine_tune_runs} / {_THRESH_FINE_TUNE}",
    ]
    if odds_api_remaining is not None:
        lines.append(
            f"{ck(odds_api_remaining >= _THRESH_QUOTA)} Odds API quota : {odds_api_remaining} req restantes"
        )

    ready = (
        settled >= _THRESH_SETTLED
        and roi >= _THRESH_ROI
        and fine_tune_runs >= _THRESH_FINE_TUNE
    )
    lines.append("")
    lines.append(
        "<b>PRÊT POUR LE LIVE 🚀</b>" if ready else "<b>Mode paper — pas encore prêt</b>"
    )
    return "\n".join(lines)


async def notify_autopilot_position(
    *,
    player_name: str,
    fixture_name: str,
    market_type: str,
    best_odds: float,
    edge: float,
    stake: float,
    action_idx: int,
    mode: str,
    settled: int,
    won: int,
    total_pnl: float,
    staked_total: float,
    fine_tune_runs: int,
    odds_api_remaining: int | None = None,
) -> None:
    """Notify when autopilot takes a position (action_idx > 0)."""
    market_label = "Buteur" if market_type == "goalscorer" else market_type.capitalize()
    action_label = _ACTION_LABELS.get(action_idx, str(action_idx))
    mode_tag = "PAPER" if mode == "paper" else "LIVE"

    msg = (
        f"<b>[Autopilot {mode_tag}] Position prise 📌</b>\n"
        f"\n"
        f"<b>{player_name}</b> — {market_label}\n"
        f"Match : {fixture_name}\n"
        f"Cote : {best_odds:.2f}  |  Edge : {edge:+.1%}\n"
        f"Action : {action_label}  |  Mise : <b>€{stake:.2f}</b>"
        + _scorecard(settled, won, total_pnl, staked_total, fine_tune_runs, odds_api_remaining)
    )
    from app.alerts import send_alert

    await send_alert(msg, channel="recos")


async def notify_autopilot_fine_tune(
    *,
    decisions_used: int,
    td_error_mean: float,
    fine_tune_runs: int,
    settled: int,
    won: int,
    total_pnl: float,
    staked_total: float,
    odds_api_remaining: int | None = None,
) -> None:
    """Notify when a fine-tune pass completes."""
    msg = (
        f"<b>[Autopilot] Fine-tune #{fine_tune_runs} terminé 🧠</b>\n"
        f"\n"
        f"Décisions utilisées : {decisions_used}\n"
        f"TD error moyen : {td_error_mean:+.4f}"
        + _scorecard(settled, won, total_pnl, staked_total, fine_tune_runs, odds_api_remaining)
    )
    from app.alerts import send_alert

    await send_alert(msg, channel="ops")


async def notify_autopilot_settle(
    *,
    batch_won: int,
    batch_lost: int,
    batch_pnl: float,
    total_settled: int,
    total_won: int,
    total_pnl: float,
    staked_total: float,
    fine_tune_runs: int,
    odds_api_remaining: int | None = None,
) -> None:
    """Notify when a settle batch completes."""
    msg = (
        f"<b>[Autopilot] Paris réglés ⚽</b>\n"
        f"\n"
        f"Cette session : {batch_won}W / {batch_lost}L  |  P&amp;L : <b>€{batch_pnl:+.2f}</b>\n"
        f"Cumulé : {total_settled} paris réglés, €{total_pnl:+.2f}"
        + _scorecard(total_settled, total_won, total_pnl, staked_total, fine_tune_runs, odds_api_remaining)
    )
    from app.alerts import send_alert

    await send_alert(msg, channel="ops")
