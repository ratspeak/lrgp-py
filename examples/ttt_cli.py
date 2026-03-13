#!/usr/bin/env python3
"""Two-peer Tic-Tac-Toe over LXMF using Reticulum.

Requires: pip install lrgp[rns]

This creates two local identities and plays TTT over actual LXMF messages.
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:
    import RNS
    import LXMF
except ImportError:
    print("This example requires RNS and LXMF.")
    print("Install with: pip install lrgp[rns]")
    sys.exit(1)

from lrgp.apps.tictactoe import TicTacToeApp
from lrgp.transport import LrgpTransport
from lrgp.envelope import pack_envelope
from lrgp.router import register, dispatch_incoming
from lrgp.constants import CMD_CHALLENGE, CMD_ACCEPT, CMD_MOVE


def main():
    print("=== LRGP Tic-Tac-Toe over LXMF ===")
    print("Initializing Reticulum...")

    reticulum = RNS.Reticulum()

    # Create two identities
    id_x = RNS.Identity()
    id_o = RNS.Identity()

    print("Player X: {}".format(id_x.hash.hex()))
    print("Player O: {}".format(id_o.hash.hex()))

    # Create LXMF routers
    router_x = LXMF.LXMRouter(identity=id_x, storagepath="./lxmf_x")
    router_o = LXMF.LXMRouter(identity=id_o, storagepath="./lxmf_o")

    # Create LRGP transports
    transport_x = LrgpTransport(router_x, id_x)
    transport_o = LrgpTransport(router_o, id_o)

    # Register TTT app
    app = TicTacToeApp()
    register(app)

    received = []

    def on_receive(envelope, sender_hash, lxm):
        received.append((envelope, sender_hash))
        print("  Received: {} from {}".format(envelope.get("c"), sender_hash[:8]))

    transport_o.register_handler(on_receive)

    # X challenges O
    session_id = os.urandom(8).hex()
    envelope = pack_envelope("ttt", 1, CMD_CHALLENGE, session_id, {})
    print("\nX sends challenge...")
    transport_x.send(id_o.hash.hex(), envelope,
                     "[LRGP TTT] Sent a challenge!")

    # Wait for delivery
    time.sleep(2)

    if received:
        print("\nChallenge delivered successfully!")
        print("Envelope: {}".format(received[0][0]))
    else:
        print("\nWaiting for delivery...")
        time.sleep(5)

    print("\nDemo complete. In a full app, the game would continue interactively.")


if __name__ == "__main__":
    main()
