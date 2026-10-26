"""LRGP Quoridor; mirrors lrgp-rs's ``apps/quoridor.rs``.

Quoridor on a 9x9 board with ten walls each. A move steps the pawn (``e2``)
or places a two-square wall (``c3h``, ``c3v``); a wall may never cut a pawn
off from its goal. First to the far side wins, and a position seen for the
third time draws. SPEC.md Appendix G.3 defines the rules.
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
    square_name,
)

SIZE = 9
CELLS = SIZE * SIZE
WALLS_EACH = 10
# Wall centres: the corner above and right of squares a1-h8.
SLOTS = (SIZE - 1) * (SIZE - 1)
R_GOAL = "gol"
R_REPETITION = "3fr"

_DIRECTIONS = ((0, 1), (0, -1), (1, 0), (-1, 0))


def _slot(file, rank):
    return rank * (SIZE - 1) + file


def _me(side):
    return 0 if side == BLACK else 1


def _goal_rank(side):
    return SIZE - 1 if side == BLACK else 0


def _on_slots(file, rank):
    return 0 <= file < SIZE - 1 and 0 <= rank < SIZE - 1


def _step(square, direction):
    return square + direction[0] + direction[1] * SIZE


class _QuoridorPosition:
    __slots__ = ("pawns", "walls_left", "horizontal", "vertical", "ply", "seen", "result")

    def __init__(self):
        # Pawn squares and walls left: Black's, then White's.
        self.pawns = [4, CELLS - 5]
        self.walls_left = [WALLS_EACH, WALLS_EACH]
        self.horizontal = [False] * SLOTS
        self.vertical = [False] * SLOTS
        self.ply = 0
        # How often each position has occurred, for threefold repetition.
        self.seen = {self.key(): 1}
        self.result = ONGOING

    def key(self):
        return (
            self.pawns[0],
            self.pawns[1],
            self.ply % 2,
            tuple(self.horizontal),
            tuple(self.vertical),
        )

    def blocked(self, square, direction):
        """Whether a wall blocks the orthogonal step from *square*, or the
        step leaves the board."""
        df, dr = direction
        file, rank = square % SIZE, square // SIZE
        to_file, to_rank = file + df, rank + dr
        if not (0 <= to_file < SIZE and 0 <= to_rank < SIZE):
            return True
        if dr != 0:
            # Crossing between ranks low and low + 1 on this file.
            low = min(rank, to_rank)
            return (file < SIZE - 1 and self.horizontal[_slot(file, low)]) or (
                file > 0 and self.horizontal[_slot(file - 1, low)]
            )
        low = min(file, to_file)
        return (rank < SIZE - 1 and self.vertical[_slot(low, rank)]) or (
            rank > 0 and self.vertical[_slot(low, rank - 1)]
        )

    def has_path(self, side):
        """Whether *side*'s pawn can still reach its goal, ignoring pawns."""
        return self.path_steps(side) is not None

    def path_steps(self, side):
        """The steps of one shortest way to *side*'s goal, as sorted square
        pairs, or ``None`` when there is none. A new wall that blocks none
        of them cannot cut the pawn off."""
        start = self.pawns[_me(side)]
        came_from = {start: None}
        queue = [start]
        for square in queue:
            if square // SIZE == _goal_rank(side):
                steps = set()
                while came_from[square] is not None:
                    previous = came_from[square]
                    steps.add((min(previous, square), max(previous, square)))
                    square = previous
                return steps
            for direction in _DIRECTIONS:
                if not self.blocked(square, direction):
                    target = _step(square, direction)
                    if target not in came_from:
                        came_from[target] = square
                        queue.append(target)
        return None

    def pawn_moves(self, side):
        """Squares *side*'s pawn can reach this ply."""
        own, theirs = self.pawns[_me(side)], self.pawns[1 - _me(side)]
        found = set()
        for direction in _DIRECTIONS:
            if self.blocked(own, direction):
                continue
            near = _step(own, direction)
            if near != theirs:
                found.add(near)
            elif not self.blocked(near, direction):
                found.add(_step(near, direction))
            else:
                # Straight jump blocked: step diagonally around the pawn.
                for side_step in _DIRECTIONS:
                    perpendicular = side_step[0] * direction[0] + side_step[1] * direction[1] == 0
                    if perpendicular and not self.blocked(near, side_step):
                        found.add(_step(near, side_step))
        return sorted(found)

    def wall_fits(self, file, rank, horizontal):
        """Whether a wall fits at (*file*, *rank*), ignoring the path rule."""
        at = _slot(file, rank)
        if self.horizontal[at] or self.vertical[at]:
            return False
        walls = self.horizontal if horizontal else self.vertical
        df, dr = (1, 0) if horizontal else (0, 1)
        for sign in (1, -1):
            f, r = file + df * sign, rank + dr * sign
            if _on_slots(f, r) and walls[_slot(f, r)]:
                return False
        return True

    def wall_keeps_paths(self, file, rank, horizontal):
        walls = self.horizontal if horizontal else self.vertical
        walls[_slot(file, rank)] = True
        keeps = self.has_path(BLACK) and self.has_path(WHITE)
        walls[_slot(file, rank)] = False
        return keeps


def _parse_wall(notation):
    """A wall token: a square in a1-h8 and ``h`` or ``v``."""
    if not isinstance(notation, str) or not notation or notation[-1] not in "hv":
        return None
    index = parse_square(notation[:-1], SIZE)
    if index is None:
        return None
    file, rank = index % SIZE, index // SIZE
    if not _on_slots(file, rank):
        return None
    return file, rank, notation[-1] == "h"


def _wall_steps(file, rank, horizontal):
    """The two pawn steps a wall at (*file*, *rank*) blocks."""
    low = rank * SIZE + file
    if horizontal:
        return {(low, low + SIZE), (low + 1, low + 1 + SIZE)}
    return {(low, low + 1), (low + SIZE, low + SIZE + 1)}


def _wall_moves(position, side):
    if position.walls_left[_me(side)] == 0:
        return []
    paths = [position.path_steps(BLACK), position.path_steps(WHITE)]
    found = []
    for horizontal in (True, False):
        for rank in range(SIZE - 1):
            for file in range(SIZE - 1):
                if not position.wall_fits(file, rank, horizontal):
                    continue
                cut = _wall_steps(file, rank, horizontal)
                if any(cut & path for path in paths) and not position.wall_keeps_paths(
                    file, rank, horizontal
                ):
                    continue
                found.append(square_name(rank * SIZE + file, SIZE) + ("h" if horizontal else "v"))
    return found


class QuoridorRules:
    LONGEST_MOVE = "h8v"
    CAN_DRAW = True
    WIN_REASON = R_GOAL
    DRAW_REASON = R_REPETITION

    @staticmethod
    def start():
        return _QuoridorPosition()

    @staticmethod
    def side_to_move(position):
        return BLACK if position.ply % 2 == 0 else WHITE

    @staticmethod
    def is_canonical(notation):
        return parse_square(notation, SIZE) is not None or _parse_wall(notation) is not None

    @staticmethod
    def legal_moves(position):
        if position.result[0]:
            return []
        side = QuoridorRules.side_to_move(position)
        moves = [square_name(square, SIZE) for square in position.pawn_moves(side)]
        return sorted(moves + _wall_moves(position, side))

    @staticmethod
    def play(position, notation):
        side = QuoridorRules.side_to_move(position)
        me = _me(side)
        square = parse_square(notation, SIZE)
        if square is not None:
            if square not in position.pawn_moves(side):
                raise ValueError("The pawn cannot move to {}".format(notation))
            position.pawns[me] = square
        else:
            wall = _parse_wall(notation)
            if wall is None:
                raise ValueError("Invalid move notation: {}".format(notation))
            file, rank, horizontal = wall
            if position.walls_left[me] == 0:
                raise ValueError("No walls left")
            if not position.wall_fits(file, rank, horizontal):
                raise ValueError("Wall {} overlaps or crosses another".format(notation))
            if not position.wall_keeps_paths(file, rank, horizontal):
                raise ValueError("Wall {} would cut a pawn off from its goal".format(notation))
            (position.horizontal if horizontal else position.vertical)[_slot(file, rank)] = True
            position.walls_left[me] -= 1
        position.ply += 1
        if position.pawns[me] // SIZE == _goal_rank(side):
            position.result = ("win", side)
            return
        key = position.key()
        position.seen[key] = position.seen.get(key, 0) + 1
        if position.seen[key] >= 3:
            position.result = DRAW

    @staticmethod
    def outcome(position):
        return position.result

    @staticmethod
    def render(position):
        """A 17x17 picture from the top-left: squares on even rows and
        columns, wall grooves between them."""
        side = 2 * SIZE - 1
        picture = [" "] * (side * side)

        def put(file2, rank2, value):
            picture[(side - 1 - rank2) * side + file2] = value

        for square in range(CELLS):
            if square == position.pawns[0]:
                stone = BLACK
            elif square == position.pawns[1]:
                stone = WHITE
            else:
                stone = EMPTY
            put(2 * (square % SIZE), 2 * (square // SIZE), stone)
        for rank in range(SIZE - 1):
            for file in range(SIZE - 1):
                f2, r2 = 2 * file + 1, 2 * rank + 1
                if position.horizontal[_slot(file, rank)]:
                    for offset in (-1, 0, 1):
                        put(f2 + offset, r2, "-")
                if position.vertical[_slot(file, rank)]:
                    for offset in (-1, 0, 1):
                        put(f2, r2 + offset, "|")
        return "".join(picture)

    @staticmethod
    def extra_metadata(position, metadata):
        walls = []
        for kind, placed in (("h", position.horizontal), ("v", position.vertical)):
            for rank in range(SIZE - 1):
                for file in range(SIZE - 1):
                    if placed[_slot(file, rank)]:
                        walls.append(square_name(rank * SIZE + file, SIZE) + kind)
        metadata["walls"] = sorted(walls)
        metadata["black_walls"] = position.walls_left[0]
        metadata["white_walls"] = position.walls_left[1]


class QuoridorApp(PlacementApp):
    """The built-in Quoridor LRGP application."""

    app_id = "quoridor"
    display_name = "Quoridor"
    icon = "quoridor"
    rules = QuoridorRules


def force_coin(challenger_is_black: Optional[bool]) -> None:
    """Pin the responder coin flip for tests (``True`` = challenger is Black).

    ``None`` clears the pin. Process-global; callers must serialize.
    """
    QuoridorApp._forced_coin = challenger_is_black


__all__ = ["CELLS", "QuoridorApp", "SIZE", "WALLS_EACH", "force_coin"]
