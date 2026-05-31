# PROGRESS.md — living state (update + commit WITH every phase checkpoint)

- **Last updated:** 2026-05-31 01:40 BST
- **HEAD:** `20f76a9` (Phase 7 checkpoint, packaging — bot.py unchanged)  ·  **branch:** `mybot`
- **NEXT STEP:** **OPTIONAL: adversarial-state hardening** (malformed states,
  all-in-for-less, heads-up transition, latency under real 0.5-core load) — the
  last pass, **awaiting explicit user go** (do not start unprompted). The bot is
  otherwise submission-ready: single-file `bot.py` verified on Python 3.10.
- **Phase 7 DONE:** 3.10 runtime verified, BOT_README written, single-file artifact
  confirmed, final validator pass green on 3.10 + 3.13. `bot.py` UNCHANGED.
- **How to resume:** `git checkout mybot` → read this file + `git log --oneline -10`
  → `python3 tools/harness.py` for the current baseline → continue at NEXT STEP.

## Cadence rule
Stop for the user's OK after **every** phase (the user reviews every line).
~~**HARD-STOP before Phase 5**~~ — CLEARED (Phase 5 built + approved). Do not roll
into the next phase unprompted. Each phase ends on a runnable, validator-passing
checkpoint. **Submission = `bots/mybot/bot.py` ONLY** — never commit engine/ or
sandbox/ changes onto `mybot` (tooling tweaks for analysis must be reverted before
a phase commit; the bot must run on the untouched engine).

## Phase status (0–7)
| Phase | Status | Checkpoint commit(s) |
|---|---|---|
| 0 — Recon / ground truth | ✅ DONE | (captured in this file + memory) |
| 1 — Safe skeleton | ✅ DONE | `529c568` |
| 2 — Equity core (MC, eval7) | ✅ DONE | `2eb1b55`; tooling `1a9e319`; repro harness `4ed130c` |
| 3 — Preflop as precomputed charts | ✅ DONE | `44b72c8` (docs `370bc33`) |
| 4 — Postflop (texture, SPR, pot control) | ✅ DONE | 4a `7556ee5` · 4b `7ca9622` |
| 5 — Opponent modelling + exploitation | ✅ DONE | `70768e4` |
| 6 — Test harness + tuning + regression gate | ✅ DONE (bot.py unchanged) | `150b9d8` |
| 7 — Package + validate + BOT_README | ✅ DONE (bot.py unchanged) | `20f76a9` |

## Standing decisions
- **Bust rate vs competent contesters is ~30% and is VARIANCE, not a leak**
  (Phase 6, honest_equity field). Diagnosed split of bust hands: **89% unavoidable**
  (16/28 postflop two-pair+ coolers, 9/28 preflop AK/AQ/pair races) vs **11%**
  postflop one-pair/TPGK stack-offs — and all of those were as the AGGRESSOR
  (we drove the betting), which the facing-bet-only 4a guard cannot reach.
  **No +EV commitment fix exists under the gate:** an aggressor-side cap on
  TPTK/overpair/one-pair would sacrifice value on the many hands that get paid
  (the Phase 4a additive trap) and would not clear. **DO NOT re-litigate / do not
  build aggressor-side caps without new evidence.** Tune commitment leaks ONLY on
  a field that contests pots (honest_equity), never the fold-happy refs.
- **Tooling/test changes never ride a `bot.py` commit** and are reverted before
  any phase checkpoint that touches the bot (the bot must run on the untouched
  engine; verified Phase 5).
- **The opponent classifier's maniac/station thresholds are NOT widened — by
  decision (Phase 7), this stays a documented known-limitation, not a fix.**
  Reasoning: (1) the LAG test showed that vs aggressive spewers MORE discipline
  wins (honest_equity beat us by harvesting spew at low variance), so "catch the
  maniac and contest more" points the WRONG way — it would increase contesting
  against the very opponent already busting us ~59%. (2) Any threshold change is a
  bet on the unseen real-field composition, validated only against self-written
  bots — the overfitting trap recursing (same error as tuning to the refs). (3)
  The bot is +EV vs BOTH tested populations (dominant vs foldy +15k; positive vs
  LAG +8.8k). That robustness is where to stop. `unknown → safe baseline` is
  working as designed. **Do not widen on the strength of one self-written field.**
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
- ~~**Unconditional postflop bluffing is −EV in this format**~~ (Phase 4b finding) —
  c-bets / barrels / semibluffs add variance, −EV in a bust-elimination format.
  **RESOLVED Phase 5:** the same bluff layer, GATED to fire only when every live
  opponent is a folder (nit / high fold-to-bet) and none is a station/maniac, is
  significantly +EV (+560/match, p=0.026) AND reduces busts (35→33) — gating, not
  the bluffs themselves, was the missing piece. "C-bet the folders, never the
  aggressor."
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

## Phase 5 notes (opponent modelling + gated exploitation)
- **Data substrate (verified):** `state["match_action_log"]` = original engine
  injection (sandbox/match.py:221/298) — `{hand_num, seat, bot_id, action,
  amount}`, rolling last-200, NO street markers, NO blinds. Bots receive ONLY
  `action_request`/`warmup` — **no `hand_complete`/`revealed_cards`**, so card-
  based showdown reads are IMPOSSIBLE (G1 drop confirmed). Bot reads the injected
  state field; it does NOT depend on any sandbox/ change (re-verified: +560 result
  reproduces on the untouched engine; engine/sandbox diff empty at commit).
- **Model:** module-global `_OPP` keyed by stable `bot_id` (seat is not), fed by
  `_observe`/`_fold_entry` from the cross-hand log via street-AGNOSTIC heuristics
  (first-action-in-hand = preflop entry; facing-prior-aggression). Stats: VPIP,
  PFR, agg%, fold-to-bet, fold-to-3bet. Shrinkage toward a HYBRID prior (fixed
  Phase-0 base blended toward the running pooled field avg, G2). `_classify` →
  station / maniac / nit / tag / unknown; `<_MIN_OBS=12` actions or no bucket →
  unknown → baseline. Exploit magnitude scales with confidence n/(n+K).
- **Exploits** (modulate 4a/4b; NEVER bypass the 4a guard): vs station — value
  thinner+bigger, fold more to their raises; vs maniac — widen bluff-catch; vs nit
  — smaller value, overfold their bets; the gated 4b bluff layer re-enables only
  when ALL live opps are folders and none station/maniac.
- **Classifier reality check (why the A/B still won):** label-accuracy vs my
  Phase-0 ground truth was only 1/5 — but my LABELS were wrong (math/ref_bot_2
  "call only ≥3:1" = foldy ROCKS, not calling stations; the field has no true
  stations/maniacs and the aggressor busts too fast (n≈15) to register). The
  classifier is FUNCTIONALLY correct: folders→nit→overfold+bluff (+EV), and the
  aggressor lands tag/unknown→NOT in the foldy set→bluff gate stays CLOSED when
  it's live (so "never bluff the aggressor" holds despite the missed maniac
  label). Absolute thresholds kept (G4/Phase-6 question: relative-to-pool).
- **A/B (200-seed paired, untouched engine):** engine-only +469 (p=0.059, CI
  [−15,+953] → neutral-or-better ✓); engine+bluff **+560 (p=0.026, CI
  [+70,+1050] → strictly positive ✓)**, busts 33 vs 35. Both flags shipped ON.

## Current performance (baseline to beat)
Deterministic PAIRED A/B (harness.py: seeded refs + `BOT_RNG_SEED`, common random
numbers):
- **Phase 5 (engine + gated bluff): +560/match vs Phase 4b** (200-seed paired,
  p=0.026, 95% CI [+70, +1050]); busts 33 vs 35/200. Mean level ≈ +15.8k.
  First statistically-significant EV gain since Phase 3; expected to be larger vs
  the heterogeneous 500-bot field (the 5 refs can't exercise station/maniac play).
- Phase 4b: statistically neutral vs 4a (paired −607, CI [−1,789,+576], p=0.32).
- Phase 4a: +15,868 (200-seed). Phase 3: +16,754 (30-seed). Phase 2: +11.5k.
Validator: PASSED. Equity: 5/5. Worst-case `decide()` ~4.4 ms (~8.7 ms @ 0.5
core); budget caps 1.2 s. Determinism: `harness.py --check` = YES.

## Phase 6 notes (third-party test + tuning + regression gate)
- **Third-party opponent:** rlcard installs but ships NO competent NLHE agent
  (only RandomAgent / untrained DQN-NFSP-DMC; CFR + pretrained models are tabular
  Leduc/Limit only), so per plan we used `bots/honest_equity/bot.py` — a competent,
  INDEPENDENTLY-coded equity TAG (no shared code, no charts/SPR/opponent-model;
  pure equity vs pot odds; deterministic under BOT_RNG_SEED). `tools/eval_field.py`
  runs the 6-max HONEST_FIELD (refs minus the redundant ref_bot_2, plus honest_equity).
- **Result (200 seeds, honest field):** mybot mean **+14,997, 111/200 wins**, beats
  honest_equity (+2,713). Edge is real, not echo-chamber. Bust rate 63/200 (~31%)
  vs 33/200 in the all-refs field — diagnosed as variance (see standing decision).
  mybot busts ~half as often as the unguarded honest_equity (63 vs 119) → 4a guard works.
- **Tuning sweep (200-seed paired vs HEAD, ref field):** SHRINK_K 20→12 (+190,p=.44),
  MIN_OBS 12→8 (no-op, identical), nit bcatch .12→.16 (+103,p=.24), SIZE_WET .70→.60
  (+427,p=.16). ALL statistically neutral (every CI includes 0) → **committed none;
  bot.py unchanged.** NOTE: ref-field tuning cannot surface commitment leaks (refs
  don't contest) — that critique drove the honest-field bust split above.
- **Regression gate** (`tools/regression_gate.py`, runnable, exit 0/1): a candidate
  is ACCEPTed only if (A) not significantly worse heads-up vs prev HEAD (seat-
  alternated, judged by CI), (B) no regression vs the ref field, (C) no regression
  vs the honest_equity contester field — all paired vs HEAD, neutral-or-better (CI
  hi ≥ 0). Use it on every future bot.py change.

## Submission status (the real upload artifact)
- **Single self-contained `bots/mybot/bot.py`** — 1,398 lines / 57 KB (≪ 5 MB cap).
- **Passes the validator IN ISOLATION:** copying ONLY bot.py to an empty dir and
  validating it there → PASSED (this is exactly how `match.py` mounts a `.py`
  submission). So the uploaded file = the tested file.
- **Zero runtime dependency outside bot.py:** imports are `time/random/os/eval7`
  (+ conditional `json`). The only file I/O is an OPTIONAL same-dir
  `data/preflop.json` (or `$BOT_DATA_DIR`); absent → identical inline charts. It
  does NOT touch `tools/`, `honest_equity`, `eval_field`, or any sibling (the two
  `tools/...` strings in bot.py are comments). `data/preflop.json` is byte-for-byte
  derivable from the inline shorthand, so single-file and dir/zip submissions agree.
- Phase 7 remaining: decide single-file vs zip+data (single-file is sufficient and
  recommended), write BOT_README, final validate, package.

## Phase 7 notes (packaging + 3.10 verification + overfitting test)
- **Python 3.10 runtime VERIFIED (the one real open risk — now closed):** provisioned
  a real 3.10.20 venv (uv), built eval7 from source there (needs Cython+setuptools
  as build deps; longintrepr.h present on 3.10). On 3.10: eval7 imports + evaluates;
  validator PASSES on the ISOLATED single-file; a full 400-hand match runs with
  `bot_errors: NONE`; equity tests 5/5. Final validator pass green on BOTH 3.10 and
  3.13. No forbidden imports. Artifact = `bots/mybot/bot.py`, 57,214 bytes,
  sha256 27c323af9aecaaf7e9ad62bcbc87c429a4e1c58bb881e154ecb3d620d39b2557.
- **BOT_README.md** written (name "Quant", approach, safety, deps=eval7, runtime).
- **Overfitting test (2nd independent opponent, `bots/lag_bot/bot.py` = loose-
  aggressive):** 200-seed LAG field [mybot, lag_bot, honest_equity, aggressor,
  mathematician, template]: mybot mean **+8,795, 69/200 wins, 118/200 busts (59%)**.
  honest_equity WON the field (+15,895, 103 wins) — a disciplined TAG with no
  opponent model beats us in a spew-heavy field by harvesting the LAG at low
  variance. Classifier put lag_bot in `unknown` (aggr 0.52 < maniac floor 0.55,
  vpip 0.63 > tag ceiling 0.50 → gap) so NO exploit fired; the maniac path DID
  fire for the seeded aggressor (aggr 0.65, n=355). Conclusion: baseline is +EV
  vs the LAG (safety net works) but the exploit edge does NOT generalize to
  aggressive contesters — see the "no maniac-threshold widen" standing decision.

## Pre-submit checklist (portal upload)
1. Artifact = `bots/mybot/bot.py` ONLY (single file; no zip/data needed). [verified]
2. Validator PASS on the isolated file, Python 3.10. [verified]
3. eval7 is the only third-party import; no forbidden imports. [verified]
4. `BOT_NAME = "Quant"` set; warmup handled. [in bot.py]
5. Confirm portal's expected filename/field for the single-file upload (portal-side).
6. (Optional, pre-submit) adversarial-state hardening pass — see NEXT STEP; awaiting go.
