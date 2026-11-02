#!/usr/bin/env python3
"""Measure representative TTT envelopes and serialized LXMF message bodies.

This excludes outer addresses, signatures, stamps and transport framing.
It does not prove that a complete encrypted message fits one packet.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lrgp._msgpack import packb
from lrgp.envelope import pack_envelope, pack_lxmf_fields
from lrgp.constants import ENVELOPE_MAX_PACKED

SESSION_ID = "a1b2c3d4e5f60718"
PEER_HASH = "abcdef0123456789abcdef0123456789"


def measure(envelope, fallback):
    env_size = len(packb(envelope))
    fields = pack_lxmf_fields(envelope)
    full = [1700000000.0, "", fallback, fields]
    full_size = len(packb(full))
    return env_size, full_size


actions = [
    ("challenge",
     pack_envelope("ttt", 1, "challenge", SESSION_ID, {}),
     "[LRGP TTT] Sent a challenge!"),
    ("accept",
     pack_envelope("ttt", 1, "accept", SESSION_ID,
                   {"b": "_________", "t": PEER_HASH}),
     "[LRGP TTT] Challenge accepted"),
    ("decline",
     pack_envelope("ttt", 1, "decline", SESSION_ID, {}),
     "[LRGP TTT] Challenge declined"),
    ("move (normal)",
     pack_envelope("ttt", 1, "move", SESSION_ID,
                   {"i": 4, "b": "____X____", "n": 1, "t": PEER_HASH, "x": ""}),
     "[LRGP TTT] Move 1"),
    ("move (win)",
     pack_envelope("ttt", 1, "move", SESSION_ID,
                   {"i": 2, "b": "XXX_OO___", "n": 5, "t": "", "x": "win",
                    "w": PEER_HASH}),
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

print("LRGP Tic-Tac-Toe envelope and message-body sizes")
print("{:<16} {:>10} {:>14}".format("Action", "Envelope", "Message body"))
for name, envelope, fallback in actions:
    env_size, full_size = measure(envelope, fallback)
    assert env_size <= ENVELOPE_MAX_PACKED
    print("{:<16} {:>7} B {:>11} B".format(name, env_size, full_size))
print("Envelope budget: {} B".format(ENVELOPE_MAX_PACKED))
print("Message-body sizes exclude outer LXMF and transport overhead.")
print("Check the complete packed message with the selected transport.")
