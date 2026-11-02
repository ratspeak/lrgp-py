#!/usr/bin/env python3
"""Send one Tic-Tac-Toe challenge through LXMF using Reticulum.

From this repository in a virtual environment: pip install -e ".[rns]"
Creates two ephemeral identities and ./lxmf_x plus ./lxmf_o storage, using
normal Reticulum configuration. This is a transport demonstration, not a full
game client. Delivery needs a working path; a timeout is not a successful send.
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
    print('Install from this repository with: pip install -e ".[rns]"')
    sys.exit(1)

from lrgp.transport import LrgpTransport
from lrgp.envelope import pack_envelope
from lrgp.constants import CMD_CHALLENGE


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

    # The bridge expects each identity to expose its registered LXMF source
    # destination. The destination hash is distinct from the identity hash.
    id_x.destination = router_x.register_delivery_identity(id_x)
    id_o.destination = router_o.register_delivery_identity(id_o)
    print("Player X LXMF: {}".format(id_x.destination.hash.hex()))
    print("Player O LXMF: {}".format(id_o.destination.hash.hex()))

    # Create LRGP transports
    transport_x = LrgpTransport(router_x, id_x)
    transport_o = LrgpTransport(router_o, id_o)

    received = []

    def on_receive(envelope, sender_hash, lxm):
        received.append((envelope, sender_hash))
        print("  Received: {} from {}".format(envelope.get("c"), sender_hash[:8]))

    transport_o.register_handler(on_receive)

    # X challenges O
    session_id = os.urandom(8).hex()
    envelope = pack_envelope("ttt", 1, CMD_CHALLENGE, session_id, {})
    print("\nX sends challenge...")
    transport_x.send(id_o.destination.hash.hex(), envelope,
                     "[LRGP TTT] Sent a challenge!")

    # Wait for the authenticated receive callback, not merely queue admission.
    deadline = time.monotonic() + 7
    while not received and time.monotonic() < deadline:
        time.sleep(0.1)
    if not received:
        print("\nNo challenge received before timeout; check the Reticulum path.")
        return 1
    print("\nChallenge received with a validated LXMF signature.")
    print("Envelope: {}".format(received[0][0]))
    print("A full game must pass received envelopes to LrgpRouter with the")
    print("authenticated sender and receiving local identity.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
