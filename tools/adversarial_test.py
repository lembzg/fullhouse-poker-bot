"""
Adversarial-state + latency verification for the submitted bot (downside-only:
proves the artifact never throws, never returns an illegal action, and never
approaches the 2s auto-fold cliff). Pure verification — it does not change play.

Run it under the PRODUCTION interpreter (Python 3.10) for a true check:
    python3 tools/adversarial_test.py [bots/mybot/bot.py] [--hogs N]

  --hogs N : spawn N busy CPU processes during the latency measurement to
             emulate a throttled core (cpulimit is a no-op on Apple Silicon).
             On an 8-core box, N≈15 throttles the test to ~0.5 core or harsher.

Exit code 0 = ALL CLEAN, 1 = a state threw / returned illegal / overran budget.
"""
import argparse
import importlib.util
import os
import statistics as st
import subprocess
import sys
import time
import traceback

VALID = {"fold", "check", "call", "raise", "all_in"}
HARD_LIMIT_MS = 2000.0
BUDGET_S = 1.2


def load_bot(path):
    spec = importlib.util.spec_from_file_location("bot_under_test", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def legal(a):
    if not isinstance(a, dict):
        return False, "not a dict"
    act = a.get("action")
    if act not in VALID:
        return False, "bad action %r" % (act,)
    if act == "raise" and not isinstance(a.get("amount"), int):
        return False, "raise without int amount"
    return True, act


def _base(**kw):
    s = {"type": "action_request", "street": "flop", "your_cards": ["As", "Kd"],
         "community_cards": ["2c", "7h", "Td"], "pot": 300, "your_stack": 9000,
         "amount_owed": 100, "can_check": False, "current_bet": 100,
         "your_bet_this_street": 0, "min_raise_to": 200, "seat_to_act": 0,
         "action_log": [{"seat": 1, "action": "small_blind", "amount": 50},
                        {"seat": 2, "action": "big_blind", "amount": 100}],
         "players": [{"seat": 0, "bot_id": "mybot", "stack": 9000, "is_folded": False, "bet_this_street": 0},
                     {"seat": 1, "bot_id": "x", "stack": 9000, "is_folded": False, "bet_this_street": 50},
                     {"seat": 2, "bot_id": "y", "stack": 9000, "is_folded": False, "bet_this_street": 100}],
         "match_action_log": []}
    s.update(kw)
    return s


HOSTILE = [
    ("empty dict", {}), ("None state", None), ("list not dict", [1, 2, 3]),
    ("warmup", {"type": "warmup"}), ("unknown type", {"type": "hand_complete", "winners": []}),
    ("only street key", {"street": "turn"}), ("empty action_log", _base(action_log=[])),
    ("empty community on flop", _base(community_cards=[])), ("None cards", _base(your_cards=[None, None])),
    ("garbage cards", _base(your_cards=["XX", "ZZ"], community_cards=["1", "??", "Td"])),
    ("one hole card", _base(your_cards=["As"])),
    ("seven community", _base(community_cards=["2c", "7h", "Td", "Js", "Qd", "Kh", "Ac"])),
    ("negative pot", _base(pot=-500)), ("zero pot owed>0", _base(pot=0)),
    ("stack of 1 chip", _base(your_stack=1, amount_owed=100)),
    ("owed > stack", _base(amount_owed=99999, your_stack=100, current_bet=99999)),
    ("players not a list", _base(players="nope")), ("players with None", _base(players=[None, {"seat": 1}])),
    ("players junk fields", _base(players=[{"seat": 0, "bot_id": None, "stack": "x", "is_folded": "maybe", "bet_this_street": None}])),
    ("None numerics", _base(pot=None, your_stack=None, amount_owed=None, current_bet=None, min_raise_to=None)),
    ("seat garbage str", _base(seat_to_act="x")), ("seat -999", _base(seat_to_act=-999)),
    ("can_check true but owed", _base(can_check=True, amount_owed=100)),
    ("min_raise_to garbage", _base(min_raise_to="big")),
    ("preflop no community", _base(street="preflop", community_cards=[], your_cards=["As", "Ah"])),
    ("everything None-ish", {"type": "action_request", "your_cards": None, "community_cards": None,
                             "players": None, "action_log": None, "pot": None, "amount_owed": None}),
]

TRICKY = [
    ("HU SB to act preflop (owes 50)", _base(street="preflop", community_cards=[], amount_owed=50,
        current_bet=100, min_raise_to=200, your_stack=9950, your_bet_this_street=50, players=[
        {"seat": 0, "bot_id": "mybot", "stack": 9950, "is_folded": False, "bet_this_street": 50},
        {"seat": 1, "bot_id": "x", "stack": 9900, "is_folded": False, "bet_this_street": 100}],
        action_log=[{"seat": 0, "action": "small_blind", "amount": 50}, {"seat": 1, "action": "big_blind", "amount": 100}])),
    ("all-in-for-less (call 30 with 30)", _base(amount_owed=30, your_stack=30, pot=500, current_bet=130, your_bet_this_street=100)),
    ("facing bet bigger than stack", _base(amount_owed=5000, your_stack=200, current_bet=5000)),
    ("short-stack HU strong (AA, 1.5bb)", _base(street="preflop", community_cards=[], your_cards=["As", "Ah"],
        your_stack=150, amount_owed=50, current_bet=100, min_raise_to=200, players=[
        {"seat": 0, "bot_id": "mybot", "stack": 150, "is_folded": False, "bet_this_street": 50},
        {"seat": 1, "bot_id": "x", "stack": 9850, "is_folded": False, "bet_this_street": 100}],
        action_log=[{"seat": 0, "action": "small_blind", "amount": 50}, {"seat": 1, "action": "big_blind", "amount": 100}])),
    ("multiway side-pot (one all-in)", _base(amount_owed=4000, current_bet=4000, pot=10050, your_stack=4000, players=[
        {"seat": 0, "bot_id": "mybot", "stack": 4000, "is_folded": False, "bet_this_street": 0},
        {"seat": 1, "bot_id": "a", "stack": 0, "is_folded": False, "bet_this_street": 4000, "is_all_in": True},
        {"seat": 2, "bot_id": "b", "stack": 2000, "is_folded": False, "bet_this_street": 4000},
        {"seat": 3, "bot_id": "c", "stack": 9000, "is_folded": True, "bet_this_street": 0}])),
    ("can only all-in (min_raise_to>max)", _base(current_bet=8000, amount_owed=8000, your_stack=5000, min_raise_to=16000)),
]


def _worst_case_state():
    return _base(street="river", your_cards=["As", "Ks"],
                 community_cards=["Qs", "Js", "2h", "7d", "9c"], pot=2000, your_stack=8000,
                 amount_owed=1000, current_bet=1000, min_raise_to=2000,
                 players=[{"seat": 0, "bot_id": "mybot", "stack": 8000, "is_folded": False, "bet_this_street": 0},
                          {"seat": 1, "bot_id": "o", "stack": 8000, "is_folded": False, "bet_this_street": 1000}],
                 action_log=[{"seat": 1, "action": "raise", "amount": 1000}])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bot", nargs="?", default="bots/mybot/bot.py")
    ap.add_argument("--hogs", type=int, default=0)
    ap.add_argument("--calls", type=int, default=300)
    args = ap.parse_args()

    bot = load_bot(args.bot)
    print("bot: %s | python %s | eval7 %s" % (args.bot, sys.version.split()[0], getattr(bot, "_HAVE_EVAL7", "?")))
    fails = 0

    print("\n(1) hostile / malformed states:")
    for label, stt in HOSTILE:
        try:
            a = bot.decide(stt); ok, info = legal(a)
        except Exception:
            ok, info = False, "THREW: " + traceback.format_exc().splitlines()[-1]
        fails += not ok
        print("  [%-7s] %-34s %s" % ("OK" if ok else "FAIL", label, "" if ok else info))

    print("\n(2) tricky-but-legal spots:")
    for label, stt in TRICKY:
        try:
            a = bot.decide(stt); ok, info = legal(a)
        except Exception:
            a, ok, info = None, False, "THREW"
        fails += not ok
        print("  [%-7s] %-34s -> %s" % ("OK" if ok else "FAIL", label, a))

    print("\n(3) latency + budget guard%s:" % (" (under %d CPU hogs)" % args.hogs if args.hogs else " (full speed)"))
    hogs = []
    try:
        for _ in range(args.hogs):
            hogs.append(subprocess.Popen([sys.executable, "-c", "while True: pass"]))
        if hogs:
            time.sleep(1)
        s = _worst_case_state(); ts = []
        for _ in range(args.calls):
            t = time.perf_counter(); bot.decide(s); ts.append((time.perf_counter() - t) * 1000)
        ts.sort()
        mx = ts[-1]; p99 = ts[int(0.99 * len(ts)) - 1]
        print("  worst-case decide() x%d: median=%.1fms p99=%.1fms MAX=%.1fms (margin to 2s cliff=%.0fms)"
              % (args.calls, st.median(ts), p99, mx, HARD_LIMIT_MS - mx))
        t = time.monotonic()
        bot.monte_carlo_equity(["As", "Ks"], ["Qs", "Js", "2h"], [None], 50_000_000, deadline=t + BUDGET_S)
        el = time.monotonic() - t
        guard_ok = el < 1.8
        fails += not guard_ok
        print("  budget guard (50M iters, %.1fs deadline): elapsed=%.3fs -> %s"
              % (BUDGET_S, el, "HOLDS" if guard_ok else "OVERRUN"))
        if mx >= HARD_LIMIT_MS:
            fails += 1
            print("  FAIL: a single decision exceeded the 2s hard limit")
    finally:
        for h in hogs:
            h.kill()

    print("\nRESULT:", "ALL CLEAN" if fails == 0 else "%d FAILURE(S)" % fails)
    sys.exit(0 if fails == 0 else 1)


if __name__ == "__main__":
    main()
