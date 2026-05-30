"""
================================================================================
 FULLHOUSE HACKATHON ENTRY — bot.py
 No-Limit Texas Hold'em, 6-max (handles 2-9 as tables shrink to heads-up)
================================================================================

PHASES 2–4a.
Phase 2 built a Monte-Carlo equity core on eval7 (the engine's own evaluator);
Phase 3 made preflop an O(1) chart lookup. Phase 4a adds POSTFLOP made-hand
CLASS + STACK-TO-POT-RATIO pot control on top of the equity number: the class
governs how much we're willing to commit (one pair never stacks off when deep —
the K7o over-commitment leak), equity governs whether the immediate price is
right. C-bet / barrel / bluff-catch refinement and opponent modelling build on
top later (Phases 4b / 5).

ALL Phase-1 safety guarantees are preserved:
  - guaranteed-legal pot-odds FALLBACK computed first, before any MC work;
  - hard time.monotonic budget (the MC loop polls it and bails to the fallback);
  - everything wrapped in try/except; every raise clamped to [min_raise_to,
    all-in]; warmup / malformed messages survive; 3.10-compatible syntax;
  - no forbidden imports (pure time.monotonic guard, no threading/pickle).
"""

import time
import random
import os

# --- Reproducibility hook (TEST-ONLY; off in production) -------------------
# The Monte-Carlo sampler is stochastic, which makes regression comparisons
# noisy. If (and ONLY if) the environment variable BOT_RNG_SEED is set at
# module-import time, we route all sampling through a dedicated seeded RNG so a
# (engine_seed, BOT_RNG_SEED) pair reproduces a match exactly. The tournament
# never sets this var, so live play uses a fresh OS-entropy RNG and stays
# stochastic / unexploitable. os.environ.get is an allowed call (not a banned
# os.* function); read once at import, never during decide().
_seed_env = os.environ.get("BOT_RNG_SEED")
if _seed_env is not None and _seed_env.lstrip("-").isdigit():
    _RNG = random.Random(int(_seed_env))
else:
    _RNG = random.Random()   # OS-seeded: production default, fully stochastic

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
_BB = 100   # big blind in chips (engine config; blinds never escalate)

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
    p["action_log"]    = list(_g(state, "action_log", []))
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
    sample = _RNG.sample          # seeded in tests, OS-entropy in production
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
# PHASE 3 — Precomputed preflop charts (O(1) lookup; built once at import)
# ===========================================================================
# Ranges are written as compact, reviewable shorthand and EXPANDED into frozen
# sets of 169 hand classes at import time (never inside decide()). Keying:
#   position    : UTG / MP / CO / BTN / SB / BB  (HU uses SB=button, BB)
#   facing      : unopened / vs_limp / vs_raise / vs_3bet / vs_4bet
#   stack bucket: <=15bb -> Nash push/fold ;  >15bb -> sized open/call/3bet/4bet
# A 100bb stack therefore NEVER hits a shove/fold node (push/fold is gated at
# eff<=15bb). Spots the charts don't cover return None -> the Phase-2 MC takes
# over, so charts can only add information, never regress coverage.
#
# Shorthand tokens understood by _expand():
#   "AA"  pair            "AKs"/"AKo" single suited/offsuit
#   "22+" pairs >= 22     "ATs+" Ax suited from T up      "KJo+" likewise
#   "TT-22" pair range    "AQs-ATs" same-high suited range (descending hi->lo)

_RANK_ORDER = "23456789TJQKA"
_RIDX = {r: i for i, r in enumerate(_RANK_ORDER)}


def _expand_token(tok):
    tok = tok.strip()
    if not tok:
        return set()
    # Range with a dash, e.g. "TT-22" or "AQs-ATs".
    if "-" in tok:
        a, b = tok.split("-", 1)
        if len(a) == 2 and a[0] == a[1] and len(b) == 2:   # pair range
            i, j = _RIDX[a[0]], _RIDX[b[0]]
            lo, hi = min(i, j), max(i, j)
            return {_RANK_ORDER[k] * 2 for k in range(lo, hi + 1)}
        if len(a) == 3 and len(b) == 3:                    # suited/offsuit range
            hi, su = a[0], a[2]
            i, j = _RIDX[a[1]], _RIDX[b[1]]
            lo_i, hi_i = min(i, j), max(i, j)
            return {hi + _RANK_ORDER[k] + su for k in range(lo_i, hi_i + 1)}
        return set()
    plus = tok.endswith("+")
    core = tok[:-1] if plus else tok
    if len(core) == 2 and core[0] == core[1]:              # pair (+)
        i = _RIDX[core[0]]
        return {_RANK_ORDER[k] * 2 for k in range(i, 13)} if plus else {core}
    if len(core) == 3:                                     # suited/offsuit (+)
        hi, lo, su = core[0], core[1], core[2]
        if not plus:
            return {core}
        return {hi + _RANK_ORDER[k] + su for k in range(_RIDX[lo], _RIDX[hi])}
    return set()


def _expand(spec):
    out = set()
    for tok in spec.replace(",", " ").split():
        out |= _expand_token(tok)
    return frozenset(out)


# --- Range shorthand (deep, >15bb) -----------------------------------------
# Opens (raise-first-in) by position; BB has no open node.
_OPEN = {
    "UTG": "22+, ATs+, KTs+, QTs+, JTs, T9s, 98s, AJo+, KQo",
    "MP":  "22+, A9s+, KTs+, QTs+, J9s+, T9s, 98s, ATo+, KJo+, QJo",
    "CO":  "22+, A2s+, K9s+, Q9s+, J8s+, T8s+, 97s+, 86s+, 65s, 54s, A9o+, KTo+, QTo+, JTo",
    "BTN": "22+, A2s+, K5s+, Q7s+, J7s+, T7s+, 96s+, 85s+, 74s+, 64s+, 53s+, A2o+, K9o+, Q9o+, J9o+, T8o+, 98o",
    "SB":  "22+, A2s+, K7s+, Q8s+, J8s+, T8s+, 97s+, 86s+, 75s+, 65s, 54s, A7o+, K9o+, Q9o+, JTo",
}
# Iso-raise over limpers (tighter than open; multiway).
_ISO = {
    "UTG": "77+, ATs+, KQs, AJo+, KQo",
    "MP":  "77+, ATs+, KQs, AJo+, KQo",
    "CO":  "55+, A9s+, KTs+, QTs+, JTs, ATo+, KJo+",
    "BTN": "33+, A8s+, K9s+, Q9s+, J9s+, T9s, A9o+, KJo+",
    "SB":  "44+, A9s+, KTs+, QTs+, JTs, ATo+, KJo+",
    "BB":  "44+, A9s+, KTs+, QTs+, JTs, ATo+, KJo+",
}
# Facing a single raise: flat (call) and 3-bet ranges by hero position.
_VS_RAISE_CALL = {
    "UTG": "TT-22, AQs-ATs, KQs, KJs, QJs, JTs",
    "MP":  "TT-22, AQs-ATs, KQs, KJs, QJs, JTs, T9s",
    "CO":  "JJ-22, AJs-A9s, KQs-KTs, QJs-QTs, JTs, T9s, 98s, AQo, KQo",
    "BTN": "JJ-22, AJs-A8s, KQs-K9s, QJs-Q9s, J9s+, T9s, 98s, 87s, AQo, AJo, KQo",
    "SB":  "QQ-22, AQs-ATs, KQs-KTs, QJs, JTs",
    "BB":  "JJ-22, AJs-A2s, KQs-K8s, QJs-Q8s, J8s+, T7s+, 97s+, 86s+, 75s+, 65s, 54s, AQo-ATo, KQo-KTo, QJo, JTo",
}
_VS_RAISE_3BET = {
    "UTG": "QQ+, AKs, AKo, A5s",
    "MP":  "QQ+, AKs, AKo, A5s, A4s",
    "CO":  "JJ+, AQs+, AKo, A5s-A4s, KQs",
    "BTN": "TT+, AQs+, AKo, A5s-A2s, KQs, KJs",
    "SB":  "QQ+, AKs, AKo, A5s-A3s, KQs",
    "BB":  "QQ+, AKs, AKo, A5s-A4s, KJs, KQs",
}
# Facing a 3-bet (I opened): 4-bet and call. Position-light (one generic each).
_VS_3BET_4BET = "KK+, AKs, A5s"
_VS_3BET_CALL = "QQ-TT, AQs, AJs, KQs, AKo"
# Facing a 4-bet: get-it-in / fold. (Call ~= commit at 100bb.)
_VS_4BET_CALL = "QQ+, AKs, AKo"

# --- Heads-up (n==2) deep ranges -------------------------------------------
_HU_SB_OPEN = ("22+, A2s+, A2o+, K2s+, K5o+, Q4s+, Q8o+, J6s+, J9o+, "
               "T6s+, T9o, 95s+, 85s+, 75s+, 64s+, 54s")
_HU_BB_3BET = "55+, A8s+, A9o+, KTs+, KJo+, QJs, A5s-A3s"
_HU_BB_CALL = ("22+, A2s+, A2o+, K2s+, K7o+, Q5s+, Q9o+, J7s+, J9o+, "
               "T7s+, T9o, 96s+, 86s+, 75s+, 65s, 54s")

# --- Nash-ish push/fold (eff<=15bb). Two tiers: <=10bb and 10-15bb ----------
_JAM = {
    "le10": {
        "UTG": "44+, A8s+, KTs+, QJs, ATo+, KQo",
        "MP":  "33+, A5s+, A9o+, KTs+, KQo, QJs",
        "CO":  "22+, A2s+, A7o+, K9s+, KTo+, Q9s+, QJo, J9s+, T9s, 98s",
        "BTN": "22+, A2s+, A2o+, K5s+, K9o+, Q8s+, QTo+, J8s+, JTo, T8s+, 97s+, 87s",
        "SB":  "22+, A2s+, A2o+, K6s+, K9o+, Q8s+, QTo+, J8s+, T8s+, 97s+",
    },
    "le15": {
        "UTG": "66+, ATs+, AQo+, KQs",
        "MP":  "55+, A9s+, AJo+, KQs, KJs",
        "CO":  "44+, A7s+, ATo+, KJs+, KQo, QJs",
        "BTN": "22+, A4s+, A8o+, K9s+, KTo+, Q9s+, J9s+, T9s",
        "SB":  "22+, A4s+, A8o+, K9s+, KTo+, Q9s+, JTs",
    },
}
# Calling an all-in while short (position-light).
_CALLJAM = {
    "le10": "44+, A7s+, A9o+, KTs+, KJo+, QJs",
    "le15": "77+, A9s+, AJo+, KQs",
}
# Heads-up short-stack push/fold (very wide).
_HU_JAM = {
    "le10": ("22+, A2s+, A2o+, K2s+, K6o+, Q4s+, Q9o+, J7s+, J9o+, "
             "T7s+, 97s+, 87s, 76s"),
    "le15": "22+, A2s+, A5o+, K7s+, KTo+, Q9s+, QJo, J9s+, T9s",
}
_HU_CALLJAM = {
    "le10": "22+, A2s+, A2o+, K5s+, K8o+, Q8s+, QTo+, J8s+, T8s+, 97s+",
    "le15": "44+, A5s+, A8o+, K9s+, KTo+, QTs+, JTs",
}


def _build_charts():
    """Expand all shorthand into frozensets once, at import time."""
    def m(d):
        return {k: _expand(v) for k, v in d.items()}
    return {
        "OPEN": m(_OPEN), "ISO": m(_ISO),
        "VS_RAISE_CALL": m(_VS_RAISE_CALL), "VS_RAISE_3BET": m(_VS_RAISE_3BET),
        "VS_3BET_4BET": _expand(_VS_3BET_4BET), "VS_3BET_CALL": _expand(_VS_3BET_CALL),
        "VS_4BET_CALL": _expand(_VS_4BET_CALL),
        "HU_SB_OPEN": _expand(_HU_SB_OPEN), "HU_BB_3BET": _expand(_HU_BB_3BET),
        "HU_BB_CALL": _expand(_HU_BB_CALL),
        "JAM": {t: m(_JAM[t]) for t in _JAM}, "CALLJAM": {t: _expand(_CALLJAM[t]) for t in _CALLJAM},
        "HU_JAM": {t: _expand(_HU_JAM[t]) for t in _HU_JAM},
        "HU_CALLJAM": {t: _expand(_HU_CALLJAM[t]) for t in _HU_CALLJAM},
    }


# Optionally load the same tables from data/preflop.json (numpy/json at import
# only) if a data/ dir was shipped; else use the inline-built charts. Both come
# from the SAME shorthand via tools/gen_preflop.py, so they're identical by
# construction — the inline build guarantees the single-file bot always works.
_CHARTS = _build_charts()
try:
    _dd = os.environ.get("BOT_DATA_DIR") or os.path.join(os.path.dirname(__file__), "data")
    _pf = os.path.join(_dd, "preflop.json")
    if os.path.isfile(_pf):
        import json as _json
        with open(_pf) as _fh:
            _raw = _json.load(_fh)
        # Rehydrate nested lists -> frozensets, mirroring _build_charts shape.
        def _fz(x):
            if isinstance(x, list):
                return frozenset(x)
            return {k: _fz(v) for k, v in x.items()}
        _CHARTS = _fz(_raw)
except Exception:
    pass  # any problem -> keep the inline charts (already built)


# --- Preflop situation derivation ------------------------------------------

def _hand_class(cards):
    """['As','Kh'] -> 'AKo'; ['Ts','Td'] -> 'TT'; high rank first."""
    hv = _hole_vals(cards)
    if hv is None:
        return None
    hi, lo, is_pair, is_suited = hv
    rh, rl = _RANK_ORDER[hi - 2], _RANK_ORDER[lo - 2]
    if is_pair:
        return rh + rh
    return rh + rl + ("s" if is_suited else "o")


def _pos_label(rel, n):
    """Seat label from its offset `rel` clockwise from the button (rel 0 = BTN).
    Fewer players => the first non-blind actor maps to a later (wider) label,
    which correctly widens ranges short-handed."""
    if n == 2:
        return "SB" if rel == 0 else "BB"   # HU: button is the small blind
    if rel == 0:
        return "BTN"
    if rel == 1:
        return "SB"
    if rel == 2:
        return "BB"
    if rel == n - 1:
        return "CO"
    if rel == n - 2:
        return "MP"
    return "UTG"


def _derive_button(p):
    """Find the button seat from the posted blinds in action_log."""
    n = len(p["players"])
    if n < 2:
        return None
    sb = bb = None
    for a in p["action_log"]:
        try:
            if a.get("action") == "small_blind":
                sb = a.get("seat")
            elif a.get("action") == "big_blind":
                bb = a.get("seat")
        except Exception:
            continue
    if n == 2:
        return sb            # HU: button == small blind
    if sb is not None:
        return sb - 1 if sb is not None else None  # button is seat before SB
    if bb is not None:
        return bb - 2
    return None


def _position(p):
    """Return (position_label, n_players) or None if it can't be derived."""
    n = len(p["players"])
    btn = _derive_button(p)
    if btn is None or p["seat"] < 0 or n < 2:
        return None
    rel = (p["seat"] - btn) % n
    return _pos_label(rel, n), n


def _facing_preflop(p):
    """Classify the action in front of us this (preflop) street.
    Returns (facing_str, n_limpers, facing_all_in)."""
    raises = 0
    limps = 0
    saw_all_in = False
    for a in p["action_log"]:
        act = a.get("action")
        if act in ("small_blind", "big_blind", "fold"):
            continue
        if act == "raise":
            raises += 1
        elif act == "all_in":
            raises += 1
            saw_all_in = True
        elif act == "call":
            if raises == 0:
                limps += 1     # a call before any raise = a limp
    if raises == 0:
        return ("vs_limp" if limps > 0 else "unopened"), limps, saw_all_in
    if raises == 1:
        return "vs_raise", limps, saw_all_in
    if raises == 2:
        return "vs_3bet", limps, saw_all_in
    return "vs_4bet", limps, saw_all_in


def _effective_bb(p):
    """Effective stack in big blinds: min(my total, biggest live opponent's
    total) / BB. Drives the push/fold gate."""
    my_total = p["stack"] + p["bet_this"]
    opp = []
    for pl in p["players"]:
        try:
            if pl.get("seat") == p["seat"] or pl.get("is_folded"):
                continue
            opp.append(int(pl.get("stack", 0)) + int(pl.get("bet_this_street", 0)))
        except Exception:
            continue
    eff = min(my_total, max(opp)) if opp else my_total
    return eff / float(_BB)


# --- Preflop sizing ---------------------------------------------------------

def _open_raise(p, limpers=0):
    target = max(p["min_raise_to"], int(_BB * 2.5) + limpers * _BB)
    return _make_raise(p, target)


def _threebet_raise(p):
    return _make_raise(p, max(p["min_raise_to"], p["current_bet"] * 3))


def _fourbet_raise(p):
    return _make_raise(p, max(p["min_raise_to"], int(p["current_bet"] * 2.3)))


# --- The chart decision -----------------------------------------------------

def _preflop_chart_action(p):
    """O(1) chart lookup. Returns a legal action dict, or None to defer to MC."""
    posn = _position(p)
    hc = _hand_class(p["your_cards"])
    if posn is None or hc is None:
        return None
    pos, n = posn
    hu = (n == 2)
    facing, limpers, facing_jam = _facing_preflop(p)
    eff = _effective_bb(p)

    # ---- Short stack: Nash-ish push/fold (eff <= 15bb) --------------------
    if eff <= 15.0:
        tier = "le10" if eff <= 10.0 else "le15"
        if facing in ("vs_raise", "vs_3bet", "vs_4bet") and facing_jam:
            calls = (_CHARTS["HU_CALLJAM"] if hu else _CHARTS["CALLJAM"])[tier]
            if hc in calls:
                return _call_or_check(p)
            return _check_or_fold(p)
        # Unopened, limped, or facing a non-all-in raise while short -> jam/fold.
        if hu:
            jam = _CHARTS["HU_JAM"][tier]
        else:
            jam = _CHARTS["JAM"][tier].get(pos, frozenset())
        if hc in jam:
            return {"action": "all_in"}
        return _check_or_fold(p)

    # ---- Deep (>15bb): sized open / call / 3-bet / 4-bet -----------------
    if facing == "unopened":
        if hu and pos == "SB":
            if hc in _CHARTS["HU_SB_OPEN"]:
                return _open_raise(p)
            return _check_or_fold(p)
        rng = _CHARTS["OPEN"].get(pos)
        if rng is not None and hc in rng:
            return _open_raise(p)
        return _check_or_fold(p)

    if facing == "vs_limp":
        if hc in _CHARTS["ISO"].get(pos, frozenset()):
            return _open_raise(p, limpers=limpers)
        return _check_or_fold(p)   # take the free flop (BB) or fold

    if facing == "vs_raise":
        if hu and pos == "BB":
            tb, ca = _CHARTS["HU_BB_3BET"], _CHARTS["HU_BB_CALL"]
        else:
            tb, ca = _CHARTS["VS_RAISE_3BET"].get(pos, frozenset()), \
                     _CHARTS["VS_RAISE_CALL"].get(pos, frozenset())
        if hc in tb:
            return _threebet_raise(p)
        if hc in ca:
            return _call_or_check(p)
        return _check_or_fold(p)

    if facing == "vs_3bet":
        if hc in _CHARTS["VS_3BET_4BET"]:
            return _fourbet_raise(p)
        if hc in _CHARTS["VS_3BET_CALL"]:
            return _call_or_check(p)
        return _check_or_fold(p)

    if facing == "vs_4bet":
        if hc in _CHARTS["VS_4BET_CALL"]:
            return _call_or_check(p)
        return _check_or_fold(p)

    return None


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

# --- Phase 4a: postflop SPR pot control (one-pair over-commitment guard) ----
# Effective stack-to-pot ratio governs commitment. Low SPR = already pot-
# committed (call off fine); high SPR = deep, so a lone weak pair must NOT stack
# off — vs-random equity over-rates one pair facing raise-over-raise (the K7o
# leak). This caps only how DEEP we go with one pair; it does NOT widen our
# folds to a single bet (the field over-bluffs, so bluff-catching stays +EV;
# blanket tightening measured -EV in Phase 2).
_SPR_COMMIT    = 1.5    # SPR at/below this = committed; a lone weak pair may call off
_STACKOFF_FRAC = 0.6    # a call >= this fraction of remaining stack ~= stacking off
_STACKOFF_EQ   = 0.85   # only commit a lone weak pair for stacks at/above this equity


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


def _spr(p):
    """Effective stack-to-pot ratio. Uses REMAINING stacks (min of mine and the
    largest live opponent's) over the current pot. Low => committed; high =>
    deep. Returns a large number when the pot is empty (pre-betting)."""
    pot = p["pot"]
    if pot <= 0:
        return 999.0
    opp = []
    for pl in p["players"]:
        try:
            if pl.get("seat") == p["seat"] or pl.get("is_folded"):
                continue
            opp.append(int(pl.get("stack", 0)))
        except Exception:
            continue
    eff = min(p["stack"], max(opp)) if opp else p["stack"]
    return eff / float(pot)


def _pair_class(cards, board):
    """Refine a one-pair hand into a commitment tier. MEDIUM = overpair or
    top-pair-good-kicker (fine to play for stacks at low SPR); MARGINAL =
    anything weaker — 2nd pair, under/weak pair, top-pair-weak-kicker — which
    must pot-control. Falls back to MARGINAL on any ambiguity (safe: caps us)."""
    r0, r1 = _RANK_VAL.get(cards[0][0]), _RANK_VAL.get(cards[1][0])
    bvals = [_RANK_VAL.get(c[0]) for c in board]
    bvals = [b for b in bvals if b is not None]
    if r0 is None or r1 is None or not bvals:
        return "MARGINAL"
    top_board = max(bvals)
    if r0 == r1:                                  # pocket pair: over- or under-pair
        return "MEDIUM" if r0 > top_board else "MARGINAL"
    hi, lo = max(r0, r1), min(r0, r1)
    paired = [r for r in (r0, r1) if r in bvals]  # which hole rank hit the board
    if not paired:
        return "MARGINAL"                         # only the board is paired -> weak
    pr = max(paired)
    kicker = hi if pr != hi else lo               # the unpaired hole card
    higher = len({b for b in bvals if b > pr})    # distinct board ranks beating ours
    if higher == 0:                               # top pair
        return "MEDIUM" if kicker >= _RANK_VAL["J"] else "MARGINAL"
    return "MARGINAL"                             # 2nd pair or worse


def _made_hand_class(p):
    """Postflop made-hand class: STRONG (two pair+), MEDIUM / MARGINAL (one
    pair, refined by _pair_class), or WEAK (high card). Returns None when it
    can't be computed (no eval7, malformed cards, <3 board) so the caller
    defers to the pure equity logic — class can only add information."""
    cards, board = p["your_cards"], p["community"]
    if not _HAVE_EVAL7 or len(cards) < 2 or len(board) < 3:
        return None
    try:
        ev = [_CARD_BY_STR[c] for c in cards] + [_CARD_BY_STR[c] for c in board]
    except KeyError:
        return None
    ht = eval7.handtype(eval7.evaluate(ev))
    if ht == "High Card":
        return "WEAK"
    if ht == "Pair":
        return _pair_class(cards, board)
    return "STRONG"   # Two Pair, Trips, Straight, Flush, Full House, Quads, SF


def _postflop_overcommit_guard(p, eq):
    """SUBTRACTIVE pot-control guard for the one-pair over-commitment leak.

    Returns a SAFER action ONLY when the baseline equity logic would commit a
    lone weak pair too deep (the K7o leak: vs-random equity over-rates one pair
    facing raise-over-raise); otherwise returns None to keep baseline behavior
    untouched. It can only downgrade raise->call or call-off->fold — it NEVER
    adds a bet/raise baseline wouldn't make, so it cannot increase our variance
    or create new stack-offs. Value-betting / bluff-catching the over-folding,
    over-bluffing field is left fully intact (blanket tightening measured -EV)."""
    if _made_hand_class(p) != "MARGINAL":
        return None                         # only lone weak pairs are capped
    if p["can_check"]:
        return None                         # no bet to us -> baseline value-bets
    # Facing a bet with a lone weak pair.
    overcommit = p["owed"] >= _STACKOFF_FRAC * max(1, p["stack"])
    if overcommit and _spr(p) > _SPR_COMMIT and eq < _STACKOFF_EQ:
        return {"action": "fold"}           # don't stack off one pair when deep
    if eq >= _VALUE_RAISE_EQ:
        return _call_or_check(p)            # don't value-RAISE one pair into aggression
    return None                             # else: baseline (call if priced, else fold)


def _strategy_action(p, deadline):
    """Equity vs pot odds. Bet/raise strong equity, call correct-priced draws,
    fold the rest. Deliberately simple — the exploit overlay rides on top later."""
    # Cheap exits that never need MC.
    if len(p["your_cards"]) < 2:
        return _fallback_action(p)

    # Phase 3: preflop is an O(1) chart lookup. Covered spots return here with
    # ~zero compute; uncovered spots (e.g. odd states with no blind info) return
    # None and fall through to the Monte-Carlo path below. Charts can only add
    # information, never reduce coverage.
    if p["street"] == "preflop":
        chart = _preflop_chart_action(p)
        if chart is not None:
            return chart

    if not _HAVE_EVAL7:
        return _fallback_action(p)
    if time.monotonic() >= deadline:
        return _fallback_action(p)

    n_opp = _num_live_opponents(p)
    iters = max(200, _BASE_ITERS.get(p["street"], 1200) // n_opp)
    eq = monte_carlo_equity(p["your_cards"], p["community"],
                            [None] * n_opp, iters, deadline)
    req = _required_equity(p)

    # Phase 4a: a subtractive pot-control guard intercepts ONLY the one-pair
    # over-commitment leak; everything else falls through to the baseline equity
    # logic below (which also serves preflop spots the charts didn't cover).
    if p["street"] != "preflop":
        guard = _postflop_overcommit_guard(p, eq)
        if guard is not None:
            return guard

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
