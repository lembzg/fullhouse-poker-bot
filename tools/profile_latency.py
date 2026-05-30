"""
Worst-case latency profiler for decide().

Run:  python3 tools/profile_latency.py

The tournament gives each bot 0.5 of a CPU core and a 2s hard deadline. This
script times decide() on the heaviest realistic states at full speed, then
(a) reports mean / p99 / max, (b) applies a conservative 2x factor to estimate
the 0.5-core wall time, and (c) proves the internal time budget actually caps a
deliberately oversized MC run.
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bots", "mybot"))
import bot  # noqa: E402


def _players(n, hero_seat=0, hero_bet=0, opp_bet=0):
    pls = []
    for s in range(n):
        pls.append({
            "seat": s, "bot_id": ("hero" if s == hero_seat else "opp%d" % s),
            "stack": 10000, "state": "active", "is_folded": False,
            "is_all_in": False,
            "bet_this_street": hero_bet if s == hero_seat else opp_bet,
            "hole_cards": None,
        })
    return pls


# Heaviest realistic spots. Single-opponent streets run the MOST MC iters
# (adaptive count is divided by opponent count), so heads-up river/turn are the
# worst case for total eval7 calls.
STATES = {
    "river HU, facing bet (max iters)": {
        "type": "action_request", "street": "river", "seat_to_act": 0,
        "pot": 4000, "community_cards": ["Qs", "7s", "2s", "8d", "3h"],
        "current_bet": 2000, "min_raise_to": 4000, "amount_owed": 2000,
        "can_check": False, "your_cards": ["As", "Ks"], "your_stack": 6000,
        "your_bet_this_street": 0, "players": _players(2, opp_bet=2000),
        "action_log": [],
    },
    "turn HU, checked to": {
        "type": "action_request", "street": "turn", "seat_to_act": 0,
        "pot": 1200, "community_cards": ["Qs", "7s", "2d", "8d"],
        "current_bet": 0, "min_raise_to": 100, "amount_owed": 0,
        "can_check": True, "your_cards": ["Ah", "Qd"], "your_stack": 9000,
        "your_bet_this_street": 0, "players": _players(2),
        "action_log": [],
    },
    "flop 6-way, facing bet": {
        "type": "action_request", "street": "flop", "seat_to_act": 0,
        "pot": 900, "community_cards": ["Qs", "7s", "2d"],
        "current_bet": 300, "min_raise_to": 600, "amount_owed": 300,
        "can_check": False, "your_cards": ["Ah", "Qd"], "your_stack": 9700,
        "your_bet_this_street": 0, "players": _players(6, opp_bet=300),
        "action_log": [],
    },
    "preflop 6-way, facing raise": {
        "type": "action_request", "street": "preflop", "seat_to_act": 0,
        "pot": 450, "community_cards": [],
        "current_bet": 300, "min_raise_to": 500, "amount_owed": 300,
        "can_check": False, "your_cards": ["Ah", "Ks"], "your_stack": 9700,
        "your_bet_this_street": 0, "players": _players(6, opp_bet=300),
        "action_log": [],
    },
}

REPS = 50


def main():
    print("decide() latency profile  (full speed; REPS=%d per state)\n" % REPS)
    overall_max = 0.0
    for name, st in STATES.items():
        # warm once (first call touches eval7 C paths)
        bot.decide(st)
        times = []
        for _ in range(REPS):
            t0 = time.perf_counter()
            bot.decide(st)
            times.append(time.perf_counter() - t0)
        times.sort()
        mean = sum(times) / len(times)
        p99 = times[int(0.99 * (len(times) - 1))]
        mx = times[-1]
        overall_max = max(overall_max, mx)
        print("  %-34s mean=%6.1fms  p99=%6.1fms  max=%6.1fms  ~0.5core(2x max)=%6.1fms"
              % (name, mean * 1e3, p99 * 1e3, mx * 1e3, mx * 2e3))

    print("\n  WORST observed (full speed): %.1f ms  ->  ~%.1f ms at 0.5 core (2x)"
          % (overall_max * 1e3, overall_max * 2e3))
    print("  Hard engine deadline: 2000 ms   Internal budget: %.0f ms"
          % (bot.TIME_BUDGET_S * 1000))

    # ---- Prove the time budget actually caps an oversized MC run ----
    print("\n  Budget-guard stress test: request 50,000,000 MC iters with a 1.2s deadline")
    hero, board = ["As", "Ks"], ["Qs", "7s", "2s", "8d", "3h"]
    t0 = time.perf_counter()
    deadline = time.monotonic() + 1.2
    bot.monte_carlo_equity(hero, board, [None], 50_000_000, deadline)
    elapsed = time.perf_counter() - t0
    print("    elapsed = %.3fs (must be ~1.2s, NOT minutes) -> guard %s"
          % (elapsed, "WORKS" if elapsed < 1.6 else "FAILED"))

    # ---- Raw MC throughput, to reason about CPU throttling ----
    t0 = time.perf_counter()
    bot.monte_carlo_equity(hero, board, [None], 50_000)
    dt = time.perf_counter() - t0
    print("\n  Raw throughput: 50k river HU iters in %.3fs = %.0f k iters/s (full speed)"
          % (dt, 50.0 / dt))


if __name__ == "__main__":
    main()
