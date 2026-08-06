"""Regenerate the deterministic Four in a Row cross-language envelopes."""

from pathlib import Path
import sys

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))

from lrgp._msgpack import packb  # noqa: E402
from lrgp.envelope import pack_envelope  # noqa: E402

SESSION = "0123456789abcdef"
CHALLENGER = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

VECTORS = {
    "four_in_a_row_challenge.bin": ("challenge", {}, bytes.fromhex("0001020304050607")),
    "four_in_a_row_accept.bin": (
        "accept",
        {"t": CHALLENGER},
        bytes.fromhex("0102030405060708"),
    ),
    "four_in_a_row_move.bin": (
        "move",
        {"c": 3, "n": 1, "x": ""},
        bytes.fromhex("0203040506070809"),
    ),
    "four_in_a_row_move_win.bin": (
        "move",
        {"c": 0, "n": 7, "x": "win", "w": CHALLENGER},
        bytes.fromhex("030405060708090a"),
    ),
}


def generate(output_directory=Path(__file__).parent):
    """Write each canonical raw MessagePack envelope and return its path."""
    written = []
    for filename, (command, payload, nonce) in VECTORS.items():
        envelope = pack_envelope(
            "four_in_a_row", 1, command, SESSION, payload, nonce=nonce
        )
        path = output_directory / filename
        path.write_bytes(packb(envelope))
        written.append(path)
    return written


if __name__ == "__main__":
    for generated in generate():
        print(generated)
