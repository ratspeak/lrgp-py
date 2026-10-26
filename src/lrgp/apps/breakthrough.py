"""LRGP Breakthrough; mirrors lrgp-rs's ``apps/breakthrough.rs``.

Breakthrough on an 8x8 board. Each side has two rows of pieces that step one
square forward, straight or diagonally, and capture only diagonally. The
first piece to reach the far rank wins. Wire moves are ``{m, n, x}`` with
``m`` a step such as ``a2b3``; SPEC.md Appendix G.5 defines the rules.
"""

from typing import Optional

from ._placement import (
    BLACK,
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
R_GOAL = "gol"
R_PIECES = "np"


class _BreakthroughPosition:
    __slots__ = ("cells", "ply", "result", "reason")

    def __init__(self):
        self.cells = [BLACK] * (2 * SIZE) + [EMPTY] * (CELLS - 4 * SIZE) + [WHITE] * (2 * SIZE)
        self.ply = 0
        self.result = ONGOING
        self.reason = ""

    def steps(self, side):
        found = []
        forward = 1 if side == BLACK else -1
        for start in (s for s in range(CELLS) if self.cells[s] == side):
            file, rank = start % SIZE, start // SIZE
            to_rank = rank + forward
            if not 0 <= to_rank < SIZE:
                continue
            for df in (-1, 0, 1):
                to_file = file + df
                if not 0 <= to_file < SIZE:
                    continue
                to = to_rank * SIZE + to_file
                target = self.cells[to]
                if target == EMPTY or (df != 0 and target == other_side(side)):
                    found.append((start, to))
        return found


def _parse_step(notation):
    """A step: two squares written together, such as ``a2b3``."""
    if not isinstance(notation, str) or len(notation) != 4:
        return None
    start, to = parse_square(notation[:2], SIZE), parse_square(notation[2:], SIZE)
    if start is None or to is None:
        return None
    return start, to


class BreakthroughRules:
    LONGEST_MOVE = "h7h8"
    CAN_DRAW = False
    WIN_REASON = R_GOAL
    DRAW_REASON = ""

    @staticmethod
    def start():
        return _BreakthroughPosition()

    @staticmethod
    def side_to_move(position):
        return BLACK if position.ply % 2 == 0 else WHITE

    @staticmethod
    def is_canonical(notation):
        return _parse_step(notation) is not None

    @staticmethod
    def legal_moves(position):
        if position.result[0]:
            return []
        side = BreakthroughRules.side_to_move(position)
        return sorted(
            square_name(start, SIZE) + square_name(to, SIZE) for start, to in position.steps(side)
        )

    @staticmethod
    def play(position, notation):
        side = BreakthroughRules.side_to_move(position)
        step = _parse_step(notation)
        if step is None:
            raise ValueError("Invalid move notation: {}".format(notation))
        if step not in position.steps(side):
            raise ValueError("Illegal move: {}".format(notation))
        position.cells[step[0]] = EMPTY
        position.cells[step[1]] = side
        position.ply += 1
        far = SIZE - 1 if side == BLACK else 0
        if step[1] // SIZE == far:
            position.result, position.reason = ("win", side), R_GOAL
        elif other_side(side) not in position.cells:
            position.result, position.reason = ("win", side), R_PIECES
        # Otherwise the opponent has a move: its most advanced piece can step
        # to a diagonal square no piece of its own holds.

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
        metadata["black_count"] = position.cells.count(BLACK)
        metadata["white_count"] = position.cells.count(WHITE)


class BreakthroughApp(PlacementApp):
    """The built-in Breakthrough LRGP application."""

    app_id = "breakthrough"
    display_name = "Breakthrough"
    icon = "breakthrough"
    rules = BreakthroughRules


def force_coin(challenger_is_black: Optional[bool]) -> None:
    """Pin the responder coin flip for tests (``True`` = challenger is Black).

    ``None`` clears the pin. Process-global; callers must serialize.
    """
    BreakthroughApp._forced_coin = challenger_is_black


__all__ = ["BreakthroughApp", "CELLS", "SIZE", "force_coin"]
