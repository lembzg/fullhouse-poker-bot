"""
Offline preflop-chart generator (Phase 3).

The bot builds its preflop charts inline at import from compact shorthand (the
reviewable source of truth in bot.py). This script imports that same shorthand,
expands it, and writes bots/mybot/data/preflop.json — the numpy/json artifact
for the optional .zip submission format. bot.py prefers data/preflop.json when a
data/ dir is shipped, and falls back to the identical inline build otherwise, so
the two can never drift (both come from here).

Run:  python3 tools/gen_preflop.py
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bots", "mybot"))
import bot  # noqa: E402


def jsonify(charts):
    """frozensets -> sorted lists, recursively, for stable JSON output."""
    if isinstance(charts, (frozenset, set)):
        return sorted(charts)
    return {k: jsonify(v) for k, v in charts.items()}


def main():
    charts = bot._build_charts()
    out = jsonify(charts)

    data_dir = os.path.join(HERE, "..", "bots", "mybot", "data")
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, "preflop.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=0, sort_keys=True)

    # Report hand counts per range so the tables are auditable at a glance.
    print("Wrote", os.path.relpath(path))
    print("\nRange sizes (hand classes; 169 = every hand):")
    for pos, hs in sorted(out["OPEN"].items()):
        print(f"  OPEN[{pos}]: {len(hs)}")
    for tier in ("le10", "le15"):
        for pos, hs in sorted(out["JAM"][tier].items()):
            print(f"  JAM[{tier}][{pos}]: {len(hs)}")
    print(f"  HU_SB_OPEN: {len(out['HU_SB_OPEN'])}   HU_BB_CALL: {len(out['HU_BB_CALL'])}")

    # Spot checks: a few hands must / must not be in specific ranges.
    def must(cond, msg):
        print(("  OK  " if cond else "  FAIL") + " " + msg)
        return cond
    ok = True
    ok &= must("AA" in charts["OPEN"]["UTG"], "AA opens UTG")
    ok &= must("72o" not in charts["OPEN"]["UTG"], "72o does NOT open UTG")
    ok &= must("72o" not in charts["HU_SB_OPEN"], "72o folds even on HU button")
    ok &= must(len(charts["HU_SB_OPEN"]) > 2 * len(charts["OPEN"]["UTG"]),
               "HU button opens much wider than UTG")
    ok &= must("AKo" in charts["VS_3BET_CALL"] or "AKo" in charts["VS_3BET_4BET"],
               "AKo continues vs 3bet")
    ok &= must("KK" in charts["VS_4BET_CALL"], "KK gets it in vs 4bet")
    ok &= must("22" in charts["JAM"]["le10"]["BTN"], "22 jams <=10bb on BTN")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
