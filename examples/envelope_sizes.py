#!/usr/bin/env python3
"""Measure and display wire sizes for every TTT action type."""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lrgp._msgpack import packb
from lrgp.envelope import pack_envelope, pack_lxmf_fields
from lrgp.constants import ENVELOPE_MAX_PACKED, OPPORTUNISTIC_MAX_CONTENT

SESSION_ID = "a1b2c3d4e5f6g7h8"
HASH_16 = "abcdef0123456789"


def measure(envelope, fallback):
    env_size = len(packb(envelope))
    fields = pack_lxmf_fields(envelope)
    full = [time.time(), "", fallback, fields]
    full_size = len(packb(full))
    return env_size, full_size


actions = [
    ("challenge",
     pack_envelope("ttt", 1, "challenge", SESSION_ID, {}),
     "[LRGP TTT] Sent a challenge!"),
    ("accept",
     pack_envelope("ttt", 1, "accept", SESSION_ID,
                   {"b": "_________", "t": HASH_16}),
     "[LRGP TTT] Challenge accepted"),
    ("decline",
     pack_envelope("ttt", 1, "decline", SESSION_ID, {}),
     "[LRGP TTT] Challenge declined"),
    ("move (normal)",
     pack_envelope("ttt", 1, "move", SESSION_ID,
                   {"i": 4, "b": "____X____", "n": 1, "t": HASH_16, "x": ""}),
     "[LRGP TTT] Move 1"),
    ("move (win)",
     pack_envelope("ttt", 1, "move", SESSION_ID,
                   {"i": 2, "b": "XXX_OO___", "n": 5, "t": "", "x": "win",
                    "w": HASH_16}),
     "[LRGP TTT] X wins!"),
    ("move (draw)",
     pack_envelope("ttt", 1, "move", SESSION_ID,
                   {"i": 8, "b": "XOXXOOOXX", "n": 9, "t": "", "x": "draw"}),
     "[LRGP TTT] Game drawn!"),
    ("resign",
     pack_envelope("ttt", 1, "resign", SESSION_ID, {}),
     "[LRGP TTT] Resigned."),
    ("draw_offer",
     pack_envelope("ttt", 1, "draw_offer", SESSION_ID, {}),
     "[LRGP TTT] Offered a draw"),
    ("error",
     pack_envelope("ttt", 1, "error", SESSION_ID,
                   {"code": "invalid_move", "msg": "Not your turn", "ref": "move"}),
     "[LRGP TTT] Error: Not your turn"),
]

print("LRGP Tic-Tac-Toe Wire Budget Verification")
print("=" * 72)
print("{:<16} {:>10} {:>12} {:>10} {:>5}".format(
    "Action", "Envelope", "Full Content", "Headroom", "Fits?"))
print("-" * 72)

all_fit = True
for name, envelope, fallback in actions:
    env_size, full_size = measure(envelope, fallback)
    headroom = OPPORTUNISTIC_MAX_CONTENT - full_size
    fits = full_size <= OPPORTUNISTIC_MAX_CONTENT
    if not fits:
        all_fit = False
    print("{:<16} {:>7} B {:>9} B {:>7} B {:>5}".format(
        name, env_size, full_size, headroom, "YES" if fits else "NO"))

print("-" * 72)
print("Envelope budget: {} B | Content limit: {} B".format(
    ENVELOPE_MAX_PACKED, OPPORTUNISTIC_MAX_CONTENT))
print("Result: {}".format("ALL FIT" if all_fit else "SOME EXCEED LIMIT"))
