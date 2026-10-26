"""LRGP Amazons; mirrors lrgp-rs's ``apps/amazons.rs``.

The Game of the Amazons on a 10x10 board. A move slides one of your four
amazons like a chess queen and then shoots an arrow from where it landed,
burning that square for good. The player left without a move loses. Wire
moves are ``{m, n, x}`` with ``m`` such as ``d1d7/g7``; SPEC.md Appendix G.6
defines the rules.
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

SIZE = 10
CELLS = SIZE * SIZE
ARROW = "x"
R_BLOCKED = "nm"

_DIRECTIONS = ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1))


def _reach(cells, start):
    """Squares a queen on *start* reaches over empty squares."""
    found = []
    for df, dr in _DIRECTIONS:
        file, rank = start % SIZE, start // SIZE
        while True:
            file += df
            rank += dr
            if not (0 <= file < SIZE and 0 <= rank < SIZE):
                break
            square = rank * SIZE + file
            if cells[square] != EMPTY:
                break
            found.append(square)
    return found


def _parse_move(notation):
    """``<from><to>/<arrow>``, with no separator between the first two."""
    if not isinstance(notation, str):
        return None
    slide, slash, arrow = notation.partition("/")
    if not slash:
        return None
    split = next((i for i in range(1, len(slide)) if "a" <= slide[i] <= "z"), None)
    if split is None:
        return None
    squares = (
        parse_square(slide[:split], SIZE),
        parse_square(slide[split:], SIZE),
        parse_square(arrow, SIZE),
    )
    return None if None in squares else squares


def _notation(move):
    start, to, arrow = move
    return "{}{}/{}".format(square_name(start, SIZE), square_name(to, SIZE), square_name(arrow, SIZE))


class _AmazonsPosition:
    __slots__ = ("cells", "ply", "result")

    def __init__(self):
        self.cells = [EMPTY] * CELLS
        for name in ("a4", "d1", "g1", "j4"):
            self.cells[parse_square(name, SIZE)] = BLACK
        for name in ("a7", "d10", "g10", "j7"):
            self.cells[parse_square(name, SIZE)] = WHITE
        self.ply = 0
        self.result = ONGOING

    def moves(self, side):
        found = []
        for start in (s for s in range(CELLS) if self.cells[s] == side):
            lifted = list(self.cells)
            lifted[start] = EMPTY
            for to in _reach(lifted, start):
                for arrow in _reach(lifted, to):
                    found.append((start, to, arrow))
        return found

    def has_move(self, side):
        # Any amazon with an empty neighbour can move there and shoot back.
        return any(_reach(self.cells, s) for s in range(CELLS) if self.cells[s] == side)


class AmazonsRules:
    LONGEST_MOVE = "a10j10/a10"
    CAN_DRAW = False
    WIN_REASON = R_BLOCKED
    DRAW_REASON = ""

    @staticmethod
    def start():
        return _AmazonsPosition()

    @staticmethod
    def side_to_move(position):
        return BLACK if position.ply % 2 == 0 else WHITE

    @staticmethod
    def is_canonical(text):
        move = _parse_move(text)
        return move is not None and _notation(move) == text

    @staticmethod
    def legal_moves(position):
        if position.result[0]:
            return []
        side = AmazonsRules.side_to_move(position)
        return sorted(_notation(move) for move in position.moves(side))

    @staticmethod
    def play(position, text):
        side = AmazonsRules.side_to_move(position)
        move = _parse_move(text)
        if move is None:
            raise ValueError("Invalid move notation: {}".format(text))
        start, to, arrow = move
        if position.cells[start] != side:
            raise ValueError("No amazon of yours on {}".format(square_name(start, SIZE)))
        lifted = list(position.cells)
        lifted[start] = EMPTY
        if to not in _reach(lifted, start):
            raise ValueError("The amazon cannot reach {}".format(square_name(to, SIZE)))
        if arrow not in _reach(lifted, to):
            raise ValueError("The arrow cannot reach {}".format(square_name(arrow, SIZE)))
        lifted[to] = side
        lifted[arrow] = ARROW
        position.cells = lifted
        position.ply += 1
        if not position.has_move(other_side(side)):
            position.result = ("win", side)

    @staticmethod
    def outcome(position):
        return position.result

    @staticmethod
    def render(position):
        return render_grid(position.cells, SIZE)

    @staticmethod
    def extra_metadata(position, metadata):
        pass


class AmazonsApp(PlacementApp):
    """The built-in Amazons LRGP application."""

    app_id = "amazons"
    display_name = "Amazons"
    icon = "amazons"
    rules = AmazonsRules


def force_coin(challenger_is_black: Optional[bool]) -> None:
    """Pin the responder coin flip for tests (``True`` = challenger is Black).

    ``None`` clears the pin. Process-global; callers must serialize.
    """
    AmazonsApp._forced_coin = challenger_is_black


__all__ = ["AmazonsApp", "CELLS", "SIZE", "force_coin"]
