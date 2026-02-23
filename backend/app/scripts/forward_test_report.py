"""Forward test report.

Runs ``simulate_historical()`` on 2025-2026 data, separating real-odds
results from synthetic-odds results.

Usage::

    python -m app.scripts.forward_test_report
"""

import asyncio

from app.backtest.engine import BacktestConfig, BacktestEngine, calculate_brier_score
from app.backtest.simulator import simulate_historical
from app.db import async_session


async def _run_report() -> None:
    print("=" * 60)
    print("  Forward Test Report")
    print("=" * 60)

    async with async_session() as db:
        records = await simulate_historical(db, league=None)

    if not records:
        print("\nNo simulation records found. Run backfill first.")
        return

    # Split by bookmaker
    real_records = [r for r in records if r.get("bookmaker") != "synthetic"]
    synthetic_records = [r for r in records if r.get("bookmaker") == "synthetic"]

    print(f"\nTotal records:     {len(records):>6,}")
    print(f"  Real odds:       {len(real_records):>6,}")
    print(f"  Synthetic odds:  {len(synthetic_records):>6,}")

    engine = BacktestEngine(BacktestConfig(min_edge=0.05))

    # Run on each subset
    for label, subset in [("Forward (real odds)", real_records), ("Backfill (synthetic)", synthetic_records), ("Combined", records)]:
        print(f"\n--- {label} ---")
        if not subset:
            print("  No data")
            continue

        result = engine.run(subset)

        predictions = [r["fair_prob"] for r in subset]
        outcomes = [r["outcome"] for r in subset]
        brier = calculate_brier_score(predictions, outcomes) if predictions else 0.0

        print(f"  Bets placed:  {result.total_bets:>6,}")
        print(f"  Wins:         {result.wins:>6,}")
        print(f"  Losses:       {result.losses:>6,}")
        print(f"  Win rate:     {result.win_rate:>8.1%}")
        print(f"  ROI:          {result.roi:>8.1%}")
        print(f"  Brier score:  {brier:>8.4f}")
        print(f"  Profit:       {result.profit:>8.2f}")

        if result.calibration:
            print("  Calibration:")
            for bucket in result.calibration:
                print(
                    f"    [{bucket['range'][0]:.1f}-{bucket['range'][1]:.1f}] "
                    f"predicted={bucket['predicted_mean']:.3f} "
                    f"actual={bucket['actual_rate']:.3f} "
                    f"n={bucket['count']}"
                )

    print("\n" + "=" * 60)


def main() -> None:
    asyncio.run(_run_report())


if __name__ == "__main__":
    main()
