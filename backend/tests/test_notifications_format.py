"""Mise en forme des messages groupes (value + autopilot)."""
from app import notifications


async def test_no_movements_sends_nothing(monkeypatch):
    sent: list = []

    async def fake_send(message, channel):
        sent.append((message, channel))
        return True

    monkeypatch.setattr("app.alerts.send_alert", fake_send)
    await notifications.notify_value_movements([])
    assert sent == []


async def test_movements_are_grouped_into_one_message(monkeypatch):
    sent: list = []

    async def fake_send(message, channel):
        sent.append((message, channel))
        return True

    monkeypatch.setattr("app.alerts.send_alert", fake_send)

    await notifications.notify_value_movements([
        {
            "kind": "new", "player": "Dembélé", "fixture": "PSG vs Marseille",
            "kickoff": "21:00", "bet_type": "goal", "odds": 2.85,
            "previous_odds": None, "bookmaker": "Betclic",
            "edge": 0.124, "previous_edge": None,
        },
        {
            "kind": "rise", "player": "Mbappé", "fixture": "Real vs Girona",
            "kickoff": "21:00", "bet_type": "goal", "odds": 3.40,
            "previous_odds": 3.10, "bookmaker": "Unibet",
            "edge": 0.142, "previous_edge": 0.081,
        },
    ])

    assert len(sent) == 1, "un seul message par cycle, pas un par mouvement"
    message, channel = sent[0]
    assert channel == "value"
    assert "2 mouvements" in message
    assert "NOUVELLE VALUE" in message
    assert "Dembélé" in message and "Buteur" in message
    assert "3.10 → 3.40" in message
    assert "+8.1% → +14.2%" in message


async def test_single_movement_is_singular(monkeypatch):
    sent: list = []

    async def fake_send(message, channel):
        sent.append(message)
        return True

    monkeypatch.setattr("app.alerts.send_alert", fake_send)
    await notifications.notify_value_movements([{
        "kind": "new", "player": "Saka", "fixture": "Arsenal vs Chelsea",
        "kickoff": "18:30", "bet_type": "assist", "odds": 4.20,
        "previous_odds": None, "bookmaker": "Betclic",
        "edge": 0.06, "previous_edge": None,
    }])
    assert "1 mouvement" in sent[0]
    assert "2 mouvements" not in sent[0]
    assert "Passeur" in sent[0]


# ── Positions autopilot groupees par run ──────────────────────────

_BETS = [
    {"player_name": "Dembélé", "fixture_name": "PSG vs OM", "market_type": "goalscorer",
     "best_odds": 2.85, "edge": 0.124, "stake": 12.5, "action_idx": 1},
    {"player_name": "Mbappé", "fixture_name": "Real vs Girona", "market_type": "goalscorer",
     "best_odds": 3.40, "edge": 0.142, "stake": 25.0, "action_idx": 2},
]

_SCORE = {
    "settled": 120,
    "won": 35,
    "total_pnl": 42.0,
    "staked_total": 400.0,
    "fine_tune_runs": 2,
}


async def test_autopilot_run_sends_one_message_with_one_scorecard(monkeypatch):
    sent: list = []

    async def fake_send(message, channel):
        sent.append((message, channel))
        return True

    monkeypatch.setattr("app.alerts.send_alert", fake_send)
    await notifications.notify_autopilot_run(bets=_BETS, mode="paper", **_SCORE)

    assert len(sent) == 1
    message, channel = sent[0]
    assert channel == "autopilot"
    assert "2 positions" in message
    assert "Dembélé" in message and "Mbappé" in message
    assert message.count("Scorecard live") == 1, "scorecard affiché une seule fois"


async def test_autopilot_run_without_bets_sends_nothing(monkeypatch):
    sent: list = []

    async def fake_send(message, channel):
        sent.append(message)
        return True

    monkeypatch.setattr("app.alerts.send_alert", fake_send)
    await notifications.notify_autopilot_run(bets=[], mode="paper", **_SCORE)
    assert sent == []


async def test_autopilot_run_singular_and_mode_tag(monkeypatch):
    sent: list = []

    async def fake_send(message, channel):
        sent.append(message)
        return True

    monkeypatch.setattr("app.alerts.send_alert", fake_send)
    await notifications.notify_autopilot_run(bets=_BETS[:1], mode="live", **_SCORE)
    assert "1 position" in sent[0]
    assert "2 positions" not in sent[0]
    assert "LIVE" in sent[0]
