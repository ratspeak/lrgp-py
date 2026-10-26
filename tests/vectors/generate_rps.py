"""Regenerate the deterministic Rock Paper Scissors cross-language fixtures.

The challenger leads, and the match follows ``ROUNDS``, a fixed script of
(leader, follower) choices with one tie, which the responder wins 3 to 2. The
leader's salt for its commitment to round ``i`` is the byte ``i`` repeated 16
times, in hex. The match runs through ``RpsApp``, so lrgp-rs can play the same
script and must produce the same payloads byte for byte.
"""

from pathlib import Path
import sys

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))

from lrgp._msgpack import packb  # noqa: E402
from lrgp.apps import rps  # noqa: E402
from lrgp.apps.rps import RpsApp  # noqa: E402
from lrgp.constants import CMD_ACCEPT, CMD_CHALLENGE, CMD_MOVE  # noqa: E402
from lrgp.envelope import pack_envelope  # noqa: E402

SESSION = "0123456789abcdef"
CHALLENGER = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
RESPONDER = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

# (leader, follower) per round: follower, leader, tie, leader, follower, follower.
ROUNDS = [("r", "p"), ("s", "p"), ("p", "p"), ("r", "s"), ("s", "r"), ("p", "s")]


def salt(index):
    return "{:02x}".format(index) * 16


def play_reference_game():
    """Play the reference match and return every wire payload in order."""
    rps.force_coin(True)
    try:
        app = RpsApp()
        challenge, _ = app.handle_outgoing(SESSION, CMD_CHALLENGE, {}, CHALLENGER)
        app.bind_peer(SESSION, CHALLENGER, RESPONDER)
        app.handle_incoming(SESSION, CMD_CHALLENGE, challenge, CHALLENGER, RESPONDER)
        accept, _ = app.handle_outgoing(SESSION, CMD_ACCEPT, {}, RESPONDER)
        app.handle_incoming(SESSION, CMD_ACCEPT, accept, RESPONDER, CHALLENGER)
    finally:
        rps.force_coin(None)

    def send(intent, mover):
        wire, fallback = app.handle_outgoing(SESSION, CMD_MOVE, intent, mover)
        assert wire, fallback
        receiver = RESPONDER if mover == CHALLENGER else CHALLENGER
        result = app.handle_incoming(SESSION, CMD_MOVE, wire, mover, receiver)
        assert result["error"] is None, result["error"]
        return wire

    wires = [send({"c": ROUNDS[0][0], "k": salt(0)}, CHALLENGER)]
    for index, (_, follow) in enumerate(ROUNDS):
        wires.append(send({"c": follow}, RESPONDER))
        nxt = index + 1
        intent = {"c": ROUNDS[nxt][0], "k": salt(nxt)} if nxt < len(ROUNDS) else {}
        wires.append(send(intent, CHALLENGER))
    return wires


def vectors():
    """``{filename: (command, payload, nonce)}`` for every envelope fixture."""
    wires = play_reference_game()
    return {
        "rps_challenge.bin": ("challenge", {}, bytes.fromhex("0001020304050607")),
        "rps_accept.bin": ("accept", {"b": CHALLENGER}, bytes.fromhex("0102030405060708")),
        "rps_move.bin": ("move", wires[0], bytes.fromhex("0203040506070809")),
        "rps_choose.bin": ("move", wires[1], bytes.fromhex("060708090a0b0c0d")),
        "rps_reveal.bin": ("move", wires[2], bytes.fromhex("030405060708090a")),
        "rps_move_win.bin": ("move", wires[-1], bytes.fromhex("0405060708090a0b")),
    }


def generate(output_directory=Path(__file__).parent):
    """Write every fixture and return the written paths."""
    written = []
    for filename, (command, payload, nonce) in vectors().items():
        envelope = pack_envelope("rps", 1, command, SESSION, payload, nonce=nonce)
        path = output_directory / filename
        path.write_bytes(packb(envelope))
        written.append(path)
    return written


if __name__ == "__main__":
    for generated in generate():
        print(generated)
