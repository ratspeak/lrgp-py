"""LRGP Ultimate Tic-Tac-Toe; mirrors lrgp-rs's ``apps/ultimate_ttt.rs``.

Nine Tic-Tac-Toe boards sit in a 3x3 grid on one 9x9 board. The cell a player
takes picks the small board the opponent plays in next; three small boards
won in a row wins. Wire moves are ``{m, n, x}`` with ``m`` a square such as
``e5``; SPEC.md Appendix G defines the rules.
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

SIZE = 9
CELLS = SIZE * SIZE
R_LINE = "3ib"
R_FULL = "ful"
# A small board that ended without a line; it counts for nobody.
DEAD = "d"

# Lines through a 3x3 grid, as ``file + 3 * rank`` indices.
_LINES = (
    (0, 1, 2),
    (3, 4, 5),
    (6, 7, 8),
    (0, 3, 6),
    (1, 4, 7),
    (2, 5, 8),
    (0, 4, 8),
    (2, 4, 6),
)


class _UltimatePosition:
    __slots__ = ("cells", "boards", "target", "ply", "result")

    def __init__(self):
        self.cells = [EMPTY] * CELLS
        # Each small board's state (_, b, w, or d), ``file + 3 * rank``.
        self.boards = [EMPTY] * 9
        # The small board the next move must be in, if it is still open.
        self.target = None
        self.ply = 0
        self.result = ONGOING

    def open(self, index):
        return (
            self.cells[index] == EMPTY
            and self.boards[board_of(index)] == EMPTY
            and (self.target is None or self.target == board_of(index))
        )


def board_of(index):
    """The small board holding square *index*."""
    return (index % SIZE) // 3 + 3 * ((index // SIZE) // 3)


def cell_of(index):
    """The position of square *index* inside its small board."""
    return (index % SIZE) % 3 + 3 * ((index // SIZE) % 3)


def squares_of(board):
    """The squares of small board *board*, in square order."""
    file, rank = 3 * (board % 3), 3 * (board // 3)
    return [(rank + cell // 3) * SIZE + file + cell % 3 for cell in range(9)]


def _has_line(grid, mark):
    return any(all(grid[i] == mark for i in line) for line in _LINES)


class UltimateRules:
    LONGEST_MOVE = "i9"
    CAN_DRAW = True
    WIN_REASON = R_LINE
    DRAW_REASON = R_FULL

    @staticmethod
    def start():
        return _UltimatePosition()

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
        return [square_name(index, SIZE) for index in range(CELLS) if position.open(index)]

    @staticmethod
    def play(position, notation):
        index = parse_square(notation, SIZE)
        if index is None:
            raise ValueError("Invalid move notation: {}".format(notation))
        if position.cells[index] != EMPTY:
            raise ValueError("Square {} is occupied".format(notation))
        if position.boards[board_of(index)] != EMPTY:
            raise ValueError("The small board holding {} is finished".format(notation))
        if not position.open(index):
            raise ValueError("{} is not in the small board you were sent to".format(notation))
        side = UltimateRules.side_to_move(position)
        position.cells[index] = side
        position.ply += 1

        board = board_of(index)
        grid = [position.cells[square] for square in squares_of(board)]
        if _has_line(grid, side):
            position.boards[board] = side
        elif EMPTY not in grid:
            position.boards[board] = DEAD

        following = cell_of(index)
        position.target = following if position.boards[following] == EMPTY else None
        if _has_line(position.boards, side):
            position.result = ("win", side)
        elif EMPTY not in position.boards:
            position.result = DRAW

    @staticmethod
    def outcome(position):
        return position.result

    @staticmethod
    def render(position):
        return render_grid(position.cells, SIZE)

    @staticmethod
    def extra_metadata(position, metadata):
        # Small boards from the top row down, like the board itself.
        metadata["boards"] = "".join(
            position.boards[board] for board in (6, 7, 8, 3, 4, 5, 0, 1, 2)
        )


class UltimateTicTacToeApp(PlacementApp):
    """The built-in Ultimate Tic-Tac-Toe LRGP application."""

    app_id = "ultimate_ttt"
    display_name = "Ultimate Tic-Tac-Toe"
    icon = "ultimate_ttt"
    rules = UltimateRules


def force_coin(challenger_is_black: Optional[bool]) -> None:
    """Pin the responder coin flip for tests (``True`` = challenger is Black).

    ``None`` clears the pin. Process-global; callers must serialize.
    """
    UltimateTicTacToeApp._forced_coin = challenger_is_black


__all__ = ["CELLS", "SIZE", "UltimateTicTacToeApp", "force_coin"]
