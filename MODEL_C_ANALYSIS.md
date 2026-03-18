# Model C + Agent Flat — Performance Analysis
> **Written by Guillaume + Claude Code on 2026-03-18. Read this before your next session.**
> There is one live bug (CRITICAL section) that needs fixing before the worker's next recommendation run.

**Period:** 2026-03-03 → 2026-03-16 (paper mode)
**Scope:** All settled goalscorer recommendations, flat €10 stake on every rec

---

## Overall Results

```
settled | wins | losses | voids | win_rate | net_pnl | avg_odds | avg_edge
--------+------+--------+-------+----------+---------+----------+---------
     77 |   12 |     52 |    13 |   18.8%  | -217.30 |     7.05 |   19.8%
```

Model fair probability averages 22.3% but actual win rate is 18.8% → systematic overestimation.

---

## Issue 1 — Duplicate player bets (name encoding bug)

The system generates **two separate recommendations for the same player** when Betclic and Unibet spell the name differently (accents, abbreviations). The unique constraint `(fixture_id, player_name, market_type)` doesn't catch these because the strings differ.

**Confirmed duplicates (same player, same fixture):**

```
home_team        | away_team          | name1              | name2              | result1 | result2 | pnl
-----------------+--------------------+--------------------+--------------------+---------+---------+-----
Lyon             | Paris FC           | Nicolas Tagliafico | Nicolás Tagliafico | lost    | lost    | -20
Atletico Madrid  | Tottenham Hotspur  | Joao Palhinha      | João Palhinha      | lost    | lost    | -20
Atletico Madrid  | Tottenham Hotspur  | Micky Van de Ven   | Micky van de ven   | lost    | lost    | -20
Newcastle United | Barcelona          | Joe Willock        | Joseph Willock     | lost    | lost    | -20
Newcastle United | Barcelona          | Jules Kounde       | Jules Koundé       | void    | void    |   0
```

**Total wasted on duplicates: -80 PnL** (4 double-lost pairs).

**Fix:** Normalize player names before insertion — strip accents (`unidecode`), lowercase, strip punctuation. Apply this normalization to the unique constraint check.

---

## Issue 2 — Model is wrong in the 12–30% probability range

The entire loss of the system comes from midfielders and attacking mids. Bets with `fair_probability` between 12% and 30% have a **0% win rate** across 31 settled bets.

```
prob_bucket                    | bets | wins | actual_win_rate | model_prob |   pnl
-------------------------------+------+------+-----------------+------------+-------
<12%  (defenders/rare scorers) |    8 |    1 |           12.5% |      0.101 |  +35.00
12-20% (midfielders)           |   16 |    0 |            0.0% |      0.150 | -160.00  ← zero wins
20-30% (attacking mid)         |   15 |    0 |            0.0% |      0.243 | -150.00  ← zero wins
30-40% (forwards)              |   14 |    4 |           28.6% |      0.349 |   -4.80
40%+   (top strikers)          |    8 |    5 |           62.5% |      0.449 |  +47.50  ← profitable
```

**High-probability bets (>40%) are profitable (+47.50).** Everything below 30% is a drain.

The npxG-based model (Model C) appears to inflate goalscoring probability for midfielders — likely because `xGChain` and `SOT` metrics for midfielders in high-possession teams look artificially strong, but don't translate to actual goals.

**Fix options:**
- Hard filter: only output recs where `fair_probability >= 0.35`
- Add a position filter: exclude players tagged as defenders/defensive mids
- Recalibrate the `conversion_rate` factor specifically for sub-30% players

---

## CRITICAL (live) — Minutes bug introduced 2026-03-17, corrupts next rec run

A bad ingestion run on 2026-03-17 inserted **143 rows across 63 players** with season-total minutes stored in `minutes_played` but `matches_played=1`. Since the pricing engine uses `expected_minutes = minutes_played / matches_played`, the next recommendation generation will compute 1000–2500 expected minutes per game for these players.

**Impact:** `mins_ratio = expected_minutes / 90` becomes a **15–28× amplifier** on lambda. Every affected player gets priced as near-certainty to score:

```
name                    | position | corrupt_expected_mins | xg_per_90 | inflated_prob
------------------------+----------+-----------------------+-----------+--------------
Pascal Struijk          | DF       |                  2348 |     0.097 |         0.920  (real: ~2%)
Trai Hume               | DF       |                  2489 |     0.059 |         0.806  (real: ~1%)
Ibrahima Konaté         | DF       |                  2404 |     0.039 |         ~0.65  (real: ~1%)
Jhon Arias              | FW       |                  1117 |     0.272 |         0.966  (real: ~27%)
Yoane Wissa             | FW       |                   421 |     0.417 |         0.858  (real: ~35%)
```

Valid data for all 63 players exists at `as_of_utc = 2026-03-16` (25–30 matches each). The `latest_subq` in `get_recommendations_for_date` picks only the newest `as_of_utc` per `(player_id, league)` — so the March 17 corrupt rows completely shadow the valid March 16 data.

**Fix (immediate):** Delete the corrupt rows before the next worker run:
```sql
DELETE FROM player_stats
WHERE as_of_utc::date = '2026-03-17'
  AND matches_played = 1
  AND minutes_played > 120;
-- 143 rows
```

**Fix (root cause):** Add a sanity check in the ingestion script: `IF minutes_played / matches_played > 120 → reject row`. Also add a DB constraint or trigger.

---

## Issue 3 — Root cause: CALIBRATION_SCALE removed without recalibration

**File:** `backend/app/services/recommendation_service.py`, line 30

```python
CALIBRATION_SCALE = 1.0  # Top-down model; old 0.62 was calibrated for bottom-up xg_per_90
```

The old bottom-up model used `CALIBRATION_SCALE = 0.62` to deflate probabilities before outputting recommendations. When the model switched to top-down (fixture_strength-based), the calibration factor was reset to 1.0 — but **no new calibration was done**. This inflates every output probability by a factor of `1/0.62 ≈ 1.6x`.

**Impact:** A midfielder with `xg_per_90=0.12`, 75 min, `fixture_strength=1.5`:
- Current model: `λ=0.15 → P=13.9%`
- With old calibration scale: `13.9% × 0.62 = 8.6%` → would get odds ~11.6, market would offer ~9-11, barely any edge → **filtered out**
- At 13.9%, edge against market odds of 9-11 looks positive → **incorrectly flagged as VALUE**

This explains exactly why the 12-30% fair_prob bucket (midfielders, attacking mids) has 0 wins — the model is over-calling edge in a zone where there isn't any.

**Fix:** Re-derive CALIBRATION_SCALE empirically from settled data. Using the current sample: actual win rate = 18.8%, model avg probability = 22.3% → calibration factor = 18.8/22.3 ≈ **0.84**. Note the real fix should bucket by position/probability range, as high-prob recs (>40%) appear well-calibrated already.

---

## Issue 4 — Corrupted player_stats data in DB

The `player_stats` table has severe data issues for several players:

```sql
-- Dan Burn (Newcastle CB): npxg_per_90 = 54.7 (should be ~0.007)
Dan Burn | D S | Newcastle | xg_per_90=0.007 | npxg_per_90=54.7 | matches=3

-- Micky van de Ven (Tottenham CB): npxg_per_90 = 86.6 (should be ~0.093)
Micky van de Ven | DF | Tottenham | xg_per_90=0.093 | npxg_per_90=86.6 | matches=5
```

`npxg_per_90` values of 54+ are physically impossible (max achievable in 90 min is ~3-4 xG). This appears to be a unit conversion bug in ingestion — likely cumulative season total stored where per-90 rate is expected. The pricing code uses `xg_per_90` for lambda (not `npxg_per_90`), so this doesn't directly affect pricing, but it corrupts the `npxg_total` threshold check at line 158.

Additionally, each player has **10-15 duplicate rows** in `player_stats` (e.g., Micky van de Ven: 15 rows, Palhinha: 11 rows, Maitland-Niles: 11 rows). The query picks the row with `max(as_of_utc)` per `(player_id, league)`, then the code at line 647 keeps the row with the most `matches_played`. If early-season stats (high xg_per_90 from a hot spell) were ingested at a later timestamp than full-season stats, the wrong row gets used.

Example: Palhinha has a 7-match row with `xg_per_90=0.228` alongside a 26-match row with `xg_per_90=0.07`. If the 7-match row has a newer `as_of_utc`, the dedup (line 647) still picks 26 matches — BUT if both rows share the same `as_of_utc`, both pass the join and the dict overwrites are non-deterministic based on query order.

**Fix:** Add a unique constraint on `(player_id, league, as_of_utc)` in player_stats, and clean the duplicates. Also add a sanity cap: `npxg_per_90 > 3.0 → NULL`.

---

## Issue 5 — Edge metric has no predictive value

Higher edge does not mean higher win rate. The 20–30% edge bucket is the worst performer.

```
edge_bucket | bets | wins | win_rate |    pnl
------------+------+------+----------+--------
0-10%       |   11 |    2 |    18.2% |  -47.80
10-20%      |   16 |    4 |    25.0% |  -58.00
20-30%      |   17 |    1 |     5.9% | -144.50  ← worst
30-40%      |   13 |    2 |    15.4% |  +22.00
40%+        |    4 |    1 |    25.0% |   -4.00
```

No monotonic relationship — the edge calculation currently reflects model overconfidence, not real market inefficiency. Until the probability calibration is fixed (Issue 2), edge is not a reliable filter.

---

## Issue 6 — High bet concentration per fixture

The model generates 6–9 recommendations per fixture, meaning a single match can represent €60–90 of exposure (agent flat). When UCL fixtures go scoreless for our players, it's a multi-bet wipeout.

**Worst fixtures (Mar 9–12 week):**

```
home_team        | away_team          | league           | recs | wins | pnl
-----------------+--------------------+------------------+------+------+------
Newcastle United | Barcelona          | champions_league |    9 |    1 | -52
Lyon             | Paris FC           | ligue_1          |    7 |    1 | -40
Atletico Madrid  | Tottenham Hotspur  | champions_league |    6 |    0 | -60
Auxerre          | Strasbourg         | ligue_1          |    7 |    0 | -70
```

The Mar 9 week alone: **-249 PnL** driven almost entirely by UCL fixtures with many low-probability recs.

**Fix:** Cap recommendations per fixture (e.g. top 3 by edge/fair_prob) or cap per-fixture flat exposure.

---

## Weekly PnL Trend (Model C, goalscorer only)

```
week        | bets | wins |    pnl
------------+------+------+--------
2026-03-02  |   23 |    6 |  +17.00   ← good start
2026-03-09  |   38 |    4 | -249.30   ← UCL blowout week
2026-03-16  |    0 |    0 |    0.00   (settling in progress)
```

The positive first week was driven by higher-probability picks. The model degraded when UCL fixtures introduced a large volume of mid-probability defenders/midfielders.

---

## Summary — Priority Fixes

| Priority | Issue | File | PnL Impact |
|----------|-------|------|------------|
| **CRITICAL** | Delete 143 corrupt player_stats rows from 2026-03-17 | DB: `DELETE … WHERE as_of_utc::date='2026-03-17' AND matches_played=1 AND minutes_played>120` | Next rec run will price 63 players at 80–97% probability |
| P0 | Add ingestion sanity check: reject rows where `minutes/matches > 120` | ingestion scripts | Prevents recurrence |
| P0 | Restore `CALIBRATION_SCALE ≈ 0.84` | `recommendation_service.py:30` | -310 (entire 12-30% bucket) |
| P0 | Clean duplicate player_stats + add unique constraint on `(player_id, league, as_of_utc)` | `player_stats` table | Unpredictable pricing |
| P1 | Normalize player names before DB insert (strip accents) | `worker.py:821` dedup | -80 confirmed |
| P2 | Filter out `fair_probability < 0.35` as interim guard | `selector.py` or `worker.py` | -310 from 12–30% band |
| P2 | Cap recs per fixture (max 3 by fair_prob) | `selector.py:apply_exposure_limits` | Reduces UCL blowouts |
| P3 | Fix sanity cap on `npxg_per_90 > 3.0` during ingestion | ingestion scripts | Data integrity |
