"""
================================================================================
 FULLHOUSE HACKATHON ENTRY — bot.py
 No-Limit Texas Hold'em, 6-max (handles 2-9 as tables shrink to heads-up)
================================================================================

PHASE 2 — EQUITY CORE.
Replaces Phase 1's crude hand-tier proxy with a real Monte-Carlo equity engine
built on eval7 (the engine's own evaluator). Decisions now compare estimated
equity against pot odds / required equity. Preflop charts (Phase 3), board
texture & bet-sizing nuance (Phase 4), and opponent modelling (Phase 5) build on
top of this.

ALL Phase-1 safety guarantees are preserved:
  - guaranteed-legal pot-odds FALLBACK computed first, before any MC work;
  - hard time.monotonic budget (the MC loop polls it and bails to the fallback);
  - everything wrapped in try/except; every raise clamped to [min_raise_to,
    all-in]; warmup / malformed messages survive; 3.10-compatible syntax;
  - no forbidden imports (pure time.monotonic guard, no threading/pickle).
"""

import time
import random

# eval7 is the engine's evaluator and is guaranteed present in the sandbox. We
# still guard the import so a missing eval7 degrades to the cheap heuristic
# rather than auto-folding the entire match at import time.
try:
    import eval7
    _HAVE_EVAL7 = True
except Exception:
    _HAVE_EVAL7 = False

BOT_NAME = "Quant"
BOT_AVATAR = "robot_1"

# --- Latency budget --------------------------------------------------------
# The engine kills us at 2s (and can't preempt, so an overrun burns CPU into the
# next decision). We stop the MC loop at this wall-clock ceiling and return the
# already-computed fallback. Pure time.monotonic, polled inside the loop, so the
# real-world worst case is ~this budget regardless of CPU speed (0.5 core).
TIME_BUDGET_S = 1.2

# --- Card constants --------------------------------------------------------
_RANKS = "23456789TJQKA"
_SUITS = "shdc"
_RANK_VAL = {r: i for i, r in enumerate(_RANKS, start=2)}  # '2'->2 ... 'A'->14

# Pre-build the full 52-card deck ONCE at import time (warmup budget covers it).
# Keyed by string so deck removal is a cheap set-membership test. Built only if
# eval7 is available; otherwise the MC engine is never entered.
if _HAVE_EVAL7:
    _FULL_DECK = [eval7.Card(r + s) for r in _RANKS for s in _SUITS]
    _CARD_BY_STR = {str(c): c for c in _FULL_DECK}
else:
    _FULL_DECK = []
    _CARD_BY_STR = {}

# --- Adaptive Monte-Carlo sample counts per street -------------------------
# Base iteration counts. Scaled DOWN as the number of opponents rises (more
# evaluate() calls per iter) and hard-capped by the time budget regardless.
# River is cheapest (no runout to draw); preflop is most expensive (5 runout
# cards) but its result mostly gets overridden by charts in Phase 3, so it stays
# modest. These are tuned against the latency profile (see tools/profile_latency.py).
_BASE_ITERS = {
    "preflop": 1200,
    "flop":    1500,
    "turn":    2000,
    "river":   2500,
}


# ===========================================================================
# Defensive parsing
# ===========================================================================

def _g(state, key, default):
    try:
        v = state.get(key, default)
        return default if v is None else v
    except Exception:
        return default


def _parse(state):
    p = {}
    p["street"]        = str(_g(state, "street", "preflop"))
    p["your_cards"]    = list(_g(state, "your_cards", []))
    p["community"]     = list(_g(state, "community_cards", []))
    p["pot"]           = max(0, int(_g(state, "pot", 0)))
    p["stack"]         = max(0, int(_g(state, "your_stack", 0)))
    p["owed"]          = max(0, int(_g(state, "amount_owed", 0)))
    p["can_check"]     = bool(_g(state, "can_check", p["owed"] == 0))
    p["current_bet"]   = max(0, int(_g(state, "current_bet", 0)))
    p["bet_this"]      = max(0, int(_g(state, "your_bet_this_street", 0)))
    p["min_raise_to"]  = max(0, int(_g(state, "min_raise_to", p["current_bet"])))
    p["players"]       = list(_g(state, "players", []))
    p["seat"]          = int(_g(state, "seat_to_act", -1))
    p["max_to"]        = p["bet_this"] + p["stack"]
    return p


def _num_live_opponents(p):
    """Opponents still contesting the pot: players that are not me and not
    folded. eval7 equity is computed against this many random hands. Capped at 5
    to bound compute (full 6-max minus hero)."""
    seat = p["seat"]
    n = 0
    for pl in p["players"]:
        try:
            if pl.get("seat") == seat:
                continue          # this is me
            if pl.get("is_folded"):
                continue          # already out of the hand
            n += 1
        except Exception:
            continue
    if n <= 0:
        n = 1                     # never compute "vs zero opponents"
    return min(n, 5)


# ===========================================================================
# Monte-Carlo equity engine  (the Phase-2 core)
# ===========================================================================

def monte_carlo_equity(hero_cards, board_cards, opponents, iters, deadline=None):
    """Estimate hero's showdown equity via Monte Carlo.

    Args:
      hero_cards : list[str]  e.g. ["As","Ah"]   (exactly 2)
      board_cards: list[str]  0 / 3 / 4 / 5 community cards
      opponents  : list       one entry per opponent. Each entry is either
                              None  -> a random unknown hand sampled from the deck,
                              or a 2-card list[str] -> a FIXED known hand (used by
                              unit tests, and later by range-weighted modelling).
      iters      : int        max Monte-Carlo samples
      deadline   : float|None  time.monotonic ceiling; loop bails early if hit.

    Returns hero equity in [0,1] (wins + split-pot fractions / samples).
    """
    if not _HAVE_EVAL7 or len(hero_cards) < 2:
        return 0.0

    try:
        hero = [_CARD_BY_STR[c] for c in hero_cards]
        board = [_CARD_BY_STR[c] for c in board_cards]
    except KeyError:
        return 0.0  # malformed card string -> let caller use fallback

    # ----------------------------------------------------------------------
    # *** DECK REMOVAL — the correctness-critical line ***
    # Build the live deck by removing EVERY card already in play: the hero's two
    # hole cards, all board cards, AND any fixed opponent cards. Nothing in
    # `dead` can ever be dealt as an opponent hole card or a runout card.
    dead = set(hero_cards) | set(board_cards)
    fixed = []
    for opp in opponents:
        if opp is None:
            fixed.append(None)
        else:
            fixed.append([_CARD_BY_STR[x] for x in opp])
            dead |= set(opp)
    deck = [c for c in _FULL_DECK if str(c) not in dead]
    # ----------------------------------------------------------------------

    need_board = 5 - len(board)                       # runout cards to draw
    n_rand_opp = sum(1 for f in fixed if f is None)
    draw_count = need_board + 2 * n_rand_opp
    if draw_count > len(deck):
        return 0.0                                    # not enough cards (bad input)

    evaluate = eval7.evaluate
    sample = random.sample
    wins = 0.0
    done = 0

    for it in range(iters):
        # Poll the wall-clock budget every 64 iters (cheap, keeps us legal even
        # if the field's CPU share is throttled to 0.5 core).
        if deadline is not None and (it & 63) == 0 and time.monotonic() >= deadline:
            break

        drawn = sample(deck, draw_count)
        k = 0
        runout = drawn[k:k + need_board]; k += need_board
        full_board = board + runout

        hero_score = evaluate(hero + full_board)

        beaten = False
        tied = 1                                      # hero counts once
        for f in fixed:
            if f is None:
                opp_hole = drawn[k:k + 2]; k += 2
            else:
                opp_hole = f
            opp_score = evaluate(opp_hole + full_board)
            if opp_score > hero_score:
                beaten = True
                break                                 # hero wins 0 this runout
            elif opp_score == hero_score:
                tied += 1

        if not beaten:
            wins += 1.0 / tied                         # split pot among ties
        done += 1

    return (wins / done) if done else 0.0


# ===========================================================================
# Legal-action helpers
# ===========================================================================

def _check_or_fold(p):
    return {"action": "check"} if p["can_check"] else {"action": "fold"}


def _call_or_check(p):
    if p["owed"] <= 0 or p["can_check"]:
        return {"action": "check"} if p["can_check"] else {"action": "call"}
    if p["owed"] >= p["stack"]:
        return {"action": "all_in"}
    return {"action": "call"}


def _make_raise(p, target_to):
    """Legal raise to absolute total `target_to`, clamped to [min_raise_to,
    all-in]; degrades to call/check if we can't legally raise."""
    max_to = p["max_to"]
    if max_to <= p["current_bet"]:
        return _call_or_check(p)
    target_to = int(target_to)
    if target_to < p["min_raise_to"]:
        target_to = p["min_raise_to"]
    if target_to >= max_to:
        return {"action": "all_in"}
    return {"action": "raise", "amount": target_to}


# ===========================================================================
# Cheap preflop proxy  (fallback only; not the main path once charts land)
# ===========================================================================

def _hole_vals(cards):
    if len(cards) < 2 or len(cards[0]) < 2 or len(cards[1]) < 2:
        return None
    v1, v2 = _RANK_VAL.get(cards[0][0]), _RANK_VAL.get(cards[1][0])
    if v1 is None or v2 is None:
        return None
    return max(v1, v2), min(v1, v2), (v1 == v2), (cards[0][1] == cards[1][1])


def _preflop_proxy(cards):
    """Rough 0..1 preflop strength from hole cards alone — used only by the
    cheap fallback when eval7/MC is unavailable."""
    hv = _hole_vals(cards)
    if hv is None:
        return 0.0
    hi, lo, is_pair, is_suited = hv
    s = ((hi - 2) / 12.0) * 0.42 + ((lo - 2) / 12.0) * 0.20
    if is_pair:
        s += 0.30 + ((hi - 2) / 12.0) * 0.18
    if is_suited:
        s += 0.07
    gap = hi - lo
    if not is_pair:
        s += {1: 0.07, 2: 0.04, 3: 0.02}.get(gap, 0.0)
    if hi == 14:
        s += 0.03
    return max(0.0, min(1.0, s))


# ===========================================================================
# Pot odds + fallback
# ===========================================================================

def _required_equity(p):
    """Break-even equity for a call: owed / (pot + owed)."""
    denom = p["pot"] + p["owed"]
    if denom <= 0 or p["owed"] <= 0:
        return 0.0
    return p["owed"] / denom


def _fallback_action(p):
    """Guaranteed-legal, cheap pot-odds decision (no MC). Returned on any
    failure or time overrun. Uses the preflop proxy / a coarse made-hand read so
    it still plays a sane tight-passive game by itself."""
    if p["can_check"] or p["owed"] <= 0:
        return _check_or_fold(p)
    # Coarse strength: preflop proxy preflop; on later streets assume marginal.
    if p["street"] == "preflop":
        strength = _preflop_proxy(p["your_cards"])
    else:
        strength = 0.5  # neutral guess; fallback only runs when MC failed
    if strength >= _required_equity(p) + 0.05:
        return _call_or_check(p)
    return {"action": "fold"}


# ===========================================================================
# Equity-driven decision  (Phase-2 baseline)
# ===========================================================================

# Thresholds (tuned in Phase 6). vs-random equity is OPTIMISTIC versus a range
# that has chosen to put money in, so call/raise bars carry a cushion. These get
# replaced/adjusted by range modelling (Phase 4) and exploits (Phase 5).
_VALUE_BET_EQ   = 0.58   # when checked to, bet for value at/above this equity
_VALUE_RAISE_EQ = 0.78   # when facing a bet, raise (not just call) at/above this
_CALL_CUSHION   = 0.03   # require equity >= pot_odds + this to call


def _preflop_raise_target(p):
    """Open / re-raise sizing preflop: ~3x the current bet (a standard ~3bb open
    or 3x-the-raise re-raise), clamped later by _make_raise."""
    base = p["current_bet"] if p["current_bet"] > 0 else p["min_raise_to"]
    return max(p["min_raise_to"], base * 3)


def _bet_target(p, pot_frac):
    """Postflop bet/raise to add `pot_frac` of the pot on top of the current
    bet (so checked-to => bet pot_frac*pot; facing a bet => raise by pot_frac*pot)."""
    add = max(p["min_raise_to"] - p["current_bet"], int(p["pot"] * pot_frac))
    return p["current_bet"] + add


def _strategy_action(p, deadline):
    """Equity vs pot odds. Bet/raise strong equity, call correct-priced draws,
    fold the rest. Deliberately simple — the exploit overlay rides on top later."""
    # Cheap exits that never need MC.
    if not _HAVE_EVAL7 or len(p["your_cards"]) < 2:
        return _fallback_action(p)
    if time.monotonic() >= deadline:
        return _fallback_action(p)

    n_opp = _num_live_opponents(p)
    iters = max(200, _BASE_ITERS.get(p["street"], 1200) // n_opp)
    eq = monte_carlo_equity(p["your_cards"], p["community"],
                            [None] * n_opp, iters, deadline)
    req = _required_equity(p)

    if p["can_check"]:
        # No bet to us: value-bet strong equity, otherwise take the free card.
        if eq >= _VALUE_BET_EQ:
            if p["street"] == "preflop":
                return _make_raise(p, _preflop_raise_target(p))
            # Size up with equity: ~half pot baseline, ~3/4 pot when very strong.
            frac = 0.75 if eq >= 0.72 else 0.55
            return _make_raise(p, _bet_target(p, frac))
        return {"action": "check"}

    # Facing a bet: raise huge equity, call correctly-priced, else fold.
    if eq >= _VALUE_RAISE_EQ:
        if p["street"] == "preflop":
            return _make_raise(p, _preflop_raise_target(p))
        return _make_raise(p, _bet_target(p, 0.7))
    if eq >= req + _CALL_CUSHION:
        return _call_or_check(p)
    return {"action": "fold"}


# ===========================================================================
# Entry point
# ===========================================================================

def decide(game_state):
    start = time.monotonic()

    # (1) Non-game messages (warmup): must not crash; reply is discarded.
    try:
        if not isinstance(game_state, dict) or game_state.get("type") == "warmup":
            # Touch eval7 once so any lazy C init happens under the 30s warmup
            # budget rather than on hand 1.
            if _HAVE_EVAL7:
                try:
                    eval7.evaluate([_CARD_BY_STR["As"], _CARD_BY_STR["Ks"],
                                    _CARD_BY_STR["Qs"], _CARD_BY_STR["Js"],
                                    _CARD_BY_STR["Ts"]])
                except Exception:
                    pass
            return {"action": "check"}
    except Exception:
        return {"action": "fold"}

    # (2) Parse + (3) guaranteed-legal fallback, both guarded.
    try:
        p = _parse(game_state)
        fallback = _fallback_action(p)
    except Exception:
        return {"action": "fold"}

    # (4) Equity-driven strategy under guard + time budget.
    try:
        deadline = start + TIME_BUDGET_S
        action = _strategy_action(p, deadline)
        if not isinstance(action, dict) or action.get("action") not in (
            "fold", "check", "call", "raise", "all_in"
        ):
            return fallback
        if action["action"] == "raise" and "amount" not in action:
            return fallback
        if action["action"] == "check" and not p["can_check"]:
            return fallback
        return action
    except Exception:
        return fallback
