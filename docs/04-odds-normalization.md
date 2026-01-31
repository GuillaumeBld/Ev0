# Odds normalization and margin removal

## Why
Raw bookmaker odds include margin (vig). Comparing fair odds to raw odds inflates false edges.

## Steps
1) Convert odds to implied probabilities:
- p = 1 / odds

2) Remove overround within a market group:
- Simple normalization:
  - p_clean = p / sum(p_all_selections)
- Alternative methods:
  - Shin model
  - Power method

3) Convert cleaned probabilities back to fair market odds:
- odds_clean = 1 / p_clean

## Market mapping
- Normalize player names to canonical IDs.
- Handle duplicates and transfers.
- Store book name, market rules, and timestamps.

## Edge definition
Use probabilities:
- edge = (p_model - p_market_clean) / p_market_clean

Or odds ratio:
- edge = (odds_market_clean / EV0) - 1

## Minimum thresholds
Start conservative, adjust after calibration:
- Anytime scorer: 6 to 10 percent
- Assist: 10 to 15 percent
