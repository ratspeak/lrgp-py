"""LRGP Hex — 11x11 with the swap rule; mirrors lrgp-rs's ``apps/hex.rs``.

Black joins ranks 1 and 11, White joins files a and k. Wire moves are
``{m, n, x}`` with ``m`` a square such as ``f6`` or ``swap``; SPEC.md
Appendix E defines the shared wire format.
"""

from typing import Optional

from ._placement import (
    BLACK,
    EMPTY,
    ONGOING,
    WHITE,
    PlacementApp,
    parse_square,
    render_grid,
    square_name,
)

SIZE = 11
CELLS = SIZE * SIZE
# White's optional second move: mirror Black's first stone as a White stone.
SWAP = "swap"
R_CONNECTED = "con"

# (file, rank) steps to the six neighbours of a cell.
_NEIGHBORS = ((-1, 0), (1, 0), (0, -1), (0, 1), (1, -1), (-1, 1))


class _HexPosition:
    __slots__ = ("cells", "ply", "winner")

    def __init__(self):
        self.cells = [EMPTY] * CELLS
        self.ply = 0
        self.winner = None


def _neighbor(index, direction):
    file = index % SIZE + direction[0]
    rank = index // SIZE + direction[1]
    if 0 <= file < SIZE and 0 <= rank < SIZE:
        return rank * SIZE + file
    return None


def _connects(cells, start, side):
    """``True`` when the group containing *start* touches both of *side*'s edges."""
    if side == BLACK:
        edge = lambda index: index // SIZE  # noqa: E731
    else:
        edge = lambda index: index % SIZE  # noqa: E731
    seen = {start}
    stack = [start]
    low = high = False
    while stack:
        index = stack.pop()
        low = low or edge(index) == 0
        high = high or edge(index) == SIZE - 1
        for direction in _NEIGHBORS:
            following = _neighbor(index, direction)
            if following is not None and following not in seen and cells[following] == side:
                seen.add(following)
                stack.append(following)
    return low and high


class HexRules:
    LONGEST_MOVE = "k11"
    CAN_DRAW = False
    WIN_REASON = R_CONNECTED
    DRAW_REASON = ""

    @staticmethod
    def start():
        return _HexPosition()

    @staticmethod
    def side_to_move(position):
        return BLACK if position.ply % 2 == 0 else WHITE

    @staticmethod
    def is_canonical(notation):
        return notation == SWAP or parse_square(notation, SIZE) is not None

    @staticmethod
    def legal_moves(position):
        if position.winner is not None:
            return []
        moves = [square_name(index, SIZE) for index in range(CELLS) if position.cells[index] == EMPTY]
        if position.ply == 1:
            moves.append(SWAP)
        return moves

    @staticmethod
    def play(position, notation):
        if notation == SWAP:
            if position.ply != 1:
                raise ValueError("Swap is only legal as White's first move")
            black = position.cells.index(BLACK)
            file, rank = black % SIZE, black // SIZE
            position.cells[black] = EMPTY
            # The mirrored square swaps file and rank.
            position.cells[file * SIZE + rank] = WHITE
            position.ply += 1
            return
        index = parse_square(notation, SIZE)
        if index is None:
            raise ValueError("Invalid move notation: {}".format(notation))
        if position.cells[index] != EMPTY:
            raise ValueError("Square {} is occupied".format(notation))
        side = HexRules.side_to_move(position)
        position.cells[index] = side
        position.ply += 1
        if _connects(position.cells, index, side):
            position.winner = side

    @staticmethod
    def outcome(position):
        return ONGOING if position.winner is None else ("win", position.winner)

    @staticmethod
    def render(position):
        return render_grid(position.cells, SIZE)

    @staticmethod
    def extra_metadata(position, metadata):
        pass


class HexApp(PlacementApp):
    """The built-in Hex LRGP application."""

    app_id = "hex"
    display_name = "Hex"
    icon = "hex"
    rules = HexRules


def force_coin(challenger_is_black: Optional[bool]) -> None:
    """Pin the responder coin flip for tests (``True`` = challenger is Black).

    ``None`` clears the pin. Process-global; callers must serialize.
    """
    HexApp._forced_coin = challenger_is_black


__all__ = ["CELLS", "HexApp", "SIZE", "SWAP", "force_coin"]
