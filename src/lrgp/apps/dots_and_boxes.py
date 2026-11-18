"""LRGP Dots and Boxes — 5x5 boxes; mirrors lrgp-rs's ``apps/dots_and_boxes.rs``.

Completing a box claims it and the mover draws again; the game ends when
every box is claimed and the side with more boxes wins. Wire moves are
``{m, n, x}`` with ``m`` a line such as ``c3h``; see SPEC.md Appendix F.
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

BOXES = 5
DOTS = BOXES + 1
# Lines of each orientation: horizontal ``rank * BOXES + file``, vertical
# ``rank * DOTS + file``.
LINES = BOXES * DOTS
GRID = 2 * BOXES + 1
R_COUNT = "cnt"

HORIZONTAL = "h"
VERTICAL = "v"


def _parse_line(notation):
    """``c3h`` is the line from dot c3 to d3, ``c3v`` the line from c3 to c4.

    Returns ``(direction, file, rank)`` or ``None``.
    """
    if not isinstance(notation, str) or not notation:
        return None
    index = parse_square(notation[:-1], DOTS)
    if index is None:
        return None
    file, rank = index % DOTS, index // DOTS
    direction = notation[-1]
    if direction == HORIZONTAL and file < BOXES:
        return (HORIZONTAL, file, rank)
    if direction == VERTICAL and rank < BOXES:
        return (VERTICAL, file, rank)
    return None


def _line_name(line):
    direction, file, rank = line
    return square_name(rank * DOTS + file, DOTS) + direction


def _all_lines():
    """Every line in canonical order: by dot (rank, then file), ``h`` before ``v``."""
    for dot in range(DOTS * DOTS):
        file, rank = dot % DOTS, dot // DOTS
        if file < BOXES:
            yield (HORIZONTAL, file, rank)
        if rank < BOXES:
            yield (VERTICAL, file, rank)


def _adjacent_boxes(line):
    """Boxes on either side of a line, as ``(file, rank)``."""
    direction, file, rank = line
    boxes = []
    if direction == HORIZONTAL:
        if rank > 0:
            boxes.append((file, rank - 1))
        if rank < BOXES:
            boxes.append((file, rank))
    else:
        if file > 0:
            boxes.append((file - 1, rank))
        if file < BOXES:
            boxes.append((file, rank))
    return boxes


class _DotsPosition:
    __slots__ = ("horizontal", "vertical", "boxes", "side", "result", "extra_turn")

    def __init__(self):
        self.horizontal = [False] * LINES
        self.vertical = [False] * LINES
        # Box owners, ``rank * BOXES + file``.
        self.boxes = [EMPTY] * (BOXES * BOXES)
        self.side = BLACK
        self.result = ONGOING
        # The last move completed a box, so the mover draws again.
        self.extra_turn = False

    def drawn(self, line):
        direction, file, rank = line
        if direction == HORIZONTAL:
            return self.horizontal[rank * BOXES + file]
        return self.vertical[rank * DOTS + file]

    def box_complete(self, file, rank):
        return (
            self.horizontal[rank * BOXES + file]
            and self.horizontal[(rank + 1) * BOXES + file]
            and self.vertical[rank * DOTS + file]
            and self.vertical[rank * DOTS + file + 1]
        )


class DotsAndBoxesRules:
    LONGEST_MOVE = "f5v"
    # An odd number of boxes cannot split evenly.
    CAN_DRAW = False
    WIN_REASON = R_COUNT
    DRAW_REASON = R_COUNT

    @staticmethod
    def start():
        return _DotsPosition()

    @staticmethod
    def side_to_move(position):
        return position.side

    @staticmethod
    def is_canonical(notation):
        return _parse_line(notation) is not None

    @staticmethod
    def legal_moves(position):
        if position.result[0]:
            return []
        return [_line_name(line) for line in _all_lines() if not position.drawn(line)]

    @staticmethod
    def play(position, notation):
        line = _parse_line(notation)
        if line is None:
            raise ValueError("Invalid move notation: {}".format(notation))
        if position.drawn(line):
            raise ValueError("Line {} is already drawn".format(notation))
        direction, file, rank = line
        if direction == HORIZONTAL:
            position.horizontal[rank * BOXES + file] = True
        else:
            position.vertical[rank * DOTS + file] = True

        side = position.side
        claimed = False
        for box_file, box_rank in _adjacent_boxes(line):
            if position.box_complete(box_file, box_rank):
                position.boxes[box_rank * BOXES + box_file] = side
                claimed = True
        position.extra_turn = claimed
        if not claimed:
            position.side = WHITE if side == BLACK else BLACK

        if EMPTY not in position.boxes:
            black = position.boxes.count(BLACK)
            white = position.boxes.count(WHITE)
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
        """An 11x11 picture from the top-left dot.

        ``+`` dots, ``-`` and ``|`` drawn lines, a space for an undrawn line,
        and ``_`` / ``b`` / ``w`` box owners.
        """
        out = []
        for row in reversed(range(GRID)):
            rank = row // 2
            for column in range(GRID):
                file = column // 2
                if row % 2 == 0 and column % 2 == 0:
                    out.append("+")
                elif row % 2 == 0:
                    out.append("-" if position.horizontal[rank * BOXES + file] else " ")
                elif column % 2 == 0:
                    out.append("|" if position.vertical[rank * DOTS + file] else " ")
                else:
                    out.append(position.boxes[rank * BOXES + file])
        return "".join(out)

    @staticmethod
    def extra_metadata(position, metadata):
        metadata["black_boxes"] = position.boxes.count(BLACK)
        metadata["white_boxes"] = position.boxes.count(WHITE)
        metadata["extra_turn"] = position.extra_turn


class DotsAndBoxesApp(PlacementApp):
    """The built-in Dots and Boxes LRGP application."""

    app_id = "dots_and_boxes"
    display_name = "Dots and Boxes"
    icon = "dots_and_boxes"
    rules = DotsAndBoxesRules


def force_coin(challenger_is_black: Optional[bool]) -> None:
    """Pin the responder coin flip for tests (``True`` = challenger is Black).

    ``None`` clears the pin. Process-global; callers must serialize.
    """
    DotsAndBoxesApp._forced_coin = challenger_is_black


__all__ = ["BOXES", "DotsAndBoxesApp", "force_coin"]
