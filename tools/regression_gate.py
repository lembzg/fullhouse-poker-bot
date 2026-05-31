"""
Regression gate — a runnable accept/reject check for any change to mybot.

A candidate bot.py is ACCEPTED only if BOTH hold vs the previous git HEAD's bot:
  (A) head-to-head: in a heads-up duel (2-max) over N seeds, the candidate's
      mean chip delta is >= 0 (it does not lose to the bot it's replacing);
  (B) field: in the 6-max seeded reference field, the candidate's 200-seed
      PAIRED mean delta vs HEAD is neutral-or-better — i.e. the 95% CI does not
      exclude zero on the downside (no field regression).

Otherwise it is REJECTED. Deterministic (seeded harness + BOT_RNG_SEED).

Usage:
    python3 tools/regression_gate.py bots/mybot/bot.py [--seeds 200]
    # compares the given candidate against HEAD:bots/mybot/bot.py
Exit code 0 = ACCEPT, 1 = REJECT (so it can gate CI / pre-commit hooks).
"""
import argparse
import math
import os
import subprocess
import sys
import statistics as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import run_field, REF_FIELD       # noqa: E402
from eval_field import HONEST_FIELD             # noqa: E402  (field w/ a competent contester)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _head_bot_tmp():
    """Materialise HEAD:bots/mybot/bot.py to a temp file."""
    out = subprocess.run(["git", "show", "HEAD:bots/mybot/bot.py"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError("git show failed: " + out.stderr[-300:])
    path = os.path.join(ROOT, "bots", "mybot", "_head_ref_tmp.py")
    with open(path, "w") as fh:
        fh.write(out.stdout)
    return path


def _field_with(field, mybot_path):
    return [mybot_path if f.endswith("mybot/bot.py") else f for f in field]


def _key(path):
    # mybot/bot.py -> 'mybot'; _head_ref_tmp.py -> '_head_ref_tmp'
    base = os.path.basename(path)
    return os.path.basename(os.path.dirname(path)) if base == "bot.py" else base[:-3]


def head_to_head(cand_path, head_path, seeds):
    """2-max duel; returns (mean, lo95, hi95) of the candidate's chip delta
    (zero-sum). Alternates seat order by seed parity so neither bot gets a
    systematic button/position edge (with identical bots this averages to ~0).
    HU swings are huge, so we judge it by the CI, not a hard threshold."""
    ck = _key(cand_path)
    diffs = []
    for s in seeds:
        field = [cand_path, head_path] if (s % 2 == 0) else [head_path, cand_path]
        diffs.append(run_field(field, s)[ck])
    md = st.mean(diffs)
    ci = 1.96 * st.stdev(diffs) / math.sqrt(len(diffs)) if len(diffs) > 1 and st.stdev(diffs) > 0 else 0.0
    return md, md - ci, md + ci


def field_paired(cand_path, head_path, seeds, field):
    """PAIRED field A/B over `seeds`; returns (mean_diff, lo95, hi95)."""
    cfield = _field_with(field, cand_path)
    hfield = _field_with(field, head_path)
    ck, hk = _key(cand_path), _key(head_path)
    d = []
    for s in seeds:
        d.append(run_field(cfield, s)[ck] - run_field(hfield, s)[hk])
    md = st.mean(d)
    ci = 1.96 * st.stdev(d) / math.sqrt(len(d)) if len(d) > 1 and st.stdev(d) > 0 else 0.0
    return md, md - ci, md + ci


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("candidate")
    ap.add_argument("--seeds", type=int, default=200)
    args = ap.parse_args()
    seeds = range(1, args.seeds + 1)

    head = _head_bot_tmp()
    try:
        amd, alo, ahi = head_to_head(args.candidate, head, seeds)
        rmd, rlo, rhi = field_paired(args.candidate, head, seeds, REF_FIELD)
        hmd, hlo, hhi = field_paired(args.candidate, head, seeds, HONEST_FIELD)
    finally:
        if os.path.isfile(head):
            os.remove(head)

    pass_a = ahi >= 0                      # not significantly worse head-to-head
    pass_b = rhi >= 0                      # no regression vs the fold-happy ref field
    pass_c = hhi >= 0                      # no regression vs the contester (honest_equity) field
    print(f"(A) head-to-head (HU, {args.seeds} seeds): {amd:+.0f}  95% CI [{alo:+.0f}, {ahi:+.0f}]"
          f"   -> {'PASS' if pass_a else 'FAIL'} (need CI hi >= 0)")
    print(f"(B) ref field paired diff vs HEAD:    {rmd:+.0f}  95% CI [{rlo:+.0f}, {rhi:+.0f}]"
          f"   -> {'PASS' if pass_b else 'FAIL'} (need CI hi >= 0)")
    print(f"(C) honest field paired diff vs HEAD: {hmd:+.0f}  95% CI [{hlo:+.0f}, {hhi:+.0f}]"
          f"   -> {'PASS' if pass_c else 'FAIL'} (need CI hi >= 0)")
    ok = pass_a and pass_b and pass_c
    print("RESULT:", "ACCEPT" if ok else "REJECT")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
