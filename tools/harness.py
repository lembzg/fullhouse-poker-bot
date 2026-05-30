"""
Reproducible match harness for regression testing.

Sets BOT_RNG_SEED in the child environment so mybot's Monte Carlo is
deterministic, and the engine --seed fixes the deck. Together, (engine_seed,
bot_seed) reproduces a match exactly. Reused by the Phase 6 test suite.

Usage as a library:
    from tools.harness import run_field, REF_FIELD
    deltas = run_field(REF_FIELD, engine_seed=1)         # dict bot_id -> chip delta

CLI:
    python3 tools/harness.py            # 30-seed summary vs the reference field
    python3 tools/harness.py --check    # prove determinism (same seeds twice)
"""

import json
import os
import subprocess
import sys
import statistics as st

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MYBOT = "bots/mybot/bot.py"
# The real benchmark field: mybot + the 5 DISTINCT reference bots. Never seat a
# clone of mybot here — two strong bots cannibalise one table and inflate
# apparent variance (see Phase 2 bust analysis).
#
# aggressor and shark are the only stochastic reference bots; we use SEEDED test
# fixtures (tools/refbots_seeded/) so a full match is bit-reproducible given
# (engine_seed, BOT_RNG_SEED). The other three refs are already deterministic.
# Pass live=True to swap in the canonical (stochastic) versions for a
# realism check.
_AGGRO_SEEDED = "tools/refbots_seeded/aggressor.py"
_SHARK_SEEDED = "tools/refbots_seeded/shark.py"
REF_FIELD = [
    MYBOT,
    _AGGRO_SEEDED,
    "bots/mathematician/bot.py",
    _SHARK_SEEDED,
    "bots/ref_bot_2/bot.py",
    "bots/template/bot.py",
]
LIVE_FIELD = [
    MYBOT,
    "bots/aggressor/bot.py",
    "bots/mathematician/bot.py",
    "bots/shark/bot.py",
    "bots/ref_bot_2/bot.py",
    "bots/template/bot.py",
]


def run_field(field, engine_seed, bot_seed=None, hands=400):
    """Run one match; return {bot_id: chip_delta}. Deterministic given
    (engine_seed, bot_seed). bot_seed defaults to engine_seed."""
    if bot_seed is None:
        bot_seed = engine_seed
    env = dict(os.environ)
    env["BOT_RNG_SEED"] = str(bot_seed)
    out = subprocess.run(
        [sys.executable, "sandbox/match.py", *field,
         "--hands", str(hands), "--seed", str(engine_seed), "--json"],
        capture_output=True, text=True, cwd=ROOT, env=env,
    )
    if out.returncode != 0:
        raise RuntimeError("match failed: " + out.stderr[-500:])
    return json.loads(out.stdout)["chip_delta"]


def summarize(field, seeds, hero="mybot", label="field"):
    deltas, wins = [], 0
    for s in seeds:
        cd = run_field(field, s)
        deltas.append(cd[hero])
        if cd[hero] == max(cd.values()):
            wins += 1
    n = len(deltas)
    print(f"[{label}] {hero} over {n} seeds:")
    print(f"  mean={st.mean(deltas):+.0f}  median={st.median(deltas):+.0f}  "
          f"stdev={st.pstdev(deltas):.0f}")
    print(f"  table-wins={wins}/{n}  busts={sum(1 for v in deltas if v==-10000)}/{n}  "
          f"min={min(deltas)}  max={max(deltas)}")
    return deltas


def main():
    if "--check" in sys.argv:
        # Determinism proof: identical seeds must yield identical deltas.
        a = run_field(REF_FIELD, engine_seed=10)
        b = run_field(REF_FIELD, engine_seed=10)
        ok = a == b
        print("Run A:", a)
        print("Run B:", b)
        print("DETERMINISTIC:" , "YES" if ok else "NO — results differ")
        sys.exit(0 if ok else 1)

    summarize(REF_FIELD, range(1, 31), label="reference field")


if __name__ == "__main__":
    main()
