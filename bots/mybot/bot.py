"""
================================================================================
 FULLHOUSE HACKATHON ENTRY — bot.py
 No-Limit Texas Hold'em, 6-max (handles 2-9 as tables shrink to heads-up)
================================================================================

PHASE 1 — SAFE SKELETON.
This checkpoint is intentionally conservative. Its job is NOT to play great
poker yet; it is to GUARANTEE we never time out, never crash, and never emit an
illegal action — which alone beats the auto-fold baseline. Real strategy
(equity Monte Carlo, preflop charts, opponent modelling) lands in later phases.

GROUND TRUTH this file is built against (verified from engine/ source):
  - decide(state) is called once per action; must return within 2s or the
    engine auto-folds us. Timeout is a daemon thread that CANNOT preempt us, so
    overrunning keeps burning CPU into the next decision -> we self-police with
    a hard time.monotonic budget well under 2s.
  - We are a long-lived subprocess: module-level globals persist across all 400
    hands of a match, then reset next match (fresh process). Good for opponent
    stats later.
  - Before hand 1 the engine sends {"type":"warmup"} (30s budget) so heavy
    imports finish; decide() must survive non-game messages.
  - amount in a raise is the TOTAL bet-to. min_raise_to = current_bet+min_raise.
  - Engine is forgiving (illegal->fold, sub-min raise->snapped) but we never
    rely on that; we emit only clean, legal actions.
  - Opponents are keyed by persistent bot_id; seat is NOT stable (remaps on
    bustouts) -> we always read identity from players[seat]["bot_id"] later.
  - PRODUCTION RUNS PYTHON 3.10. Keep syntax 3.10-compatible (no 3.11+ only
    features). Forbidden imports: threading, importlib, pickle, asyncio, etc.
    -> our clock guard is pure time.monotonic; no concurrency primitives.
"""

import time

# eval7 is the engine's own evaluator and is guaranteed available in the sandbox.
# We still import it defensively: if for any reason it's missing, the bot must
# degrade to the pure-Python heuristics rather than fail to import (which would
# auto-fold every hand of the match).
try:
    import eval7  # noqa: F401  (used in postflop made-hand evaluation below)
    _HAVE_EVAL7 = True
except Exception:
    _HAVE_EVAL7 = False

BOT_NAME = "Quant"      # leaderboard display name
BOT_AVATAR = "robot_1"

# --- Latency budget --------------------------------------------------------
# Hard self-imposed wall-clock ceiling per decision. The engine kills us at 2s;
# we stop at 1.5s so we always have margin to serialise and return a legal
# action. Phase 1 does almost no work, but the budget plumbing is wired now so
# the Phase 2 Monte-Carlo loop can poll it and bail early.
TIME_BUDGET_S = 1.5

# --- Card constants --------------------------------------------------------
_RANK_VAL = {r: i for i, r in enumerate("23456789TJQKA", start=2)}  # '2'->2 ... 'A'->14


# ===========================================================================
# Defensive parsing
# ===========================================================================
# Every accessor uses .get with a safe default. A malformed / partial state
# (or the warmup ping) must never raise out of here.

def _g(state, key, default):
    try:
        v = state.get(key, default)
        return default if v is None else v
    except Exception:
        return default


def _parse(state):
    """Pull the fields we need into a plain dict of safe primitives."""
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
    # Maximum TOTAL we could have committed on this street if we shoved:
    # everything already in front of us plus everything behind it.
    p["max_to"]        = p["bet_this"] + p["stack"]
    return p


# ===========================================================================
# Legal-action helpers  (every emitted action passes through here)
# ===========================================================================

def _check_or_fold(p):
    """The cheapest always-legal action: take a free check, else fold."""
    return {"action": "check"} if p["can_check"] else {"action": "fold"}


def _make_raise(p, target_to):
    """Build a legal raise to an absolute total `target_to`, clamped to
    [min_raise_to, all-in]. Returns all_in when the target meets/exceeds our
    stack, and degrades to call/check if we somehow can't legally raise."""
    max_to = p["max_to"]
    # If we have no chips behind, we can't raise — just call/check.
    if max_to <= p["current_bet"]:
        return _call_or_check(p)
    target_to = int(target_to)
    # Snap up to the minimum legal raise.
    if target_to < p["min_raise_to"]:
        target_to = p["min_raise_to"]
    # If reaching the (snapped) target requires our whole stack, it's an all-in.
    if target_to >= max_to:
        return {"action": "all_in"}
    return {"action": "raise", "amount": target_to}


def _call_or_check(p):
    """Call if there's something owed (and we can afford it); else check."""
    if p["owed"] <= 0 or p["can_check"]:
        return {"action": "check"} if p["can_check"] else {"action": "call"}
    # Owe more than our stack -> calling is an all-in; emit all_in explicitly.
    if p["owed"] >= p["stack"]:
        return {"action": "all_in"}
    return {"action": "call"}


# ===========================================================================
# Cheap hand-strength estimate  (no Monte Carlo — that's Phase 2)
# ===========================================================================

def _hole_vals(cards):
    """Return (hi, lo, is_pair, is_suited) from two card strings, or None."""
    if len(cards) < 2 or len(cards[0]) < 2 or len(cards[1]) < 2:
        return None
    r1, s1 = cards[0][0], cards[0][1]
    r2, s2 = cards[1][0], cards[1][1]
    v1, v2 = _RANK_VAL.get(r1), _RANK_VAL.get(r2)
    if v1 is None or v2 is None:
        return None
    hi, lo = max(v1, v2), min(v1, v2)
    return hi, lo, (v1 == v2), (s1 == s2)


def _preflop_strength(cards):
    """Rough 0..1 preflop strength from hole cards alone.

    This is a deliberately simple closed-form proxy (high-card weight + pair /
    suited / connectedness bonuses) — NOT a real equity number. Phase 3 replaces
    it with precomputed 169-hand equity. Good enough to make Phase 1 fold trash,
    raise premiums, and not spew."""
    hv = _hole_vals(cards)
    if hv is None:
        return 0.0
    hi, lo, is_pair, is_suited = hv
    # High-card contribution (normalised so AKo ~ 0.6 before bonuses).
    s = ((hi - 2) / 12.0) * 0.42 + ((lo - 2) / 12.0) * 0.20
    if is_pair:
        # Pairs are strong; scale with rank (22 modest, AA huge).
        s += 0.30 + ((hi - 2) / 12.0) * 0.18
    if is_suited:
        s += 0.07
    gap = hi - lo
    if not is_pair:
        if gap == 1:
            s += 0.07      # connectors
        elif gap == 2:
            s += 0.04
        elif gap == 3:
            s += 0.02
    if hi == 14:           # having an ace adds a little
        s += 0.03
    return max(0.0, min(1.0, s))


# Coarse map from eval7 hand-type label -> 0..1 made-hand strength. Intentionally
# blunt (ignores top-pair vs bottom-pair etc.); Phase 4 adds board-texture and
# kicker nuance. Used only as a heuristic this phase.
_MADE_STRENGTH = {
    "High Card":       0.12,
    "Pair":            0.35,
    "Two Pair":        0.58,
    "Trips":           0.70,
    "Straight":        0.80,
    "Flush":           0.86,
    "Full House":      0.93,
    "Quads":           0.98,
    "Straight Flush":  0.99,
}


def _postflop_strength(cards, community):
    """Rough 0..1 strength of our current made hand using eval7's hand type.
    Falls back to the preflop proxy if eval7 is unavailable or inputs are bad."""
    if not _HAVE_EVAL7:
        return _preflop_strength(cards)
    try:
        ec = [eval7.Card(c) for c in cards] + [eval7.Card(c) for c in community]
        if len(ec) < 5:
            return _preflop_strength(cards)
        label = str(eval7.handtype(eval7.evaluate(ec)))
        return _MADE_STRENGTH.get(label, 0.30)
    except Exception:
        return _preflop_strength(cards)


def _strength(p):
    if p["street"] == "preflop" or len(p["community"]) < 3:
        return _preflop_strength(p["your_cards"])
    return _postflop_strength(p["your_cards"], p["community"])


# ===========================================================================
# Fallback decision  (computed FIRST, every call, cheaply, before any risk)
# ===========================================================================

def _required_equity(p):
    """Pot odds: fraction of the post-call pot we must win to break even on a
    call. owed / (pot + owed). Returns 0 when nothing is owed."""
    denom = p["pot"] + p["owed"]
    if denom <= 0 or p["owed"] <= 0:
        return 0.0
    return p["owed"] / denom


def _fallback_action(p):
    """A guaranteed-legal, cheap pot-odds decision. This is what we return if
    the richer strategy raises, overruns the time budget, or anything else goes
    wrong. It already plays a reasonable tight-passive game on its own."""
    if p["can_check"]:
        return {"action": "check"}
    if p["owed"] <= 0:
        return {"action": "check"}
    strength = _strength(p)
    needed = _required_equity(p)
    # Call when our crude strength comfortably clears the price; small cushion
    # so we don't call off on marginal spots. Never raises (safe by design).
    if strength >= needed + 0.05:
        return _call_or_check(p)
    return {"action": "fold"}


# ===========================================================================
# Phase-1 "strategy" layer  (thin; just adds value raises on top of fallback)
# ===========================================================================

def _strategy_action(p, deadline):
    """Slightly more active than the fallback: raise premiums / strong made
    hands for value, otherwise defer to pot-odds logic. Polls the time budget
    so future heavy phases can bail to the fallback cleanly."""
    if time.monotonic() >= deadline:
        return _fallback_action(p)

    strength = _strength(p)

    if p["street"] == "preflop":
        # Premium: open/3-bet to ~3x the current bet (min one BB-ish via min_raise_to).
        if strength >= 0.78:
            return _make_raise(p, max(p["min_raise_to"], p["current_bet"] * 3 or p["min_raise_to"]))
        # Strong-but-not-premium: raise if cheap to, else call cheap, else fold.
        if strength >= 0.62:
            if p["can_check"]:
                return _make_raise(p, p["min_raise_to"])
            if _required_equity(p) <= strength:
                return _call_or_check(p)
        return _fallback_action(p)

    # Postflop.
    if p["can_check"]:
        # Bet ~60% pot for value with strong made hands; otherwise check.
        if strength >= 0.55:
            target = p["current_bet"] + max(p["min_raise_to"] - p["current_bet"], int(p["pot"] * 0.6))
            return _make_raise(p, target)
        return {"action": "check"}

    # Facing a bet postflop: raise very strong hands for value, else pot-odds.
    if strength >= 0.80:
        target = p["current_bet"] + max(p["min_raise_to"] - p["current_bet"], int(p["pot"] * 0.7))
        return _make_raise(p, target)
    return _fallback_action(p)


# ===========================================================================
# Entry point
# ===========================================================================

def decide(game_state):
    """Single entry point the engine calls. Contract: return a dict with a
    legal 'action' key within 2s, no matter what. Structured as:
        1) handle non-game messages (warmup) trivially
        2) parse defensively
        3) compute a guaranteed-legal fallback FIRST
        4) try the richer strategy under a try/except; on ANY failure or time
           overrun, return the fallback.
    """
    start = time.monotonic()

    # (1) Warmup / non-action messages: the engine discards the reply, we just
    # must not crash. A check is harmless and lets imports finish warming.
    try:
        if not isinstance(game_state, dict) or game_state.get("type") == "warmup":
            return {"action": "check"}
    except Exception:
        return {"action": "fold"}

    # (2) Parse + (3) fallback, both inside try so even these can't kill us.
    try:
        p = _parse(game_state)
        fallback = _fallback_action(p)
    except Exception:
        # Last-ditch: we couldn't even parse. Fold is always legal.
        return {"action": "fold"}

    # (4) Richer strategy under guard. Any exception or overrun -> fallback.
    try:
        deadline = start + TIME_BUDGET_S
        action = _strategy_action(p, deadline)
        # Paranoia: validate the shape before returning.
        if not isinstance(action, dict) or action.get("action") not in (
            "fold", "check", "call", "raise", "all_in"
        ):
            return fallback
        if action["action"] == "raise" and "amount" not in action:
            return fallback
        # Never check when we actually owe chips (engine would convert, but be clean).
        if action["action"] == "check" and not p["can_check"]:
            return fallback
        return action
    except Exception:
        return fallback
