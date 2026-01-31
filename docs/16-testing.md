# Testing strategy

## Unit tests
- Odds to implied probability conversion
- Overround removal correctness
- Player intensity and anytime scorer probability
- Scenario mixture pricing
- Name to player_id normalization

## Integration tests
- Ingest fixture -> store -> price -> recommend -> log
- End-to-end backtest on a small fixture subset

## Regression tests
- Freeze a small dataset and expected outputs.
- Run in CI to catch drift when parsers or logic changes.

## Data validation tests
- Completeness checks per fixture
- Duplicate detection
- Timestamp ordering and gap detection
