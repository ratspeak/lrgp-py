"""Regenerate the deterministic Battleship cross-language fixtures.

Both fleets and salts are fixed. The challenger shoots first, and every shot
follows the shared self-play policy ``legal[(ply * 7 + SEED) % len(legal)]``
over the shooter's ``legal_moves``. The game runs through ``BattleshipApp``
until the challenger's fleet sinks, the challenger concedes, and the
responder reveals and wins. ``battleship_reference.txt`` holds one line per
ply: the shot and the answer it carries, with ``-`` for an empty field, so
lrgp-rs can replay the same game and must derive the same answers and the
same final payloads.
"""

from pathlib import Path
import sys

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))

from lrgp._msgpack import packb  # noqa: E402
from lrgp.apps import battleship  # noqa: E402
from lrgp.apps.battleship import BattleshipApp, commitment  # noqa: E402
from lrgp.constants import CMD_ACCEPT, CMD_CHALLENGE, CMD_MOVE  # noqa: E402
from lrgp.envelope import pack_envelope  # noqa: E402

SESSION = "0123456789abcdef"
CHALLENGER = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
RESPONDER = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

CHALLENGER_FLEET = "b2v d9h g3v i6v e1h"
CHALLENGER_SALT = "00112233445566778899aabbccddeeff"
RESPONDER_FLEET = "a10h c2v f5h h7v j1v"
RESPONDER_SALT = "ffeeddccbbaa99887766554433221100"
SEED = 3


def play_reference_game():
    """Play the reference game and return every wire payload in order."""
    battleship.force_coin(True)
    try:
        app = BattleshipApp()
        challenge, _ = app.handle_outgoing(
            SESSION, CMD_CHALLENGE, {"f": CHALLENGER_FLEET, "k": CHALLENGER_SALT}, CHALLENGER
        )
        app.bind_peer(SESSION, CHALLENGER, RESPONDER)
        app.handle_incoming(SESSION, CMD_CHALLENGE, challenge, CHALLENGER, RESPONDER)
        accept, _ = app.handle_outgoing(
            SESSION, CMD_ACCEPT, {"f": RESPONDER_FLEET, "k": RESPONDER_SALT}, RESPONDER
        )
        app.handle_incoming(SESSION, CMD_ACCEPT, accept, RESPONDER, CHALLENGER)
    finally:
        battleship.force_coin(None)
    wires = []
    while True:
        metadata = app._get_session(SESSION, CHALLENGER).metadata
        mover = metadata["turn"]
        if not mover:
            return wires
        state = app._get_session(SESSION, mover).metadata
        if state["phase"] == battleship.PHASE_SHOOT:
            legal = state["legal_moves"]
            intent = {"s": legal[(len(wires) * 7 + SEED) % len(legal)]}
        else:
            intent = {}
        wire, fallback = app.handle_outgoing(SESSION, CMD_MOVE, intent, mover)
        assert wire, fallback
        receiver = RESPONDER if mover == CHALLENGER else CHALLENGER
        result = app.handle_incoming(SESSION, CMD_MOVE, wire, mover, receiver)
        assert result["error"] is None, result["error"]
        wires.append(wire)


def reference_lines(wires):
    return ["{} {}".format(w.get("s") or "-", w.get("a") or "-") for w in wires]


def vectors():
    """``{filename: (command, payload, nonce)}`` for every envelope fixture."""
    wires = play_reference_game()
    return {
        "battleship_challenge.bin": (
            "challenge",
            {"h": commitment(CHALLENGER_FLEET, CHALLENGER_SALT)},
            bytes.fromhex("0001020304050607"),
        ),
        "battleship_accept.bin": (
            "accept",
            {"b": CHALLENGER, "h": commitment(RESPONDER_FLEET, RESPONDER_SALT)},
            bytes.fromhex("0102030405060708"),
        ),
        "battleship_move.bin": ("move", wires[0], bytes.fromhex("0203040506070809")),
        "battleship_concede.bin": ("move", wires[-2], bytes.fromhex("030405060708090a")),
        "battleship_move_win.bin": ("move", wires[-1], bytes.fromhex("0405060708090a0b")),
    }


def generate(output_directory=Path(__file__).parent):
    """Write every fixture and return the written paths."""
    path = output_directory / "battleship_reference.txt"
    path.write_bytes(("\n".join(reference_lines(play_reference_game())) + "\n").encode("ascii"))
    written = [path]
    for filename, (command, payload, nonce) in vectors().items():
        envelope = pack_envelope("battleship", 1, command, SESSION, payload, nonce=nonce)
        path = output_directory / filename
        path.write_bytes(packb(envelope))
        written.append(path)
    return written


if __name__ == "__main__":
    for generated in generate():
        print(generated)
