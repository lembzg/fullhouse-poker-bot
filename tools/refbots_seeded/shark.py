"""TEST FIXTURE — seeded copy of bots/shark/bot.py.

Identical strategy to the canonical shark, but seeds the global RNG from
BOT_RNG_SEED at import so local regression matches are bit-reproducible. NOT a
submission; used only by tools/harness.py. Keep in sync with the original if it
ever changes.
"""
import os
import random

_s = os.environ.get("BOT_RNG_SEED")
if _s is not None and _s.lstrip("-").isdigit():
    random.seed(int(_s))

BOT_NAME = "The Shark"

STRONG_HANDS = {
    ("A", "A"), ("K", "K"), ("Q", "Q"), ("J", "J"), ("T", "T"),
    ("A", "K"), ("A", "Q"), ("A", "J"), ("K", "Q"),
}


def hand_strength(cards):
    ranks = tuple(sorted([c[0] for c in cards], reverse=True))
    suited = cards[0][1] == cards[1][1]
    if ranks in STRONG_HANDS:
        return "strong"
    if ranks[0] in "AKQJT" or suited:
        return "medium"
    return "weak"


def decide(state):
    street  = state["street"]
    owed    = state["amount_owed"]
    pot     = state["pot"]
    stack   = state["your_stack"]
    seat    = state["seat_to_act"]
    n       = len(state["players"])
    position = seat / max(n - 1, 1)  # 0 = early, 1 = late

    if street == "preflop":
        strength = hand_strength(state["your_cards"])

        if strength == "strong":
            raise_to = min(state["min_raise_to"] * 3, stack + state["your_bet_this_street"])
            return {"action": "raise", "amount": raise_to}

        if strength == "medium" and position > 0.5:
            if owed < pot * 0.2:
                return {"action": "call"}

        if state["can_check"]:
            return {"action": "check"}

        return {"action": "fold"}

    if state["can_check"]:
        if position > 0.6 and random.random() < 0.4:
            bet = min(int(pot * 0.6), stack + state["your_bet_this_street"])
            bet = max(bet, state["min_raise_to"])
            return {"action": "raise", "amount": bet}
        return {"action": "check"}

    threshold = 0.25 if position > 0.5 else 0.15
    if pot > 0 and owed / pot <= threshold:
        return {"action": "call"}

    return {"action": "fold"}
