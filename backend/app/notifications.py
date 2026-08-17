"""Telegram notification helpers."""
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

CHANNELS = ("value", "incidents", "autopilot")

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


def _chat_id_for(channel: str) -> str:
    return {
        "value": settings.telegram_chat_id_value,
        "incidents": settings.telegram_chat_id_incidents,
        "autopilot": settings.telegram_chat_id_autopilot,
    }[channel]


async def _post(token: str, chat_id: str, text: str) -> bool:
    """Un envoi Telegram. Ne lève jamais ; retourne True si accepté."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
            r.raise_for_status()
            return True
    except Exception as exc:
        logger.warning("Telegram sendMessage échoué (chat_id=%s): %s", chat_id, exc)
        return False


async def send_telegram_alert(message: str, channel: str) -> bool:
    """Envoie sur le groupe du canal. Repli sur le chat historique, jamais en silence."""
    if channel not in CHANNELS:
        raise ValueError(f"canal Telegram inconnu: {channel!r}")

    token = getattr(settings, "telegram_bot_token", None)
    if not token:
        logger.debug("TELEGRAM_BOT_TOKEN absent — notification ignorée")
        return False

    chat_id = _chat_id_for(channel)
    if chat_id:
        if await _post(token, chat_id, message):
            return True
        logger.warning(
            "Canal '%s' injoignable (chat_id=%s) — repli sur le chat historique",
            channel, chat_id,
        )
    else:
        logger.warning(
            "Canal '%s' non configuré (TELEGRAM_CHAT_ID_%s vide) — repli sur le chat historique",
            channel, channel.upper(),
        )

    fallback = settings.telegram_chat_id
    if not fallback:
        logger.error("Aucun chat de repli configuré — alerte '%s' PERDUE", channel)
        return False
    return await _post(token, fallback, f"[{channel}] {message}")


_BET_LABELS = {"goal": "Buteur", "assist": "Passeur"}


def _format_movement(m: dict) -> str:
    label = _BET_LABELS.get(m["bet_type"], m["bet_type"])
    head = "▲ NOUVELLE VALUE\n" if m["kind"] == "new" else "▲ "
    name_line = f"<b>{m['player']}</b> — {label}"
    fixture_line = f"{m['fixture']} ({m['kickoff']})"

    if m["kind"] == "new":
        odds_line = f"{m['odds']:.2f} {m['bookmaker']} | edge {m['edge']:+.1%}"
    else:
        odds_line = (
            f"{m['previous_odds']:.2f} → {m['odds']:.2f} {m['bookmaker']} | "
            f"edge {m['previous_edge']:+.1%} → {m['edge']:+.1%}"
        )

    return f"{head}{name_line}\n{fixture_line}\n{odds_line}"


async def notify_value_movements(movements: list[dict]) -> None:
    """Un seul message par cycle de scraping. Rien si le cycle est vide.

    Le scraping reevalue toutes les recos toutes les 60 s : notifier mouvement
    par mouvement recreerait le flood qu'on cherche a supprimer.
    """
    if not movements:
        return

    n = len(movements)
    header = f"🎯 <b>{n} mouvement{'s' if n > 1 else ''}</b>"
    body = "\n\n".join(_format_movement(m) for m in movements)

    from app.alerts import send_alert

    await send_alert(f"{header}\n\n{body}", channel="value")


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


async def notify_autopilot_run(
    *,
    bets: list[dict],
    mode: str,
    settled: int,
    won: int,
    total_pnl: float,
    staked_total: float,
    fine_tune_runs: int,
    odds_api_remaining: int | None = None,
) -> None:
    """Un message par run d'autopilot, scorecard affiche une seule fois.

    Un run a 12 paris produisait 12 notifications repetant le meme scorecard.
    """
    if not bets:
        return

    mode_tag = "PAPER" if mode == "paper" else "LIVE"
    n = len(bets)
    plural = "s" if n > 1 else ""
    lines = [
        f"<b>[Autopilot {mode_tag}] {n} position{plural} prise{plural} 📌</b>",
        "",
    ]
    for bet in bets:
        market_label = (
            "Buteur" if bet["market_type"] == "goalscorer" else bet["market_type"].capitalize()
        )
        action_label = _ACTION_LABELS.get(bet["action_idx"], str(bet["action_idx"]))
        lines.append(f"• <b>{bet['player_name']}</b> — {market_label}")
        lines.append(f"  {bet['fixture_name']}")
        lines.append(
            f"  {bet['best_odds']:.2f} | edge {bet['edge']:+.1%} | "
            f"{action_label} → <b>€{bet['stake']:.2f}</b>"
        )

    msg = "\n".join(lines) + _scorecard(
        settled, won, total_pnl, staked_total, fine_tune_runs, odds_api_remaining
    )

    from app.alerts import send_alert

    await send_alert(msg, channel="autopilot")


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

    await send_alert(msg, channel="autopilot")


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

    await send_alert(msg, channel="autopilot")
