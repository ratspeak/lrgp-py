"""LRGP Mancala — Kalah, six pits of four seeds; mirrors lrgp-rs's ``apps/mancala.rs``.

Seeds are sown counter-clockwise; ending in your own store earns another
move, and ending in an empty pit of your own captures the opposite pit. Wire
moves are ``{m, n, x}`` with ``m`` a pit letter; see SPEC.md Appendix F.
"""

from typing import Optional

from ._placement import BLACK, DRAW, ONGOING, WHITE, PlacementApp

PITS = 6
SEEDS = 4
# Sowing order: Black's pits a-f, Black's store, White's pits g-l, White's store.
HOLES = 2 * (PITS + 1)
BLACK_STORE = PITS
WHITE_STORE = HOLES - 1
R_COUNT = "cnt"

_LETTERS = "abcdefghijkl"


def _store(side):
    return BLACK_STORE if side == BLACK else WHITE_STORE


def _pits(side):
    return range(0, PITS) if side == BLACK else range(PITS + 1, WHITE_STORE)


def _opposite(hole):
    """The pit facing *hole* across the board (``a`` faces ``l``)."""
    return 2 * PITS - hole


def _parse_pit(notation):
    """Pit letters skip the stores: ``a``-``f`` are holes 0-5, ``g``-``l`` 7-12."""
    if not isinstance(notation, str) or len(notation) != 1:
        return None
    index = _LETTERS.find(notation)
    if index < 0:
        return None
    return index if index < PITS else index + 1


def _pit_name(hole):
    return _LETTERS[hole if hole < PITS else hole - 1]


class _MancalaPosition:
    __slots__ = ("holes", "side", "result", "extra_turn")

    def __init__(self):
        self.holes = [SEEDS] * HOLES
        self.holes[BLACK_STORE] = 0
        self.holes[WHITE_STORE] = 0
        self.side = BLACK
        self.result = ONGOING
        # The last seed landed in the mover's store, so the mover goes again.
        self.extra_turn = False

    def side_empty(self, side):
        return not any(self.holes[pit] for pit in _pits(side))


class MancalaRules:
    LONGEST_MOVE = "l"
    CAN_DRAW = True
    WIN_REASON = R_COUNT
    DRAW_REASON = R_COUNT

    @staticmethod
    def start():
        return _MancalaPosition()

    @staticmethod
    def side_to_move(position):
        return position.side

    @staticmethod
    def is_canonical(notation):
        return _parse_pit(notation) is not None

    @staticmethod
    def legal_moves(position):
        if position.result[0]:
            return []
        return [_pit_name(hole) for hole in _pits(position.side) if position.holes[hole]]

    @staticmethod
    def play(position, notation):
        start = _parse_pit(notation)
        if start is None:
            raise ValueError("Invalid move notation: {}".format(notation))
        side = position.side
        opponent = WHITE if side == BLACK else BLACK
        if start not in _pits(side):
            raise ValueError("Pit {} belongs to the opponent".format(notation))
        seeds = position.holes[start]
        if not seeds:
            raise ValueError("Pit {} is empty".format(notation))

        holes = position.holes
        holes[start] = 0
        hole = start
        while seeds:
            hole = (hole + 1) % HOLES
            if hole == _store(opponent):
                continue
            holes[hole] += 1
            seeds -= 1

        position.extra_turn = hole == _store(side)
        # The last seed landed in an empty pit of the mover's own.
        if hole in _pits(side) and holes[hole] == 1 and holes[_opposite(hole)]:
            holes[_store(side)] += holes[hole] + holes[_opposite(hole)]
            holes[hole] = 0
            holes[_opposite(hole)] = 0

        if position.side_empty(BLACK) or position.side_empty(WHITE):
            for owner in (BLACK, WHITE):
                for pit in _pits(owner):
                    holes[_store(owner)] += holes[pit]
                    holes[pit] = 0
            position.extra_turn = False
            black, white = holes[BLACK_STORE], holes[WHITE_STORE]
            if black > white:
                position.result = ("win", BLACK)
            elif white > black:
                position.result = ("win", WHITE)
            else:
                position.result = DRAW
        elif not position.extra_turn:
            position.side = opponent

    @staticmethod
    def outcome(position):
        return position.result

    @staticmethod
    def render(position):
        """Seed counts in sowing order, comma-separated: a-f, Black's store, g-l, White's store."""
        return ",".join(str(seeds) for seeds in position.holes)

    @staticmethod
    def extra_metadata(position, metadata):
        metadata["black_store"] = position.holes[BLACK_STORE]
        metadata["white_store"] = position.holes[WHITE_STORE]
        metadata["extra_turn"] = position.extra_turn


class MancalaApp(PlacementApp):
    """The built-in Mancala LRGP application."""

    app_id = "mancala"
    display_name = "Mancala"
    icon = "mancala"
    rules = MancalaRules


def force_coin(challenger_is_black: Optional[bool]) -> None:
    """Pin the responder coin flip for tests (``True`` = challenger is Black).

    ``None`` clears the pin. Process-global; callers must serialize.
    """
    MancalaApp._forced_coin = challenger_is_black


__all__ = ["MancalaApp", "PITS", "SEEDS", "force_coin"]
