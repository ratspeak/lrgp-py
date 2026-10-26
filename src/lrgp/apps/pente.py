"""LRGP Pente; mirrors lrgp-rs's ``apps/pente.rs``.

Pente on a 19x19 board: Gomoku with captures. Flanking exactly two opposing
stones in a line removes them; five in a row or five captured pairs wins.
Wire moves are ``{m, n, x}`` with ``m`` a square such as ``j10``; SPEC.md
Appendix G.7 defines the rules.
"""

from typing import Optional

from ._placement import (
    BLACK,
    DRAW,
    EMPTY,
    ONGOING,
    WHITE,
    PlacementApp,
    other_side,
    parse_square,
    render_grid,
    square_name,
)

SIZE = 19
CELLS = SIZE * SIZE
# Captured pairs that win the game.
CAPTURES_TO_WIN = 5
R_FIVE = "5ir"
R_CAPTURES = "cap"
R_FULL = "ful"

# (file, rank) line directions; each is also walked backwards.
_LINES = ((1, 0), (0, 1), (1, 1), (1, -1))


class _PentePosition:
    __slots__ = ("cells", "ply", "captures", "result", "reason")

    def __init__(self):
        self.cells = [EMPTY] * CELLS
        self.ply = 0
        # Pairs captured: Black's, then White's.
        self.captures = [0, 0]
        self.result = ONGOING
        self.reason = ""


def _walk(index, direction, steps):
    """The square *steps* away from *index* along *direction*, if on the board."""
    file = index % SIZE + direction[0] * steps
    rank = index // SIZE + direction[1] * steps
    return rank * SIZE + file if 0 <= file < SIZE and 0 <= rank < SIZE else None


def _line_length(cells, index, direction):
    stone = cells[index]
    length = 1
    for sign in (1, -1):
        steps = 1
        while True:
            square = _walk(index, direction, sign * steps)
            if square is None or cells[square] != stone:
                break
            length += 1
            steps += 1
    return length


class PenteRules:
    LONGEST_MOVE = "s19"
    CAN_DRAW = True
    WIN_REASON = R_FIVE
    DRAW_REASON = R_FULL

    @staticmethod
    def start():
        return _PentePosition()

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
        side = PenteRules.side_to_move(position)
        theirs = other_side(side)
        position.cells[index] = side
        position.ply += 1

        me = 0 if side == BLACK else 1
        for df, dr in _LINES:
            for direction in ((df, dr), (-df, -dr)):
                a, b, c = (_walk(index, direction, steps) for steps in (1, 2, 3))
                if (
                    c is not None
                    and position.cells[a] == theirs
                    and position.cells[b] == theirs
                    and position.cells[c] == side
                ):
                    position.cells[a] = EMPTY
                    position.cells[b] = EMPTY
                    position.captures[me] += 1

        if any(_line_length(position.cells, index, d) >= 5 for d in _LINES):
            position.result, position.reason = ("win", side), R_FIVE
        elif position.captures[me] >= CAPTURES_TO_WIN:
            position.result, position.reason = ("win", side), R_CAPTURES
        elif EMPTY not in position.cells:
            position.result, position.reason = DRAW, R_FULL

    @staticmethod
    def outcome(position):
        return position.result

    @staticmethod
    def reason(position):
        return position.reason

    @staticmethod
    def render(position):
        return render_grid(position.cells, SIZE)

    @staticmethod
    def extra_metadata(position, metadata):
        metadata["black_captures"] = position.captures[0]
        metadata["white_captures"] = position.captures[1]


class PenteApp(PlacementApp):
    """The built-in Pente LRGP application."""

    app_id = "pente"
    display_name = "Pente"
    icon = "pente"
    rules = PenteRules


def force_coin(challenger_is_black: Optional[bool]) -> None:
    """Pin the responder coin flip for tests (``True`` = challenger is Black).

    ``None`` clears the pin. Process-global; callers must serialize.
    """
    PenteApp._forced_coin = challenger_is_black


__all__ = ["CAPTURES_TO_WIN", "CELLS", "PenteApp", "SIZE", "force_coin"]
