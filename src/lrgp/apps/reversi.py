"""LRGP Reversi — 8x8 with automatic passing; mirrors lrgp-rs's ``apps/reversi.rs``.

A move must flip at least one disc; a player with no legal move passes
automatically, and the game ends when neither side can move. Wire moves are
``{m, n, x}`` with ``m`` a square such as ``d3``; SPEC.md Appendix E defines
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
    other_side,
    parse_square,
    render_grid,
    square_name,
)

SIZE = 8
CELLS = SIZE * SIZE
R_COUNT = "cnt"

# (file, rank) steps in all eight directions.
_DIRECTIONS = (
    (-1, -1),
    (0, -1),
    (1, -1),
    (-1, 0),
    (1, 0),
    (-1, 1),
    (0, 1),
    (1, 1),
)


class _ReversiPosition:
    __slots__ = ("cells", "side", "result", "skipped")

    def __init__(self):
        self.cells = [EMPTY] * CELLS
        for name, side in (("d4", WHITE), ("e4", BLACK), ("d5", BLACK), ("e5", WHITE)):
            self.cells[parse_square(name, SIZE)] = side
        self.side = BLACK
        self.result = ONGOING
        # The last move left the opponent without a move, so the mover goes again.
        self.skipped = False


def _flips(cells, index, side):
    """Discs that placing *side* on *index* would flip; empty if illegal."""
    if cells[index] != EMPTY:
        return []
    theirs = other_side(side)
    flipped = []
    for d_file, d_rank in _DIRECTIONS:
        file, rank = index % SIZE, index // SIZE
        run = []
        while True:
            file += d_file
            rank += d_rank
            if not (0 <= file < SIZE and 0 <= rank < SIZE):
                run = []
                break
            square = rank * SIZE + file
            if cells[square] == theirs:
                run.append(square)
                continue
            if cells[square] != side:
                run = []
            break
        flipped.extend(run)
    return flipped


def _has_move(cells, side):
    return any(_flips(cells, index, side) for index in range(CELLS))


class ReversiRules:
    LONGEST_MOVE = "h8"
    CAN_DRAW = True
    WIN_REASON = R_COUNT
    DRAW_REASON = R_COUNT

    @staticmethod
    def start():
        return _ReversiPosition()

    @staticmethod
    def side_to_move(position):
        return position.side

    @staticmethod
    def is_canonical(notation):
        return parse_square(notation, SIZE) is not None

    @staticmethod
    def legal_moves(position):
        if position.result[0]:
            return []
        return [
            square_name(index, SIZE)
            for index in range(CELLS)
            if _flips(position.cells, index, position.side)
        ]

    @staticmethod
    def play(position, notation):
        index = parse_square(notation, SIZE)
        if index is None:
            raise ValueError("Invalid move notation: {}".format(notation))
        if position.cells[index] != EMPTY:
            raise ValueError("Square {} is occupied".format(notation))
        flipped = _flips(position.cells, index, position.side)
        if not flipped:
            raise ValueError("{} does not flip any disc".format(notation))
        side = position.side
        position.cells[index] = side
        for square in flipped:
            position.cells[square] = side

        position.skipped = False
        if _has_move(position.cells, other_side(side)):
            position.side = other_side(side)
        elif _has_move(position.cells, side):
            position.skipped = True
        else:
            black = position.cells.count(BLACK)
            white = position.cells.count(WHITE)
            if black > white:
                position.result = ("win", BLACK)
            elif white > black:
                position.result = ("win", WHITE)
            else:
                position.result = DRAW

    @staticmethod
    def outcome(position):
        return position.result

    @staticmethod
    def render(position):
        return render_grid(position.cells, SIZE)

    @staticmethod
    def extra_metadata(position, metadata):
        metadata["black_count"] = position.cells.count(BLACK)
        metadata["white_count"] = position.cells.count(WHITE)
        metadata["skipped_turn"] = position.skipped


class ReversiApp(PlacementApp):
    """The built-in Reversi LRGP application."""

    app_id = "reversi"
    display_name = "Reversi"
    icon = "reversi"
    rules = ReversiRules


def force_coin(challenger_is_black: Optional[bool]) -> None:
    """Pin the responder coin flip for tests (``True`` = challenger is Black).

    ``None`` clears the pin. Process-global; callers must serialize.
    """
    ReversiApp._forced_coin = challenger_is_black


__all__ = ["CELLS", "ReversiApp", "SIZE", "force_coin"]
