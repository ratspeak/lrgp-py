"""LRGP Nine Men's Morris; mirrors lrgp-rs's ``apps/morris.rs``.

Players place nine pieces each, then move them along the lines, flying
anywhere once down to three. Three in a line (a mill) removes an opponent
piece. Moves are ``d2``, ``d2-d3``, or either with a removal such as
``d2-d3xg7``; SPEC.md Appendix G.4 defines the rules.
"""

from typing import Optional

from ._placement import BLACK, DRAW, EMPTY, ONGOING, WHITE, PlacementApp, other_side

PIECES = 9
# The 24 points, in canonical (byte) order.
POINTS = (
    "a1", "a4", "a7", "b2", "b4", "b6", "c3", "c4", "c5", "d1", "d2", "d3",
    "d5", "d6", "d7", "e3", "e4", "e5", "f2", "f4", "f6", "g1", "g4", "g7",
)
# Plies in a row without a removal, after which the game is drawn.
QUIET_PLIES = 100

R_PIECES = "np"
R_BLOCKED = "nm"
R_REPETITION = "3fr"
R_QUIET = "50m"

# The sixteen mills, as point indices.
MILLS = tuple(
    tuple(POINTS.index(name) for name in mill.split())
    for mill in (
        "a1 d1 g1", "b2 d2 f2", "c3 d3 e3", "a4 b4 c4",
        "e4 f4 g4", "c5 d5 e5", "b6 d6 f6", "a7 d7 g7",
        "a1 a4 a7", "b2 b4 b6", "c3 c4 c5", "d1 d2 d3",
        "d5 d6 d7", "e3 e4 e5", "f2 f4 f6", "g1 g4 g7",
    )
)


def _adjacent(index):
    """Points joined to *index* by a line."""
    found = []
    for mill in MILLS:
        for a, b in ((mill[0], mill[1]), (mill[1], mill[2])):
            if a == index:
                found.append(b)
            elif b == index:
                found.append(a)
    return found


_ADJACENT = tuple(_adjacent(index) for index in range(24))


def _in_mill(cells, index):
    stone = cells[index]
    return stone != EMPTY and any(
        index in mill and all(cells[p] == stone for p in mill) for mill in MILLS
    )


def _point(name):
    return POINTS.index(name) if name in POINTS else None


def _parse_move(notation):
    """``(from, to, remove)`` point indices, ``None`` where absent."""
    if not isinstance(notation, str):
        return None
    body, _, removed = notation.partition("x")
    remove = None
    if removed or notation.endswith("x"):
        remove = _point(removed)
        if remove is None:
            return None
    origin, dash, target = body.partition("-")
    if dash:
        start, to = _point(origin), _point(target)
        if start is None:
            return None
    else:
        start, to = None, _point(body)
    if to is None:
        return None
    return start, to, remove


def _notation(move):
    start, to, remove = move
    text = POINTS[to] if start is None else "{}-{}".format(POINTS[start], POINTS[to])
    return text if remove is None else "{}x{}".format(text, POINTS[remove])


def _slot(side):
    return 0 if side == BLACK else 1


class _MorrisPosition:
    __slots__ = ("cells", "in_hand", "ply", "quiet", "seen", "result", "reason")

    def __init__(self):
        self.cells = [EMPTY] * 24
        # Pieces still to place: Black's, then White's.
        self.in_hand = [PIECES, PIECES]
        self.ply = 0
        # Plies since the last removal.
        self.quiet = 0
        self.seen = {self.key(): 1}
        self.result = ONGOING
        self.reason = ""

    def key(self):
        return (tuple(self.cells), self.in_hand[0], self.in_hand[1], self.ply % 2)

    def on_board(self, side):
        return self.cells.count(side)

    def moves(self, side):
        """Every legal move for *side*, unsorted."""
        empty = [p for p in range(24) if self.cells[p] == EMPTY]
        steps = []
        if self.in_hand[_slot(side)] > 0:
            steps = [(None, to) for to in empty]
        else:
            flying = self.on_board(side) == 3
            for start in (p for p in range(24) if self.cells[p] == side):
                for to in empty if flying else _ADJACENT[start]:
                    if self.cells[to] == EMPTY:
                        steps.append((start, to))
        theirs = other_side(side)
        found = []
        for start, to in steps:
            after = list(self.cells)
            if start is not None:
                after[start] = EMPTY
            after[to] = side
            if not _in_mill(after, to):
                found.append((start, to, None))
                continue
            pieces = [p for p in range(24) if after[p] == theirs]
            free = [p for p in pieces if not _in_mill(after, p)]
            for remove in free or pieces:
                found.append((start, to, remove))
        return found


class MorrisRules:
    LONGEST_MOVE = "a1-a4xg7"
    CAN_DRAW = True
    WIN_REASON = R_PIECES
    DRAW_REASON = R_REPETITION

    @staticmethod
    def start():
        return _MorrisPosition()

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
        side = MorrisRules.side_to_move(position)
        return sorted(_notation(move) for move in position.moves(side))

    @staticmethod
    def play(position, text):
        side = MorrisRules.side_to_move(position)
        move = _parse_move(text)
        if move is None:
            raise ValueError("Invalid move notation: {}".format(text))
        if move not in position.moves(side):
            raise ValueError("Illegal move: {}".format(text))
        start, to, remove = move
        if start is None:
            position.in_hand[_slot(side)] -= 1
        else:
            position.cells[start] = EMPTY
        position.cells[to] = side
        position.ply += 1
        position.quiet += 1
        if remove is not None:
            position.cells[remove] = EMPTY
            position.quiet = 0

        opponent = other_side(side)
        if position.on_board(opponent) + position.in_hand[_slot(opponent)] < 3:
            position.result, position.reason = ("win", side), R_PIECES
        elif not position.moves(opponent):
            position.result, position.reason = ("win", side), R_BLOCKED
        else:
            key = position.key()
            position.seen[key] = position.seen.get(key, 0) + 1
            if position.seen[key] >= 3:
                position.result, position.reason = DRAW, R_REPETITION
            elif position.quiet >= QUIET_PLIES:
                position.result, position.reason = DRAW, R_QUIET

    @staticmethod
    def outcome(position):
        return position.result

    @staticmethod
    def reason(position):
        return position.reason

    @staticmethod
    def render(position):
        return "".join(position.cells)

    @staticmethod
    def extra_metadata(position, metadata):
        metadata["black_in_hand"] = position.in_hand[0]
        metadata["white_in_hand"] = position.in_hand[1]
        metadata["black_count"] = position.on_board(BLACK)
        metadata["white_count"] = position.on_board(WHITE)


class MorrisApp(PlacementApp):
    """The built-in Nine Men's Morris LRGP application."""

    app_id = "morris"
    display_name = "Nine Men's Morris"
    icon = "morris"
    rules = MorrisRules


def force_coin(challenger_is_black: Optional[bool]) -> None:
    """Pin the responder coin flip for tests (``True`` = challenger is Black).

    ``None`` clears the pin. Process-global; callers must serialize.
    """
    MorrisApp._forced_coin = challenger_is_black


__all__ = ["MorrisApp", "PIECES", "POINTS", "QUIET_PLIES", "force_coin"]
