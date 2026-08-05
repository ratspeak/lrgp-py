#!/usr/bin/env python3
"""Local Chess simulation — Scholar's Mate, no network.

Runs two ChessApp instances side by side and walks the seven plies of
1.e4 e5 2.Bc4 Nc6 3.Qh5 Nf6 4.Qxf7#. The example pins the coin flip via
``force_coin(True)`` so the challenger is always White.

Requires the ``[chess]`` extra:

    pip install 'lrgp[chess]'
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lrgp.apps.chess import ChessApp, force_coin
from lrgp.envelope import pack_envelope, validate_envelope_size
from lrgp.constants import (
    CMD_CHALLENGE, CMD_ACCEPT, CMD_MOVE,
)

PLAYER_A = "aaaa1111bbbb2222"
PLAYER_B = "cccc3333dddd4444"

SCHOLARS_MATE = ["e2e4", "e7e5", "f1c4", "b8c6", "d1h5", "g8f6", "h5f7"]


def main():
    force_coin(True)  # Challenger A plays White

    app_a = ChessApp()
    app_b = ChessApp()
    session_id = "10ca1c0e55000001"

    print("=== LRGP Chess Local Simulation (Scholar's Mate) ===\n")

    # 1) Challenge
    payload_out, fallback = app_a.handle_outgoing(
        session_id, CMD_CHALLENGE, {}, PLAYER_A
    )
    app_a.bind_peer(session_id, PLAYER_A, PLAYER_B)
    envelope = pack_envelope("chess", 1, CMD_CHALLENGE, session_id, payload_out)
    print("A: {} (envelope: {} B)".format(fallback, validate_envelope_size(envelope)))

    app_b.handle_incoming(session_id, CMD_CHALLENGE, {}, PLAYER_A, PLAYER_B)

    # 2) Accept
    payload_out, fallback = app_b.handle_outgoing(
        session_id, CMD_ACCEPT, {}, PLAYER_B
    )
    envelope = pack_envelope("chess", 1, CMD_ACCEPT, session_id, payload_out)
    print("B: {} (envelope: {} B)".format(fallback, validate_envelope_size(envelope)))

    app_a.handle_incoming(session_id, CMD_ACCEPT, payload_out, PLAYER_B, PLAYER_A)

    # 3) Plies
    sender, sender_app, recv, recv_app = PLAYER_A, app_a, PLAYER_B, app_b
    for ply, uci in enumerate(SCHOLARS_MATE):
        payload_out, fallback = sender_app.handle_outgoing(
            session_id, CMD_MOVE, {"m": uci}, sender
        )
        envelope = pack_envelope("chess", 1, CMD_MOVE, session_id, payload_out)
        size = validate_envelope_size(envelope)
        print("ply {} {}: {} (envelope: {} B)".format(ply, sender[:4], fallback, size))

        result = recv_app.handle_incoming(
            session_id, CMD_MOVE, payload_out, sender, recv
        )
        if result["error"]:
            print("  ERROR: {}".format(result["error"]["msg"]))
            return

        if payload_out.get("x") == "win":
            print("\n{} delivered checkmate.".format(sender[:4]))
            return

        sender, sender_app, recv, recv_app = recv, recv_app, sender, sender_app


if __name__ == "__main__":
    main()
