"""LRGP Gomoku — 15x15 freestyle; mirrors lrgp-rs's ``apps/gomoku.rs``.

Five or more in a row wins and a full board is a draw. Wire moves are
``{m, n, x}`` with ``m`` a square such as ``h8``; SPEC.md Appendix E defines
the shared wire format.
"""

from typing import Optional

from ._placement import (
    BLACK,
    DRAW,
    EMPTY,
    ONGOING,
    WHITE,
    PlacementApp,
    parse_square,
    render_grid,
    square_name,
)

SIZE = 15
CELLS = SIZE * SIZE
R_FIVE = "5ir"
R_FULL = "ful"

# (file, rank) line directions; each is also walked backwards.
_LINES = ((1, 0), (0, 1), (1, 1), (1, -1))


class _GomokuPosition:
    __slots__ = ("cells", "ply", "result")

    def __init__(self):
        self.cells = [EMPTY] * CELLS
        self.ply = 0
        self.result = ONGOING


def _line_length(cells, index, direction):
    """Length of the line of stones through *index* along *direction*."""
    stone = cells[index]
    length = 1
    for sign in (1, -1):
        file, rank = index % SIZE, index // SIZE
        while True:
            file += direction[0] * sign
            rank += direction[1] * sign
            if not (0 <= file < SIZE and 0 <= rank < SIZE):
                break
            if cells[rank * SIZE + file] != stone:
                break
            length += 1
    return length


def _makes_five(cells, index):
    return any(_line_length(cells, index, direction) >= 5 for direction in _LINES)


class GomokuRules:
    LONGEST_MOVE = "o15"
    CAN_DRAW = True
    WIN_REASON = R_FIVE
    DRAW_REASON = R_FULL

    @staticmethod
    def start():
        return _GomokuPosition()

    @staticmethod
    def side_to_move(position):
        return BLACK if position.ply % 2 == 0 else WHITE

    @staticmethod
    def is_canonical(notation):
        return parse_square(notation, SIZE) is not None

    @staticmethod
    def legal_moves(position):
        if position.result[0]:
            return []
        return [square_name(index, SIZE) for index in range(CELLS) if position.cells[index] == EMPTY]

    @staticmethod
    def play(position, notation):
        index = parse_square(notation, SIZE)
        if index is None:
            raise ValueError("Invalid move notation: {}".format(notation))
        if position.cells[index] != EMPTY:
            raise ValueError("Square {} is occupied".format(notation))
        side = GomokuRules.side_to_move(position)
        position.cells[index] = side
        position.ply += 1
        if _makes_five(position.cells, index):
            position.result = ("win", side)
        elif position.ply == CELLS:
            position.result = DRAW

    @staticmethod
    def outcome(position):
        return position.result

    @staticmethod
    def render(position):
        return render_grid(position.cells, SIZE)

    @staticmethod
    def extra_metadata(position, metadata):
        pass


def draw_pattern_is_black(file, rank):
    """Stone for a full, five-free board shared with lrgp-rs's tests.

    Pairs of stones shift by two files each rank, so rows, columns, and both
    diagonals change color at least every four cells, and Black holds 113 of
    the 225 cells.
    """
    return ((file + 2 * rank) // 2) % 2 == 0


class GomokuApp(PlacementApp):
    """The built-in Gomoku LRGP application."""

    app_id = "gomoku"
    display_name = "Gomoku"
    icon = "gomoku"
    rules = GomokuRules


def force_coin(challenger_is_black: Optional[bool]) -> None:
    """Pin the responder coin flip for tests (``True`` = challenger is Black).

    ``None`` clears the pin. Process-global; callers must serialize.
    """
    GomokuApp._forced_coin = challenger_is_black


__all__ = ["CELLS", "GomokuApp", "SIZE", "force_coin"]
