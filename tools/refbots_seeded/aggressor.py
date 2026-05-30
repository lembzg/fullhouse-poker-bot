"""TEST FIXTURE — seeded copy of bots/aggressor/bot.py.

Identical strategy to the canonical aggressor, but seeds the global RNG from
BOT_RNG_SEED at import so local regression matches are bit-reproducible. NOT a
submission; used only by tools/harness.py. Keep in sync with the original if it
ever changes.
"""
import os
import random

_s = os.environ.get("BOT_RNG_SEED")
if _s is not None and _s.lstrip("-").isdigit():
    random.seed(int(_s))

BOT_NAME = "The Aggressor"


def decide(state):
    stack = state["your_stack"]
    pot   = state["pot"]
    min_r = state["min_raise_to"]

    if random.random() < 0.7:
        raise_to = min(min_r * random.randint(2, 4), stack + state["your_bet_this_street"])
        raise_to = max(raise_to, min_r)
        return {"action": "raise", "amount": raise_to}

    if state["can_check"]:
        return {"action": "check"}

    return {"action": "call"}
