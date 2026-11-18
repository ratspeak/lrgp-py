"""LRGP Go; mirrors lrgp-rs's ``apps/go.rs``.

9x9 Go with area scoring, positional superko, and 7.5 komi. Two passes in a
row end the game and every stone left on the board counts as alive, so
players capture dead stones before passing. Wire moves are ``{m, n, x}`` with
``m`` a point such as ``e5`` or ``pass``; SPEC.md Appendix G.2 defines the
rules.
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

SIZE = 9
CELLS = SIZE * SIZE
PASS = "pass"
# White's compensation for moving second, in half points.
KOMI_HALF_POINTS = 15
R_COUNT = "cnt"


class _GoPosition:
    __slots__ = ("cells", "ply", "passes", "seen", "captures", "result")

    def __init__(self):
        self.cells = [EMPTY] * CELLS
        self.ply = 0
        # Passes in a row; two end the game.
        self.passes = 0
        # Every board seen so far, for positional superko.
        self.seen = {tuple(self.cells)}
        # Stones each side has captured: Black's, then White's.
        self.captures = [0, 0]
        self.result = ONGOING


def _neighbors(index):
    file, rank = index % SIZE, index // SIZE
    found = []
    if file > 0:
        found.append(index - 1)
    if file + 1 < SIZE:
        found.append(index + 1)
    if rank > 0:
        found.append(index - SIZE)
    if rank + 1 < SIZE:
        found.append(index + SIZE)
    return found


def _chain(cells, index):
    """The chain through *index* and whether it has a liberty."""
    stone = cells[index]
    stones = [index]
    seen = {index}
    free = False
    for current in stones:
        for neighbor in _neighbors(current):
            if cells[neighbor] == EMPTY:
                free = True
            elif cells[neighbor] == stone and neighbor not in seen:
                seen.add(neighbor)
                stones.append(neighbor)
    return stones, free


def _place(cells, index, side):
    """The board after *side* plays on *index* and the stones captured.

    Raises ``ValueError`` for an occupied point or suicide; the caller checks
    superko.
    """
    if cells[index] != EMPTY:
        raise ValueError("Point {} is occupied".format(square_name(index, SIZE)))
    board = list(cells)
    board[index] = side
    captured = 0
    for neighbor in _neighbors(index):
        if board[neighbor] == other_side(side):
            stones, free = _chain(board, neighbor)
            if not free:
                captured += len(stones)
                for stone in stones:
                    board[stone] = EMPTY
    if not _chain(board, index)[1]:
        raise ValueError("{} would be suicide".format(square_name(index, SIZE)))
    return board, captured


def area(cells):
    """Area score ``[black, white]``: stones plus empty regions that reach
    only that side's stones."""
    score = [0, 0]
    seen = set()
    for index in range(CELLS):
        if cells[index] == BLACK:
            score[0] += 1
        elif cells[index] == WHITE:
            score[1] += 1
        elif index not in seen:
            region = [index]
            seen.add(index)
            black = white = False
            for current in region:
                for neighbor in _neighbors(current):
                    if cells[neighbor] == BLACK:
                        black = True
                    elif cells[neighbor] == WHITE:
                        white = True
                    elif neighbor not in seen:
                        seen.add(neighbor)
                        region.append(neighbor)
            if black and not white:
                score[0] += len(region)
            elif white and not black:
                score[1] += len(region)
    return score


def _try_place(position, index, side):
    """The board after *side* plays on *index*, if legal including superko."""
    board, captured = _place(position.cells, index, side)
    if tuple(board) in position.seen:
        raise ValueError(
            "{} would repeat an earlier position".format(square_name(index, SIZE))
        )
    return board, captured


def _is_legal(position, index, side):
    try:
        _try_place(position, index, side)
    except ValueError:
        return False
    return True


class GoRules:
    LONGEST_MOVE = PASS
    CAN_DRAW = False
    WIN_REASON = R_COUNT
    DRAW_REASON = R_COUNT

    @staticmethod
    def start():
        return _GoPosition()

    @staticmethod
    def side_to_move(position):
        return BLACK if position.ply % 2 == 0 else WHITE

    @staticmethod
    def is_canonical(notation):
        return notation == PASS or parse_square(notation, SIZE) is not None

    @staticmethod
    def legal_moves(position):
        if position.result[0]:
            return []
        side = GoRules.side_to_move(position)
        moves = [
            square_name(index, SIZE)
            for index in range(CELLS)
            if _is_legal(position, index, side)
        ]
        return moves + [PASS]

    @staticmethod
    def play(position, notation):
        side = GoRules.side_to_move(position)
        if notation == PASS:
            position.passes += 1
        else:
            index = parse_square(notation, SIZE)
            if index is None:
                raise ValueError("Invalid move notation: {}".format(notation))
            board, captured = _try_place(position, index, side)
            position.cells = board
            position.seen.add(tuple(board))
            position.captures[0 if side == BLACK else 1] += captured
            position.passes = 0
        position.ply += 1
        if position.passes == 2:
            black, white = area(position.cells)
            winner = BLACK if 2 * black > 2 * white + KOMI_HALF_POINTS else WHITE
            position.result = ("win", winner)

    @staticmethod
    def outcome(position):
        return position.result

    @staticmethod
    def render(position):
        return render_grid(position.cells, SIZE)

    @staticmethod
    def extra_metadata(position, metadata):
        black, white = area(position.cells)
        metadata["black_count"] = black
        metadata["white_count"] = white
        metadata["komi"] = KOMI_HALF_POINTS / 2
        metadata["black_captures"] = position.captures[0]
        metadata["white_captures"] = position.captures[1]


class GoApp(PlacementApp):
    """The built-in Go LRGP application."""

    app_id = "go"
    display_name = "Go"
    icon = "go"
    rules = GoRules


def force_coin(challenger_is_black: Optional[bool]) -> None:
    """Pin the responder coin flip for tests (``True`` = challenger is Black).

    ``None`` clears the pin. Process-global; callers must serialize.
    """
    GoApp._forced_coin = challenger_is_black


__all__ = ["CELLS", "GoApp", "KOMI_HALF_POINTS", "PASS", "SIZE", "area", "force_coin"]
