"""Evaluate an arbitrary field over N seeds (deterministic, seeded). Reports
per-bot mean / median / busts / table-wins. Reused by the regression gate."""
import sys, statistics as st
sys.path.insert(0, "tools")
from harness import run_field

# 6-max field with a NON-exploitable, independently-coded competitor swapped in
# for the redundant ref_bot_2 rock: a real test of the bot vs someone it didn't
# write and wasn't tuned against.
HONEST_FIELD = [
    "bots/mybot/bot.py",
    "tools/refbots_seeded/aggressor.py",
    "bots/mathematician/bot.py",
    "tools/refbots_seeded/shark.py",
    "bots/honest_equity/bot.py",
    "bots/template/bot.py",
]

def evaluate(field, seeds):
    ids = None
    deltas = {}
    wins = {}
    for s in seeds:
        cd = run_field(field, s)
        if ids is None:
            ids = list(cd.keys())
            deltas = {b: [] for b in ids}
            wins = {b: 0 for b in ids}
        top = max(cd.values())
        for b in ids:
            deltas[b].append(cd[b])
            if cd[b] == top:
                wins[b] += 1
    n = len(list(seeds))
    rows = []
    for b in ids:
        d = deltas[b]
        rows.append((b, st.mean(d), st.median(d), sum(1 for v in d if v == -10000), wins[b]))
    rows.sort(key=lambda r: -r[1])
    print(f"{'bot':16}{'mean':>9}{'median':>9}{'busts':>7}{'wins':>6}   (n={n})")
    for b, m, md, bu, w in rows:
        print(f"{b:16}{m:>+9.0f}{md:>+9.0f}{bu:>7}{w:>6}")
    return deltas

if __name__ == "__main__":
    evaluate(HONEST_FIELD, range(1, 201))
