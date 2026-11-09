from app.scripts.transfermarkt_career import aggregate_by_season_and_competition


def _game(**stats):
    base = {
        "gameInformation": {"seasonId": 2015, "competitionId": "FR1",
                             "season": {"display": "15/16"}},
        "statistics": {"generalStatistics": {"participationState": "played"}},
    }
    base["statistics"].update(stats)
    return base


def test_aggregation_sums_all_families():
    games = [
        _game(
            goalStatistics={"goalsScoredTotal": 1, "assists": 0,
                            "scoringAttempts": 4, "scoringAttemptsOnGoal": 2},
            distributionStatistics={"passes": 40, "passesReached": 34},
            duelStatistics={"tackles": 2, "tacklesWon": 1,
                            "foulsCommitted": 1, "foulsGained": 3, "offsides": 1},
            cardStatistics={"yellowCardNet": 1},
            playingTimeStatistics={"playedMinutes": 90, "isStarting": True},
        ),
        _game(
            goalStatistics={"goalsScoredTotal": 0, "assists": 1,
                            "scoringAttempts": 2, "scoringAttemptsOnGoal": 1},
            distributionStatistics={"passes": 30, "passesReached": 25},
            duelStatistics={"tackles": 0, "tacklesWon": 0,
                            "foulsCommitted": 2, "foulsGained": 1, "offsides": 0},
            cardStatistics={"yellowCardNet": 0},
            playingTimeStatistics={"playedMinutes": 70, "isStarting": False},
        ),
    ]
    out = aggregate_by_season_and_competition(client=None, games=games, cache={"competition_names": {}})
    assert len(out) == 1
    s = out[0]
    assert s["appearances"] == 2 and s["minutes"] == 160
    assert s["starts"] == 1
    assert s["total_shots"] == 6 and s["shots_on_target"] == 3
    assert s["total_pass"] == 70 and s["accurate_pass"] == 59
    assert s["total_tackle"] == 2 and s["won_tackle"] == 1
    assert s["fouls_committed"] == 3 and s["fouls_suffered"] == 4 and s["offsides"] == 1
    assert s["yellow_card"] == 1 and s["red_card"] == 0
