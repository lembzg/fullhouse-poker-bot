"""
Replay the hands where mybot loses the most chips in a bustout match, to see
HOW it busts (preflop coolers? multiway spew? hero-call-downs gone wrong?).

Run:  python3 tools/analyze_busts.py [seed]
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from sandbox.match import run_match  # noqa: E402

SEED = int(sys.argv[1]) if len(sys.argv) > 1 else 1
HERO = "mybot"
BOTS = {
    "mybot":         "bots/mybot/bot.py",
    "aggressor":     "bots/aggressor/bot.py",
    "mathematician": "bots/mathematician/bot.py",
    "shark":         "bots/shark/bot.py",
    "ref_bot_2":     "bots/ref_bot_2/bot.py",
    "template":      "bots/template/bot.py",
}


def hand_delta(hand, prev_stacks):
    fs = hand["final_stacks"]
    d = {b: fs[b] - prev_stacks.get(b, fs[b]) for b in fs}
    return d, fs


def replay(hand):
    """Compact human-readable replay from the rich event log."""
    lines = []
    hero_cards = hand.get("revealed_cards", {}).get(HERO)
    board_final = hand.get("community_cards", [])
    for ev in hand.get("events", []):
        t = ev.get("type")
        if t == "street_start":
            cc = ev.get("community_cards", [])
            lines.append(f"   -- {ev.get('street','?').upper():8} board={cc}")
        elif t == "blind":
            lines.append(f"      {ev['bot_id']:13} posts {ev['action']} {ev['amount']}")
        elif t == "action":
            mark = " <<HERO" if ev.get("bot_id") == HERO else ""
            lines.append(f"      {ev['bot_id']:13} {ev['action']:8} {ev.get('amount',0):>6}"
                         f"  (pot->{ev.get('pot_after','?')}){mark}")
        elif t == "showdown":
            lines.append(f"   -- SHOWDOWN  board={ev.get('community_cards')}")
            for bid, cards in ev.get("revealed", {}).items():
                hs = ev.get("hand_strengths", {}).get(bid, "")
                m = " <<HERO" if bid == HERO else ""
                lines.append(f"        {bid:13} {cards}  {hs}{m}")
            for w in ev.get("winners", []):
                lines.append(f"        WIN {w['bot_id']} +{w['amount']} ({w['pot_type']})")
        elif t == "uncontested_win":
            lines.append(f"   -- {ev['bot_id']} wins {ev['amount']} uncontested")
    return hero_cards, board_final, lines


def main():
    print(f"Analyzing seed {SEED} (6-bot field, 400 hands), tracking '{HERO}'\n")
    res = run_match(f"analyze_{SEED}", BOTS, n_hands=400, seed=SEED)

    print("Final deltas:", {b: res["chip_delta"][b] for b in
                            sorted(res["chip_delta"], key=lambda b: -res["chip_delta"][b])})
    print(f"\n'{HERO}' bot_errors: {res['bot_errors'].get(HERO) or 'none'}\n")

    # Per-hand hero delta.
    prev = {b: 10000 for b in BOTS}
    rows = []
    for hand in res["hands"]:
        d, fs = hand_delta(hand, prev)
        rows.append((hand["hand_num"], d.get(HERO, 0), fs.get(HERO, 0), hand))
        prev = fs

    losses = sorted([r for r in rows if r[1] < 0], key=lambda r: r[1])[:5]
    print(f"=== {HERO}'s 5 biggest-loss hands ===")
    for hand_num, delta, stack_after, hand in losses:
        hero_cards, board, lines = replay(hand)
        print(f"\n#### Hand {hand_num}: {HERO} {delta:+d}  (stack after = {stack_after}) "
              f"hole={hero_cards or 'folded/mucked'} ####")
        for ln in lines:
            print(ln)


if __name__ == "__main__":
    main()
