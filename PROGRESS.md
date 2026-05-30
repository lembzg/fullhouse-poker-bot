# PROGRESS.md — living state (update + commit WITH every phase checkpoint)

- **Last updated:** 2026-05-30 13:42 BST
- **HEAD:** `4ed130c`  ·  **branch:** `mybot`
- **NEXT STEP:** Start **Phase 3** — offline `tools/` generator writing precomputed
  preflop charts (positional open/call/3-bet/4-bet ranges + Nash push/fold);
  `decide()` does O(1) lookup keyed by (169-hand class, position, facing-action,
  effective-stack bucket). Replaces the noisy preflop Monte Carlo.
- **How to resume:** `git checkout mybot` → read this file + `git log --oneline -10`
  → `python3 tools/harness.py` for the current baseline → continue at NEXT STEP.

## Cadence rule
Stop for the user's OK after **every** phase (the user reviews every line).
**HARD-STOP before Phase 5** (opponent modelling / exploitation — that's the edge;
needs explicit sign-off before building). Do not roll into the next phase
unprompted. Each phase ends on a runnable, validator-passing checkpoint.

## Phase status (0–7)
| Phase | Status | Checkpoint commit(s) |
|---|---|---|
| 0 — Recon / ground truth | ✅ DONE | (captured in this file + memory) |
| 1 — Safe skeleton | ✅ DONE | `529c568` |
| 2 — Equity core (MC, eval7) | ✅ DONE | `2eb1b55`; tooling `1a9e319`; repro harness `4ed130c` |
| 3 — Preflop as precomputed data | ⏳ NEXT | — |
| 4 — Postflop (texture, SPR, pot control) | ◻ TODO | — |
| 5 — Opponent modelling + exploitation | ◻ TODO (HARD-STOP before) | — |
| 6 — Test harness + tuning + regression gate | ◻ TODO | — |
| 7 — Package + validate + BOT_README | ◻ TODO | — |

## Standing decisions
- **Packaging:** single-file `bots/mybot/bot.py` for now; switch to `.zip` + `data/`
  only if Phase 3 tables exceed what's sane to inline (169-grid is tiny → likely
  stays inline). Decided in Phase 0.
- **Never push to origin** (upstream is `uzlez/fullhouse-engine`); local commits on
  `mybot` only.
- **Benchmark vs the 5 DISTINCT reference bots, never a self-clone** (two strong
  bots cannibalise one table and inflate apparent variance).
- **Reproducible tests** via `BOT_RNG_SEED` (env-gated, test-only) + seeded ref
  fixtures in `tools/refbots_seeded/`. Production leaves the var unset → stochastic.
- **Optimise MEAN chip delta**, accept variance. Do NOT blanket-tighten —
  measured −EV vs this bluff-heavy field (Phase 2 experiment).
- **Latency is sacred:** fallback computed first; MC polls a `time.monotonic`
  budget (1.2 s) and bails; never risk the 2 s hard limit.
- **3.10-compatible syntax only**; no forbidden imports.

## Phase 0 ground truth (compressed — engine source is canonical)
- **Format:** 6-max tables, **400 hands/match**, Swiss, rank by **cumulative chip
  delta**, top 64 advance. Engine supports 2–9; tables shrink to **heads-up** as
  bots bust (HU rules: button = SB, acts first preflop). Bot must handle 2–9.
- **Stakes:** 10,000 start (100 bb), blinds 50/100 **fixed (no escalation)**.
  SNG-style: stacks carry across hands, busted (stack 0) bots removed.
- **Opponent id = persistent `bot_id`**; `seat` is NOT stable (remaps on bustouts)
  → always resolve identity via `players[seat]["bot_id"]`. `action_log` is
  seat-keyed (join to `players`). Engine also injects `state["match_action_log"]`
  (last 200 cross-hand actions, each with `bot_id`).
- **Process model:** bot is a long-lived subprocess; **module globals persist the
  whole match**, reset next match (per-match opponent stats reset for free).
  Warmup `{"type":"warmup"}` sent before hand 1 with a 30 s budget (heavy
  import-time loads OK).
- **Timing:** 2 s hard limit via a daemon thread that **cannot preempt** (overrun
  burns CPU into next decision) → self-police with `time.monotonic` budget.
- **Actions:** raise `amount` = **TOTAL bet-to**; `min_raise_to = current_bet +
  min_raise`. Engine is forgiving (illegal→fold, sub-min→snapped) but we emit only
  clean legal actions; every raise clamped to `[min_raise_to, all-in]`.
- **Limits:** RAM **768 MB**; `data/` ≤ 200 MB; package ≤ 250 MB; `bot.py` ≤ 5 MB.
- **Runtime:** **production = Python 3.10** (eval7 needs `longintrepr.h`, gone in
  3.11+); local is 3.13. Forbidden imports (hard validator fail): `threading`,
  `importlib`, `pickle`, `ctypes`, `asyncio`, `subprocess`, `socket`/`urllib`/…
  Load tables via numpy/json, never pickle. `os` allowed (no dangerous `os.*`).
- **Allowed libs:** eval7, numpy, scipy, treys, scikit-learn.
- **Validator** = pure safety gate (any LEGAL action passes; no strategy check).
- **Reference field leaks:** template (raises only AA/KK, folds a lot); aggressor
  (70 % raise, spews — trap it, don't bluff it); mathematician + ref_bot_2 (never
  raise, call only ≥ 3:1 pot odds); shark (tight TAG, folds postflop to > 25 %-pot
  bets). Field over-folds / under-raises → exploitation thesis holds.

## Known leaks (to fix in later phases)
- **One-pair over-commitment vs escalating aggression** — stacked off 100 bb with
  2nd pair (K7o) vs a re-raising opponent who had top pair. Raw equity-vs-random
  overvalues one pair facing raise-over-raise. **Fix in Phase 4** (made-hand class
  + SPR pot control), NOT blanket call-bar tightening (measured −EV).
- **vs-random equity is optimistic** against value-heavy ranges → over-calls vs
  tight opponents. Fix via range modelling (Phase 4) + per-opponent reads (Phase 5).
- **Preflop currently uses noisy MC** → Phase 3 replaces with O(1) chart lookup.

## Current performance (baseline to beat)
vs reproducible 5-bot field, 30 seeds: mean **+13.7k**, 27/30 table wins, 1 bust.
vs live (stochastic) field: mean +11.8k, 24/30 wins, 4 busts. Validator: PASSED.
Worst-case `decide()` ~4 ms full speed (~8 ms @ 0.5 core); budget guard caps at 1.2 s.
