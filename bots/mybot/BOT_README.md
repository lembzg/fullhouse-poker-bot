# Quant — Fullhouse NLHE entry

**Submission artifact:** `bot.py` (single self-contained file, ~57 KB). No `data/`
or zip required — preflop tables are inlined and built at import.

## Approach
Quant is a 6-max No-Limit Hold'em bot (handles 2–9 as tables shrink to heads-up)
built in layers on a Monte-Carlo equity core:

- **Equity core** — showdown equity by Monte-Carlo with `eval7`, vs the live
  opponent count, deck-removal-correct, polling a wall-clock budget.
- **Preflop** — O(1) lookup in precomputed, position × facing × stack-bucket
  charts (Nash-ish push/fold short, sized open/call/3-bet/4-bet deep, heads-up
  ranges for the endgame). Uncovered spots fall back to the equity core.
- **Postflop made-hand class + SPR pot control** — a *subtractive* guard that
  stops one-pair over-commitment when deep (never stacks off a lone weak pair at
  high SPR), without widening folds to a single bet.
- **Board texture + polarized sizing + bluff-catch** — value sizing small on dry
  / larger on wet boards; bluff-catch priced by texture and stack-to-pot.
- **Opponent modelling + gated exploitation** — per-opponent VPIP / PFR /
  aggression / fold-to-bet, keyed by stable `bot_id` from the cross-hand action
  log, shrunk toward a population prior. Drives conditional deviations (value
  thinner/bigger vs stations, wider bluff-catch vs maniacs, overfold vs nits) and
  re-enables a bluff layer **only** when every live opponent is a folder and none
  is a station/maniac — "c-bet the folders, never the aggressor."

## Safety
Fallback-first: a guaranteed-legal pot-odds action is computed before any MC;
the MC polls a `time.monotonic` budget (1.2 s) and bails to that fallback; every
raise is clamped to `[min_raise_to, all-in]`; warmup and malformed states are
handled defensively; the opponent-model update is best-effort (failure → play
baseline). No forbidden imports; `eval7` import is guarded.

## Dependencies & runtime
- **Third-party:** `eval7` only (plus the Python standard library).
- **Runtime:** Python **3.10** (verified: eval7 compiles/imports, isolated
  validator passes, a full 400-hand match runs with zero bot errors on 3.10.20).
- **Footprint:** single file ≪ 5 MB; RAM well under 768 MB; worst-case
  `decide()` ≈ 4–9 ms at 0.5 core (budget guard 1.2 s, hard limit 2 s).
