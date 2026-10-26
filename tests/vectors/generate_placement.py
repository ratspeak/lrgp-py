"""Regenerate the deterministic single-token game cross-language fixtures.

Covers every game in ``RULES``: Hex, Gomoku, Reversi, Dots and Boxes,
Mancala, and the Appendix G games.

Each game has a reference game played by the shared self-play policy
``legal[(ply * 7 + seed) % len(legal)]`` with the challenger as Black. Both
implementations replay it from their own engines and must agree on every
legal-move list and on the final payload. The move lists are written one per
line to ``<game>_reference.txt`` (and ``<game>_draw.txt`` for a drawn game)
so lrgp-rs can read the same bytes.

Hex's seed makes White swap at ply 1. Reversi's game contains an automatic
pass (ply 56) and ends with White winning on count after Black's last move.
The Dots and Boxes and Mancala games contain extra turns (and double boxes
and captures) and are also won by White after Black's last move.
``mancala_draw.txt`` is the same policy with its own seed, ending 24-24, and
``ultimate_ttt_draw.txt`` ends with every small board finished and no line.
The Go game ends with Black's pass and White winning on komi, although Black
holds more area. SPEC.md Appendix G describes the other games' seeds.
"""

from functools import lru_cache
from pathlib import Path
import sys

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))

from lrgp._msgpack import packb  # noqa: E402
from lrgp.apps._placement import play_checked, square_name  # noqa: E402
from lrgp.apps.go import GoRules  # noqa: E402
from lrgp.apps.gomoku import (  # noqa: E402
    CELLS as GOMOKU_CELLS,
    SIZE as GOMOKU_SIZE,
    GomokuRules,
    draw_pattern_is_black,
)
from lrgp.apps.dots_and_boxes import DotsAndBoxesRules  # noqa: E402
from lrgp.apps.hex import HexRules  # noqa: E402
from lrgp.apps.amazons import AmazonsRules  # noqa: E402
from lrgp.apps.breakthrough import BreakthroughRules  # noqa: E402
from lrgp.apps.mancala import MancalaRules  # noqa: E402
from lrgp.apps.morris import MorrisRules  # noqa: E402
from lrgp.apps.pente import PenteRules  # noqa: E402
from lrgp.apps.quoridor import QuoridorRules  # noqa: E402
from lrgp.apps.reversi import ReversiRules  # noqa: E402
from lrgp.apps.ultimate_ttt import UltimateRules  # noqa: E402
from lrgp.envelope import pack_envelope  # noqa: E402

SESSION = "0123456789abcdef"
CHALLENGER = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
RESPONDER = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

RULES = {
    "hex": HexRules,
    "gomoku": GomokuRules,
    "reversi": ReversiRules,
    "dots_and_boxes": DotsAndBoxesRules,
    "mancala": MancalaRules,
    "ultimate_ttt": UltimateRules,
    "go": GoRules,
    "quoridor": QuoridorRules,
    "morris": MorrisRules,
    "breakthrough": BreakthroughRules,
    "amazons": AmazonsRules,
    "pente": PenteRules,
}
SEEDS = {
    "hex": 113,
    "gomoku": 219,
    "reversi": 12,
    "dots_and_boxes": 1,
    "mancala": 4,
    "ultimate_ttt": 4,
    "go": 12,
    "quoridor": 63,
    "morris": 30,
    "breakthrough": 18,
    "amazons": 15,
    "pente": 13,
}
# Drawn games played by the same policy with their own seed.
DRAW_SEEDS = {"mancala": 3, "ultimate_ttt": 19, "quoridor": 0, "morris": 22}


def reference_game(game, seed=None):
    """Play the shared self-play policy to the end; return the move list."""
    return list(_played(game, SEEDS[game] if seed is None else seed))


@lru_cache(maxsize=None)
def _played(game, seed):
    rules = RULES[game]
    position = rules.start()
    moves = []
    while not rules.outcome(position)[0]:
        legal = rules.legal_moves(position)
        move = legal[(len(moves) * 7 + seed) % len(legal)]
        play_checked(rules, position, move)
        moves.append(move)
    return tuple(moves)


def gomoku_draw_game():
    """A full 15x15 board without five, Black and White alternating."""
    black, white = [], []
    for index in range(GOMOKU_CELLS):
        file, rank = index % GOMOKU_SIZE, index // GOMOKU_SIZE
        name = square_name(index, GOMOKU_SIZE)
        (black if draw_pattern_is_black(file, rank) else white).append(name)
    moves = []
    for index, stone in enumerate(black):
        moves.append(stone)
        if index < len(white):
            moves.append(white[index])
    return moves


def move_lists():
    lists = {"{}_reference.txt".format(game): reference_game(game) for game in RULES}
    lists["gomoku_draw.txt"] = gomoku_draw_game()
    for game, seed in DRAW_SEEDS.items():
        lists["{}_draw.txt".format(game)] = reference_game(game, seed)
    return lists


def vectors():
    """``{filename: (app_id, command, payload, nonce)}`` for every fixture."""
    lists = move_lists()
    result = {}
    for game in RULES:
        moves = lists["{}_reference.txt".format(game)]
        result["{}_accept.bin".format(game)] = (
            game,
            "accept",
            {"b": CHALLENGER},
            bytes.fromhex("0102030405060708"),
        )
        result["{}_move.bin".format(game)] = (
            game,
            "move",
            {"m": moves[0], "n": 0, "x": ""},
            bytes.fromhex("0203040506070809"),
        )
        # White (the responder) wins every reference game.
        result["{}_move_win.bin".format(game)] = (
            game,
            "move",
            {"m": moves[-1], "n": len(moves) - 1, "x": "win", "w": RESPONDER},
            bytes.fromhex("0405060708090a0b"),
        )
    result["hex_swap.bin"] = (
        "hex",
        "move",
        {"m": "swap", "n": 1, "x": ""},
        bytes.fromhex("030405060708090a"),
    )
    for game in ("gomoku",) + tuple(DRAW_SEEDS):
        draw = lists["{}_draw.txt".format(game)]
        result["{}_move_draw.bin".format(game)] = (
            game,
            "move",
            {"m": draw[-1], "n": len(draw) - 1, "x": "draw"},
            bytes.fromhex("05060708090a0b0c"),
        )
    return result


def generate(output_directory=Path(__file__).parent):
    """Write every fixture and return the written paths."""
    written = []
    for filename, moves in move_lists().items():
        path = output_directory / filename
        path.write_bytes(("\n".join(moves) + "\n").encode("ascii"))
        written.append(path)
    for filename, (app_id, command, payload, nonce) in vectors().items():
        envelope = pack_envelope(app_id, 1, command, SESSION, payload, nonce=nonce)
        path = output_directory / filename
        path.write_bytes(packb(envelope))
        written.append(path)
    return written


if __name__ == "__main__":
    for generated in generate():
        print(generated)
