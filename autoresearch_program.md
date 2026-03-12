# Ev0 Agent Autoresearch

## Setup
1. Create branch: `git checkout -b autoresearch/<tag>` from main
2. Read: `features.py`, `agent.py`, `results.tsv`
3. Initialize `results.tsv` with header if empty
4. Run baseline: `python -m app.autopilot.autoresearch > run.log 2>&1`
5. Log baseline to `results.tsv`

## The Loop (NEVER STOP)

LOOP FOREVER:
1. Read `features.py` + `agent.py` + `results.tsv`
2. Propose ONE code change (new feature, remove feature, architecture tweak)
3. `git commit -m "experiment: <description>"`
4. Run: `cd backend && python -m app.autopilot.autoresearch > run.log 2>&1`
5. Extract: `grep "^best_log_wealth:" run.log`
6. If empty -> crash. Run `tail -n 50 run.log`, fix, retry.
7. Log to `results.tsv`
8. If `log_wealth` improved -> KEEP (advance branch)
9. If equal or worse -> `git reset --hard HEAD~1` (discard)
10. REPEAT

## What you CAN modify
- `backend/app/autopilot/features.py` - feature engineering
- `backend/app/autopilot/agent.py` - agent architecture/logic

## What you CANNOT modify
- Everything else. The pricing engine, backtest, evaluation are fixed.

## Simplicity criterion
- <0.5% improvement + added complexity = DISCARD
- Equal result + simpler code = KEEP
- Removing code + equal/better result = DEFINITELY KEEP

## results.tsv format
```
commit	log_wealth	sharpe	dsr	n_features	status	description
```
