"""Regenerate the deterministic Checkers cross-language envelopes."""

from pathlib import Path
import sys

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))

from lrgp._msgpack import packb  # noqa: E402
from lrgp.envelope import pack_envelope  # noqa: E402

SESSION = "0123456789abcdef"
CHALLENGER = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

# A complete legal game in which the challenger plays Black. Both
# implementations replay it from their own engines and must agree on every
# legal-move list and on the final terminal state. Ply 16 is a king's
# four-capture circuit back to its own origin square; ply 32 wins by
# leaving White without pieces.
REFERENCE_GAME = [
    "f6 g5", "g3 f4", "e7 f6", "e3 d4", "g5 e3", "d2 f4", "d6 c5", "h2 g3",
    "c5 e3", "f2 d4", "f6 g5", "a3 b4", "g5 e3", "c1 d2", "e3 c1", "e1 d2",
    "c1 e3 c5 a3 c1", "g3 f4", "c7 d6", "f4 e5", "d6 f4", "g1 h2", "f4 g3",
    "h2 f4", "b6 c5", "a1 b2", "c1 a3", "f4 e5", "c5 d4", "e5 d6", "d4 b2",
    "d6 c7", "d8 b6",
]

VECTORS = {
    "checkers_challenge.bin": ("challenge", {}, bytes.fromhex("0001020304050607")),
    "checkers_accept.bin": (
        "accept",
        {"b": CHALLENGER},
        bytes.fromhex("0102030405060708"),
    ),
    "checkers_move.bin": (
        "move",
        {"m": REFERENCE_GAME[0], "n": 0, "x": ""},
        bytes.fromhex("0203040506070809"),
    ),
    "checkers_move_jump.bin": (
        "move",
        {"m": REFERENCE_GAME[16], "n": 16, "x": ""},
        bytes.fromhex("030405060708090a"),
    ),
    "checkers_move_win.bin": (
        "move",
        {"m": REFERENCE_GAME[32], "n": 32, "x": "win", "r": "np", "w": CHALLENGER},
        bytes.fromhex("0405060708090a0b"),
    ),
    "checkers_draw_claim.bin": (
        "draw_offer",
        {"r": "3fr"},
        bytes.fromhex("05060708090a0b0c"),
    ),
}


def generate(output_directory=Path(__file__).parent):
    """Write each canonical raw MessagePack envelope and return its path."""
    written = []
    for filename, (command, payload, nonce) in VECTORS.items():
        envelope = pack_envelope("checkers", 1, command, SESSION, payload, nonce=nonce)
        path = output_directory / filename
        path.write_bytes(packb(envelope))
        written.append(path)
    return written


if __name__ == "__main__":
    for generated in generate():
        print(generated)
