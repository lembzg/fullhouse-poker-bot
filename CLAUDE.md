# CLAUDE.md — Fullhouse poker bot project

Competition entry for the Fullhouse quantitative poker hackathon (NLHE, 6-max,
London 1–5 Jun 2026). Goal: WIN a ~500-bot field. Submission = `bots/mybot/bot.py`
(single file for now). Work happens on branch `mybot`.

## Checkpoint discipline (READ AND FOLLOW EVERY SESSION)

1. **Checkpoint = ONE inseparable action.** "Commit a phase checkpoint" and
   "update + commit `PROGRESS.md`" are a single unit. Never commit a phase
   checkpoint without updating `PROGRESS.md` in the same commit (or an immediate
   paired commit), and never update `PROGRESS.md` without committing it. One
   without the other is a discipline violation.

2. **`PROGRESS.md` is the living state file.** It MUST always contain, kept
   current:
   - last-updated timestamp;
   - current HEAD short-hash + branch;
   - a one-line **NEXT STEP**;
   - phase status for Phases 0–7, each with its checkpoint commit hash(es);
   - the cadence rule (stop for user OK after each phase; **HARD-STOP before
     Phase 5**);
   - standing decisions;
   - compressed Phase 0 ground truth;
   - known leaks;
   - a how-to-resume line.

3. **Never push to `origin`.** `origin` is the upstream engine repo
   (`uzlez/fullhouse-engine`). All work is local commits on branch `mybot`.
   No `git push`, no PRs, unless the user explicitly says so.

4. **Every session, before doing anything else:** read `PROGRESS.md` and run
   `git log --oneline -10`. Orient from those, then continue at NEXT STEP.

## Quick commands
- Validate submission:  `python3 sandbox/validator.py bots/mybot/bot.py`
- Reproducible benchmark: `python3 tools/harness.py`  (determinism: `--check`)
- Run a match: `python3 sandbox/match.py bots/mybot/bot.py <others...> --hands 400 --seed N`
- Equity unit tests: `python3 tools/test_equity.py` · Latency: `python3 tools/profile_latency.py`
