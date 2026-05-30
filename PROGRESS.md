# PROGRESS.md — living state (update + commit WITH every phase checkpoint)

- **Last updated:** 2026-05-30 22:40 BST
- **HEAD:** `7ca9622` (Phase 4b checkpoint)  ·  **branch:** `mybot`
- **NEXT STEP:** **HARD-STOP — Phase 5 needs explicit user sign-off.** Phase 4 is
  DONE (4a guard + 4b texture/sizing/bluff-catch). Phase 5 = opponent modelling +
  exploitation; the deferred 4b bluff initiative (c-bet / barrel / semibluff /
  check-raise) folds into it because its EV is opponent-conditional.
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
| 3 — Preflop as precomputed charts | ✅ DONE | `44b72c8` (docs `370bc33`) |
| 4 — Postflop (texture, SPR, pot control) | ✅ DONE | 4a `7556ee5` · 4b `7ca9622` |
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
- ~~**One-pair over-commitment vs escalating aggression**~~ — stacked off 100 bb
  with 2nd pair (K7o) vs a re-raising opponent who had top pair. **GUARDED in
  Phase 4a**: `_postflop_overcommit_guard` is a *strictly subtractive* SPR cap —
  a lone weak pair (MARGINAL class) facing a bet folds the stack-off when deep
  (SPR>1.5, call ≥60 % of stack, eq<0.85) and never value-raises into aggression.
  Can only downgrade raise→call / call-off→fold, so it provably can't bust more
  than baseline (verified: 0 new busts in the 30-seed A/B). NOT blanket call-bar
  tightening (measured −EV). Full postflop sizing/pot-growth control → Phase 4b.
- **vs-random equity is optimistic** against value-heavy ranges → over-calls vs
  tight opponents. Phase 4b adds texture+SPR-priced bluff-catching (folds marginal
  more on wet, calls wider when committed); full fix needs per-opponent reads
  (Phase 5).
- **Unconditional postflop bluffing is −EV in this format** (Phase 4b finding) —
  c-bets / barrels / semibluffs add variance, and variance is −EV in a bust-
  elimination format (busts cost a fixed −10k floor). Their EV is opponent-
  conditional → only profitable once we can c-bet the folders and never the
  aggressor. **Deferred to Phase 5.**
- ~~Preflop noisy MC~~ → **RESOLVED Phase 3**: O(1) chart lookup (position ×
  facing × stack bucket); push/fold gated at eff ≤ 15bb (100bb never shoves);
  HU ranges for the endgame. Uncovered spots still fall back to MC.

## Phase 3 notes (preflop charts)
- Charts are compact shorthand in `bot.py`, expanded to frozensets at import
  (`_build_charts`). `tools/gen_preflop.py` writes the same tables to
  `bots/mybot/data/preflop.json` (json artifact for the optional zip); bot.py
  prefers data/ json if shipped, else identical inline build → no drift.
- Position derived from posted blinds in `action_log`; facing from raise count;
  effective bb = min(my total, biggest live opp total)/100.
- Single-file submission unaffected (inline charts; data/ optional).

## Phase 4a notes (made-hand class + SPR over-commitment guard)
- New postflop helpers in `bot.py`: `_spr` (effective stack-to-pot from remaining
  stacks), `_pair_class` (overpair/TPGK → MEDIUM; 2nd pair / weak-kicker TP /
  under-pair → MARGINAL; ambiguity → MARGINAL = safe), `_made_hand_class` (O(1)
  via `eval7.handtype`: two pair+ → STRONG, one pair → MEDIUM/MARGINAL, high card
  → WEAK; returns None → defer to pure equity), `_postflop_overcommit_guard`.
- **Design lesson:** the first attempt was a full postflop *rewrite* (class-sized
  value bets + MEDIUM/STRONG value-raises). It was *additive* (raised more) and
  created 3 NEW busts (−4.8k mean, busts 5→8). Rebuilt as a *strictly subtractive*
  guard that can only reduce commitment → provably can't regress. Rule for Phase
  4b: a leak fix must never add a bet/raise the baseline wouldn't make.
- Constants: `_SPR_COMMIT=1.5`, `_STACKOFF_FRAC=0.6`, `_STACKOFF_EQ=0.85`
  (principled defaults; grid-tune in Phase 6, per standing decision).
- Guard fires rarely vs the 5 over-bluffing refs (1/30 seeds) — folding one pair
  ≈ break-even against bluff-heavy opponents. It is *insurance* against the
  catastrophic deep stack-off that matters vs the stronger 500-bot field, not an
  EV boost vs these refs.

## Phase 4b notes (board texture + polarized sizing + texture-priced bluff-catch)
- New in `bot.py`: `_board_texture` (wet/dry: live flush or straighty), and
  `_postflop_strategy` — class+texture VALUE sizing (small dry / large wet),
  marginal made hands take thin value on dry only & pot-control on wet, and
  bluff-catch priced by texture (+0.05 eq bar on wet) + SPR (−0.03 when committed).
  Runs AFTER the 4a guard (structurally preserved — guard handles the stack-offs
  first; verified it still fires first).
- **What we built then DROPPED (and why):** a full bluff layer (c-bet bluffs,
  turn barrels, semibluff bets, semibluff check-raise + draw/lead/all-in helpers).
  200-seed paired A/B vs 4a: **mean −1,516, 95% CI [−2,893, −140], p=0.032 — a
  real (if small) regression**, busts 33→42/200. Variance is −EV here (bust floor
  −10k). The 30-seed point estimate (−3,955) was noise-inflated; the larger run
  both shrank and sharpened it. Bluff EV is opponent-conditional → deferred to P5.
- **What we KEPT (committed):** trimmed 4b = value sizing + texture/SPR bluff-catch
  only. 200-seed paired A/B vs 4a: **mean −607, 95% CI [−1,789, +576], p=0.32 —
  statistically NEUTRAL**, busts 35 vs 33/200. Clears the agreed bar (CI must not
  exclude zero on the downside). Committed for the structural refinement + as the
  texture/class scaffolding Phase 5 will gate by opponent.
- **Methodology note:** postflop changes cascade (one different flop bet reshuffles
  all 400 hands), so 30 seeds is too noisy to judge them — use ≥200-seed PAIRED
  A/B (common random numbers) + a t-test, not point estimates.

## Current performance (baseline to beat)
Deterministic PAIRED A/B (harness.py: seeded refs + `BOT_RNG_SEED`, common random
numbers):
- **Phase 4b: statistically neutral vs 4a** (200-seed paired diff −607,
  CI [−1,789, +576], p=0.32; busts 35 vs 33/200). Mean level ≈ +15.2k.
- Phase 4a: mean +16,804 (30-seed) / +15,868 (200-seed), ~24/30 wins, 5 busts;
  paired +50 vs Phase 3, 0 new busts.
- Phase 3 charts: mean +16,754 (30-seed). Phase 2: +11.5k.
Validator: PASSED. Equity: 5/5. Worst-case `decide()` ~4 ms (~8 ms @ 0.5 core);
budget caps 1.2 s. Determinism: `harness.py --check` = YES.
