"""
HONEST EQUITY BOT — an independent test opponent (NOT the competition entry).

A clean, principled No-Limit Hold'em bot written from scratch with logic that is
deliberately INDEPENDENT of bots/mybot/bot.py: it shares no code, no preflop
charts, no SPR pot-control, and — crucially — no opponent modelling. It simply
estimates its showdown equity by Monte-Carlo (eval7) against the live opponents
as random hands, then plays straightforwardly:

  * bet / raise when it is comfortably ahead,
  * call when the pot odds justify it,
  * fold when priced out,
  * never bluff, never pay off without equity.

The point of this bot is to be a *competent, honest* third party — a different
strategic archetype from the five exploitable reference bots — so that a
"wrong-but-self-consistent" entry (one that only beats tight bots it was tuned
against) is exposed when it plays someone who folds correctly and value-bets.

Deterministic under BOT_RNG_SEED (so the seeded harness stays reproducible);
OS-seeded otherwise. Defensive throughout; always returns a legal action.
"""

import os
import time
import random

try:
    import eval7
    _HAVE_EVAL7 = True
except Exception:
    _HAVE_EVAL7 = False

BOT_NAME = "Honest Equity"
BOT_AVATAR = "robot_2"

_seed = os.environ.get("BOT_RNG_SEED")
_RNG = random.Random(int(_seed)) if (_seed and _seed.lstrip("-").isdigit()) else random.Random()

_RANKS = "23456789TJQKA"
_SUITS = "shdc"
_BB = 100
_BUDGET_S = 0.6   # this is a sparring partner, not the entry — keep it light

if _HAVE_EVAL7:
    _DECK = [eval7.Card(r + s) for r in _RANKS for s in _SUITS]
    _BYSTR = {str(c): c for c in _DECK}
else:
    _DECK, _BYSTR = [], {}

# Straightforward equity gates (no opponent reads — these never adapt).
_OPEN_EQ   = 0.60   # checked to us: bet when equity is at least this
_RAISE_EQ  = 0.80   # facing a bet: raise (for value) at/above this
_MARGIN    = 0.04   # require equity >= pot_odds + this to call


def _live_opponents(state, my_seat):
    n = 0
    for pl in state.get("players", []):
        try:
            if pl.get("seat") == my_seat or pl.get("is_folded"):
                continue
            n += 1
        except Exception:
            continue
    return max(1, min(n, 5))


def _equity(hole, board, n_opp, deadline):
    if not _HAVE_EVAL7 or len(hole) < 2:
        return 0.0
    try:
        h = [_BYSTR[c] for c in hole]
        b = [_BYSTR[c] for c in board]
    except KeyError:
        return 0.0
    dead = set(hole) | set(board)
    deck = [c for c in _DECK if str(c) not in dead]
    need = 5 - len(b)
    draw = need + 2 * n_opp
    if draw > len(deck):
        return 0.0
    ev = eval7.evaluate
    wins = 0.0
    done = 0
    for it in range(1500):
        if (it & 63) == 0 and time.monotonic() >= deadline:
            break
        d = _RNG.sample(deck, draw)
        run = d[:need]
        full = b + run
        hs = ev(h + full)
        k = need
        beat = False
        tie = 1
        for _ in range(n_opp):
            os_ = ev(d[k:k + 2] + full); k += 2
            if os_ > hs:
                beat = True
                break
            if os_ == hs:
                tie += 1
        if not beat:
            wins += 1.0 / tie
        done += 1
    return (wins / done) if done else 0.0


def _legal_raise(state, target_to):
    cur = max(0, int(state.get("current_bet", 0)))
    bet_this = max(0, int(state.get("your_bet_this_street", 0)))
    stack = max(0, int(state.get("your_stack", 0)))
    min_to = max(0, int(state.get("min_raise_to", cur)))
    max_to = bet_this + stack
    if max_to <= cur:                                  # can't raise -> call/check
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
        my_seat = int(state.get("seat_to_act", -1))

        if len(hole) < 2 or not _HAVE_EVAL7:
            return {"action": "check"} if can_check else (
                {"action": "fold"} if owed > 0 else {"action": "check"})

        n_opp = _live_opponents(state, my_seat)
        eq = _equity(hole, board, n_opp, start + _BUDGET_S)
        pot_odds = owed / (pot + owed) if owed > 0 else 0.0

        if can_check or owed == 0:
            if eq >= _OPEN_EQ:
                if street == "preflop":
                    target = max(int(_BB * 2.5), int(state.get("min_raise_to", 0)))
                else:
                    cur = int(state.get("current_bet", 0))
                    target = cur + max(1, int(pot * 0.66))
                return _legal_raise(state, target)
            return {"action": "check"}

        # Facing a bet.
        if eq >= _RAISE_EQ:
            cur = int(state.get("current_bet", 0))
            target = cur + max(1, int(pot * 0.66)) if street != "preflop" else max(
                cur * 3, int(state.get("min_raise_to", 0)))
            return _legal_raise(state, target)
        if eq >= pot_odds + _MARGIN:
            stack = max(0, int(state.get("your_stack", 0)))
            return {"action": "all_in"} if owed >= stack else {"action": "call"}
        return {"action": "fold"}
    except Exception:
        try:
            return {"action": "check"} if state.get("can_check") else {"action": "fold"}
        except Exception:
            return {"action": "fold"}
