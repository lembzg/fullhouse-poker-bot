"""
LAG BOT — a second independent test opponent (NOT the competition entry).

A loose-aggressive No-Limit Hold'em bot, written from scratch with logic
INDEPENDENT of bots/mybot/bot.py (no shared code, no charts/SPR/opponent model).
Its whole purpose is to be a strategic archetype the entry was NEVER tuned
against: it enters most pots (high VPIP), raises and barrels frequently, bluffs,
and is sticky — it does not fold to the small bets that fold the reference bots.

It is still anchored to equity so it's a *competent* LAG, not pure chaos:
it value-raises real hands big, bluff-raises/c-bets a fixed fraction of the
time, calls down loosely, and only folds the genuinely hopeless. This should
classify as a 'maniac' under a behavioural opponent model (high VPIP + high
aggression) and contest pots hard (so it stresses variance and the maniac
exploit path).

Deterministic under BOT_RNG_SEED; OS-seeded otherwise. Always returns a legal
action. eval7-based equity; light MC for speed (it's a sparring partner).
"""

import os
import time
import random

try:
    import eval7
    _HAVE = True
except Exception:
    _HAVE = False

BOT_NAME = "LAG"
BOT_AVATAR = "robot_3"

_seed = os.environ.get("BOT_RNG_SEED")
_RNG = random.Random(int(_seed)) if (_seed and _seed.lstrip("-").isdigit()) else random.Random()

_RANKS = "23456789TJQKA"
_SUITS = "shdc"
_BB = 100
_BUDGET = 0.5

if _HAVE:
    _DECK = [eval7.Card(r + s) for r in _RANKS for s in _SUITS]
    _BY = {str(c): c for c in _DECK}
else:
    _DECK, _BY = [], {}


def _n_opp(state, seat):
    n = 0
    for pl in state.get("players", []):
        try:
            if pl.get("seat") == seat or pl.get("is_folded"):
                continue
            n += 1
        except Exception:
            continue
    return max(1, min(n, 5))


def _equity(hole, board, n_opp, deadline):
    if not _HAVE or len(hole) < 2:
        return 0.0
    try:
        h = [_BY[c] for c in hole]; b = [_BY[c] for c in board]
    except KeyError:
        return 0.0
    dead = set(hole) | set(board)
    deck = [c for c in _DECK if str(c) not in dead]
    need = 5 - len(b); draw = need + 2 * n_opp
    if draw > len(deck):
        return 0.0
    ev = eval7.evaluate; wins = 0.0; done = 0
    for it in range(1000):
        if (it & 63) == 0 and time.monotonic() >= deadline:
            break
        d = _RNG.sample(deck, draw); full = b + d[:need]
        hs = ev(h + full); k = need; beat = False; tie = 1
        for _ in range(n_opp):
            o = ev(d[k:k + 2] + full); k += 2
            if o > hs:
                beat = True; break
            if o == hs:
                tie += 1
        if not beat:
            wins += 1.0 / tie
        done += 1
    return (wins / done) if done else 0.0


def _raise_to(state, target_to):
    cur = max(0, int(state.get("current_bet", 0)))
    bet_this = max(0, int(state.get("your_bet_this_street", 0)))
    stack = max(0, int(state.get("your_stack", 0)))
    min_to = max(0, int(state.get("min_raise_to", cur)))
    max_to = bet_this + stack
    if max_to <= cur:
        return {"action": "call"} if state.get("amount_owed", 0) > 0 else {"action": "check"}
    target_to = max(int(target_to), min_to)
    if target_to >= max_to:
        return {"action": "all_in"}
    return {"action": "raise", "amount": target_to}


def decide(state):
    start = time.monotonic()
    try:
        if not isinstance(state, dict) or state.get("type") == "warmup":
            return {"action": "check"}
    except Exception:
        return {"action": "fold"}
    try:
        owed = max(0, int(state.get("amount_owed", 0)))
        can_check = bool(state.get("can_check", owed == 0))
        pot = max(0, int(state.get("pot", 0)))
        hole = list(state.get("your_cards", []))
        board = list(state.get("community_cards", []))
        street = str(state.get("street", "preflop"))
        seat = int(state.get("seat_to_act", -1))
        cur = int(state.get("current_bet", 0))
        stack = max(0, int(state.get("your_stack", 0)))
        r = _RNG.random()

        if len(hole) < 2 or not _HAVE:
            if can_check:
                return {"action": "check"}
            return {"action": "call"} if r < 0.5 and owed > 0 else (
                {"action": "fold"} if owed > 0 else {"action": "check"})

        eq = _equity(hole, board, _n_opp(state, seat), start + _BUDGET)

        # Aggressive sizing: ~3x preflop, 0.66-1.0 pot postflop (occasional overbet).
        def opensize():
            return max(int(_BB * 3), int(state.get("min_raise_to", 0)))
        def betsize():
            frac = 1.0 if r < 0.2 else 0.7      # overbet 20% of the time
            return cur + max(1, int(pot * frac))

        if can_check or owed == 0:
            # c-bet / bluff a high fraction of the time; value-bet real hands.
            if eq >= 0.50 or r < 0.60:
                return _raise_to(state, opensize() if street == "preflop" else betsize())
            return {"action": "check"}

        # Facing a bet: value-raise big eq, bluff-raise sometimes, call LOOSE, rarely fold.
        if eq >= 0.66 or r < 0.18:
            return _raise_to(state, opensize() if street == "preflop" else betsize())
        pot_odds = owed / (pot + owed) if owed > 0 else 0.0
        if eq >= pot_odds - 0.06 or r < 0.30:          # sticky: call below price + float
            return {"action": "all_in"} if owed >= stack else {"action": "call"}
        return {"action": "fold"}
    except Exception:
        try:
            return {"action": "check"} if state.get("can_check") else {"action": "fold"}
        except Exception:
            return {"action": "fold"}
