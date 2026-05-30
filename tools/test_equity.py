"""
Standalone unit test for the Monte-Carlo equity engine in bots/mybot/bot.py.

Run:  python3 tools/test_equity.py

Checks hand-calculable spots against known textbook equities. This is the
correctness gate the Phase-2 equity core must pass before it replaces Phase 1.
We seed the RNG for reproducibility and use high iteration counts (this is an
offline test, not subject to the 2s in-game budget).
"""

import os
import random
import sys

# Import the bot module directly from bots/mybot/bot.py.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bots", "mybot"))
import bot  # noqa: E402

ITERS = 120_000


def pct(x):
    return f"{100.0 * x:.2f}%"


def run(name, hero, board, opponents, lo, hi):
    random.seed(12345)  # reproducible
    eq = bot.monte_carlo_equity(hero, board, opponents, ITERS)
    ok = lo <= eq <= hi
    status = "PASS" if ok else "FAIL"
    opp_desc = ", ".join("random" if o is None else "".join(o) for o in opponents)
    print(f"[{status}] {name}")
    print(f"        hero={hero}  board={board or '(preflop)'}  vs [{opp_desc}]")
    print(f"        equity = {pct(eq)}   (expected {pct(lo)}–{pct(hi)})")
    return ok


def main():
    print(f"Monte-Carlo equity unit test  ({ITERS:,} iters/spot, eval7={bot._HAVE_EVAL7})\n")
    results = []

    # 1) AA vs KK preflop all-in. Textbook ~82% for the aces. Suits chosen to
    #    avoid both pairs sharing a suit (keeps it the canonical number).
    results.append(run(
        "AA vs KK preflop (textbook ~82%)",
        ["As", "Ah"], [], [["Ks", "Kh"]],
        lo=0.79, hi=0.84,
    ))

    # 2) Made nut flush on the river vs one random opponent. Board is complete,
    #    so no runout — only the opponent's hole cards are sampled. The nut flush
    #    only loses to a full house / quads / straight flush that the river board
    #    allows; on this unpaired board that's a rare straight-flush-only beat,
    #    so equity should be 95%+.
    #    Hero AsKs makes the nut (ace-high) spade flush on As? no — hero holds the
    #    Ace and King of spades; board has three more spades.
    results.append(run(
        "Nut flush on river vs 1 random (>=95%)",
        ["As", "Ks"], ["Qs", "7s", "2s", "8d", "3h"], [None],
        lo=0.95, hi=1.00,
    ))

    # 3) Two overcards vs an under-pair, preflop — the classic "race" ~ coinflip.
    #    AKo vs 22: the pair is a slight favourite, so the OVERCARDS land ~46–48%.
    results.append(run(
        "Overcards AKo vs pair 22 preflop (~47%, a coinflip)",
        ["Ac", "Kd"], [], [["2c", "2h"]],
        lo=0.44, hi=0.52,
    ))

    # 4) Sanity extras (not required by the brief, but cheap confidence):
    #    AA vs one random hand preflop ~85%.
    results.append(run(
        "AA vs 1 random preflop (~85%)",
        ["As", "Ah"], [], [None],
        lo=0.83, hi=0.87,
    ))
    #    AA vs FIVE random hands preflop drops to ~49% at showdown.
    results.append(run(
        "AA vs 5 randoms preflop (~49% showdown)",
        ["As", "Ah"], [], [None] * 5,
        lo=0.44, hi=0.54,
    ))

    print()
    passed = sum(results)
    total = len(results)
    print(f"==> {passed}/{total} spots within expected range")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
