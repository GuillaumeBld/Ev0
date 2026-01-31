# Product requirements document

## Problem
Prematch player prop markets have margin and noise. Manual evaluation is slow and inconsistent.

## Solution
A system that prices and explains anytime scorer and assist probabilities using xG and xA inputs, then compares to normalized market probabilities to identify mispricing.

## Success metrics
- Positive mean CLV over a 4 to 8 week paper trading period
- Stable calibration by league and edge bucket
- Low operational error rate in mapping and ingestion

## User stories
- As an operator, I can see recommendations with explanations and data freshness.
- As an operator, I can review backtest evidence and adjust thresholds.
- As an operator, I can log accept or reject decisions and track outcomes.

## Constraints
- Respect data source terms.
- Avoid live execution until paper trading gates are met.
