"""Protocol, rule, persistence, and interop tests for the single-token games.

Covers Hex, Gomoku, Reversi, Dots and Boxes, and Mancala here; the newer
games keep their rule tests in their own files and join the shared lifecycle,
hydration, and vector tests through ``GAMES``.
"""

import copy
import hashlib
import os
import sys

import pytest

from lrgp._msgpack import packb, unpackb
from lrgp.apps import (
    amazons,
    breakthrough,
    dots_and_boxes,
    go,
    gomoku,
    hex as hex_game,
    mancala,
    morris,
    quoridor,
    reversi,
    ultimate_ttt,
)
from lrgp.apps._placement import (
    BLACK,
    EMPTY,
    WHITE,
    PlacementApp,
    parse_square,
    play_checked,
    replay,
    square_name,
    terminal_reason,
)
from lrgp.apps.amazons import AmazonsApp
from lrgp.apps.breakthrough import BreakthroughApp
from lrgp.apps.dots_and_boxes import DotsAndBoxesApp, DotsAndBoxesRules
from lrgp.apps.go import GoApp
from lrgp.apps.gomoku import GomokuApp, GomokuRules
from lrgp.apps.hex import SWAP, HexApp, HexRules
from lrgp.apps.mancala import MancalaApp, MancalaRules
from lrgp.apps.morris import MorrisApp
from lrgp.apps.quoridor import QuoridorApp
from lrgp.apps.reversi import ReversiApp, ReversiRules
from lrgp.apps.ultimate_ttt import UltimateTicTacToeApp
from lrgp.constants import (
    CMD_ACCEPT,
    CMD_CHALLENGE,
    CMD_DECLINE,
    CMD_DRAW_ACCEPT,
    CMD_DRAW_DECLINE,
    CMD_DRAW_OFFER,
    CMD_MOVE,
    CMD_RESIGN,
    ENVELOPE_MAX_PACKED,
    ERR_INVALID_MOVE,
    ERR_NOT_YOUR_TURN,
    ERR_PROTOCOL_ERROR,
    OPPORTUNISTIC_MAX_CONTENT,
    STATUS_ACTIVE,
    STATUS_COMPLETED,
    STATUS_DECLINED,
    STATUS_EXPIRED,
    STATUS_PENDING,
)
from lrgp.envelope import (
    measure_content_size,
    pack_envelope,
    pack_lxmf_fields,
    unpack_envelope,
    validate_envelope_size,
)
from lrgp.errors import InvalidEnvelope, OutgoingActionError
from lrgp.router import LrgpRouter

VECTORS_DIR = os.path.join(os.path.dirname(__file__), "vectors")
sys.path.insert(0, VECTORS_DIR)
import generate_placement  # noqa: E402
from generate_placement import CHALLENGER, RESPONDER, SEEDS, SESSION  # noqa: E402

GAMES = {
    "hex": (HexApp, hex_game),
    "gomoku": (GomokuApp, gomoku),
    "reversi": (ReversiApp, reversi),
    "dots_and_boxes": (DotsAndBoxesApp, dots_and_boxes),
    "mancala": (MancalaApp, mancala),
    "ultimate_ttt": (UltimateTicTacToeApp, ultimate_ttt),
    "go": (GoApp, go),
    "quoridor": (QuoridorApp, quoridor),
    "morris": (MorrisApp, morris),
    "breakthrough": (BreakthroughApp, breakthrough),
    "amazons": (AmazonsApp, amazons),
}


@pytest.fixture(params=sorted(GAMES))
def game(request):
    """``(app_class, module)`` with the coin pinned so the challenger is Black."""
    app_class, module = GAMES[request.param]
    module.force_coin(True)
    yield app_class, module
    module.force_coin(None)


@pytest.fixture
def app(game):
    return game[0]()


def activate(app, session_id=SESSION):
    """Challenger challenges, responder accepts; both live in one app store."""
    app.handle_outgoing(session_id, CMD_CHALLENGE, {}, CHALLENGER)
    app.bind_peer(session_id, CHALLENGER, RESPONDER)
    challenge = app.handle_incoming(
        session_id, CMD_CHALLENGE, {}, CHALLENGER, RESPONDER
    )
    assert challenge["error"] is None
    app.validate_outgoing(session_id, CMD_ACCEPT, {}, RESPONDER)
    accept, _fallback = app.handle_outgoing(session_id, CMD_ACCEPT, {}, RESPONDER)
    accepted = app.handle_incoming(session_id, CMD_ACCEPT, accept, RESPONDER, CHALLENGER)
    assert accepted["error"] is None
    return accept


def other(identity):
    return RESPONDER if identity == CHALLENGER else CHALLENGER


def meta(app, identity, session_id=SESSION):
    return app._get_session(session_id, identity).metadata


def play(app, notation, mover, session_id=SESSION):
    app.validate_outgoing(session_id, CMD_MOVE, {"m": notation}, mover)
    wire, fallback = app.handle_outgoing(session_id, CMD_MOVE, {"m": notation}, mover)
    assert wire, fallback
    result = app.handle_incoming(session_id, CMD_MOVE, wire, mover, other(mover))
    assert result["error"] is None, result["error"]
    return wire


def play_any(app):
    metadata = meta(app, CHALLENGER)
    return play(app, metadata["legal_moves"][0], metadata["turn"])


def assert_shared_state(app, session_id=SESSION):
    left = dict(meta(app, CHALLENGER, session_id))
    right = dict(meta(app, RESPONDER, session_id))
    left.pop("my_color")
    right.pop("my_color")
    assert left == right


def read_moves(filename):
    with open(os.path.join(VECTORS_DIR, filename), "rb") as handle:
        return handle.read().decode("ascii").splitlines()


# --- Hex rules -------------------------------------------------------------


def hex_game_of(moves):
    return replay(HexRules, list(moves))


def hex_cell(position, name):
    return position.cells[parse_square(name, hex_game.SIZE)]


class TestHexRules:
    def test_notation_is_strict(self):
        assert HexRules.is_canonical("a1")
        assert HexRules.is_canonical("k11")
        assert HexRules.is_canonical(SWAP)
        for bad in ["", "a0", "a12", "l1", "a01", "A1", "k 11", "Swap", "a1 ", None, 3]:
            assert not HexRules.is_canonical(bad), bad
        assert square_name(hex_game.CELLS - 1, hex_game.SIZE) == "k11"

    def test_neighbours_follow_the_rhombus(self):
        index = parse_square("b2", hex_game.SIZE)
        names = sorted(
            square_name(n, hex_game.SIZE)
            for n in (hex_game._neighbor(index, d) for d in hex_game._NEIGHBORS)
            if n is not None
        )
        assert names == ["a2", "a3", "b1", "b3", "c1", "c2"]

    def test_black_connects_ranks_and_white_connects_files(self):
        moves = []
        for rank in range(1, 12):
            moves.append("f{}".format(rank))
            if rank < 11:
                moves.append("a{}".format(rank))
        assert HexRules.outcome(hex_game_of(moves[:-1])) == ("", None)
        won = hex_game_of(moves)
        assert HexRules.outcome(won) == ("win", BLACK)
        assert HexRules.legal_moves(won) == []

        cells = [EMPTY] * hex_game.CELLS
        for file in range(11):
            cells[5 * 11 + file] = WHITE
        assert hex_game._connects(cells, 5 * 11 + 3, WHITE)
        assert not hex_game._connects(cells, 5 * 11 + 3, BLACK)

    def test_zigzag_chain_uses_hex_diagonals(self):
        cells = [EMPTY] * hex_game.CELLS
        for rank in range(11):
            cells[rank * 11 + (10 - rank)] = BLACK
        assert hex_game._connects(cells, 10, BLACK)
        cells = [EMPTY] * hex_game.CELLS
        for rank in range(11):
            cells[rank * 11 + rank] = BLACK
        assert not hex_game._connects(cells, 0, BLACK)

    def test_swap_mirrors_blacks_first_stone_and_keeps_alternation(self):
        swapped = hex_game_of(["c7", SWAP])
        assert hex_cell(swapped, "c7") == EMPTY
        assert hex_cell(swapped, "g3") == WHITE
        assert HexRules.side_to_move(swapped) == BLACK
        assert SWAP not in HexRules.legal_moves(swapped)
        assert hex_cell(hex_game_of(["f6", SWAP]), "f6") == WHITE
        assert HexRules.legal_moves(hex_game_of(["a1"]))[-1] == SWAP
        with pytest.raises(ValueError):
            play_checked(HexRules, HexRules.start(), SWAP)
        with pytest.raises(ValueError):
            play_checked(HexRules, hex_game_of(["a1", "b1"]), SWAP)

    def test_occupied_cells_are_illegal(self):
        with pytest.raises(ValueError):
            play_checked(HexRules, hex_game_of(["e5"]), "e5")
        assert len(HexRules.legal_moves(HexRules.start())) == hex_game.CELLS

    def test_full_boards_always_have_a_winner(self):
        position = HexRules.start()
        index = 0
        while not HexRules.outcome(position)[0]:
            index = (index + 37) % hex_game.CELLS
            if position.cells[index] == EMPTY:
                play_checked(HexRules, position, square_name(index, hex_game.SIZE))
        assert HexRules.outcome(position)[0] == "win"

    def test_board_renders_from_the_top_rank(self):
        board = HexRules.render(hex_game_of(["a1", "k11"]))
        assert len(board) == hex_game.CELLS
        assert board[:11] == "__________w"
        assert board[-11:] == "b__________"


# --- Gomoku rules ----------------------------------------------------------

FAR = ["a15", "c15", "e15", "g15"]


def line_game(black, white):
    moves = []
    for index, stone in enumerate(black):
        moves.append(stone)
        if index < len(white):
            moves.append(white[index])
    return moves


class TestGomokuRules:
    def test_notation_is_strict(self):
        assert GomokuRules.is_canonical("a1")
        assert GomokuRules.is_canonical("o15")
        for bad in ["", "a0", "a16", "p1", "a01", "H8", "h 8", "swap", "a1١"]:
            assert not GomokuRules.is_canonical(bad), bad

    @pytest.mark.parametrize(
        "line",
        [
            ["d4", "e4", "f4", "g4", "h4"],
            ["d4", "d5", "d6", "d7", "d8"],
            ["d4", "e5", "f6", "g7", "h8"],
            ["d8", "e7", "f6", "g5", "h4"],
        ],
    )
    def test_five_wins_in_every_direction(self, line):
        moves = line_game(line, FAR)
        assert GomokuRules.outcome(replay(GomokuRules, moves[:-1])) == ("", None)
        won = replay(GomokuRules, moves)
        assert GomokuRules.outcome(won) == ("win", BLACK)
        assert GomokuRules.legal_moves(won) == []

    def test_overlines_win_in_freestyle(self):
        moves = line_game(["d4", "e4", "f4", "h4", "i4", "g4"], FAR + ["i15"])
        assert GomokuRules.outcome(replay(GomokuRules, moves)) == ("win", BLACK)

    def test_four_and_broken_lines_do_not_win(self):
        moves = line_game(["d4", "e4", "f4", "g4", "i4"], FAR)
        assert GomokuRules.outcome(replay(GomokuRules, moves)) == ("", None)

    def test_white_can_win(self):
        moves = line_game(
            ["a1", "c1", "e1", "g1", "i1", "k1"], ["d8", "e8", "f8", "g8", "h8"]
        )
        assert GomokuRules.outcome(replay(GomokuRules, moves[:-1])) == ("win", WHITE)

    def test_occupied_squares_and_post_win_moves_are_illegal(self):
        with pytest.raises(ValueError):
            play_checked(GomokuRules, replay(GomokuRules, ["h8"]), "h8")
        won = replay(GomokuRules, line_game(["d4", "e4", "f4", "g4", "h4"], FAR))
        with pytest.raises(ValueError):
            play_checked(GomokuRules, won, "o1")

    def test_full_board_without_five_is_a_draw(self):
        moves = read_moves("gomoku_draw.txt")
        assert len(moves) == gomoku.CELLS
        assert GomokuRules.outcome(replay(GomokuRules, moves[:-1])) == ("", None)
        assert GomokuRules.outcome(replay(GomokuRules, moves)) == ("draw", None)

    def test_board_renders_from_the_top_rank(self):
        board = GomokuRules.render(replay(GomokuRules, ["a1", "o15"]))
        assert len(board) == gomoku.CELLS
        assert board.startswith("______________w")
        assert board.endswith("b______________")


# --- Reversi rules ---------------------------------------------------------


def reversi_position(side, black, white):
    position = ReversiRules.start()
    position.cells = [EMPTY] * reversi.CELLS
    for name in black:
        position.cells[parse_square(name, 8)] = BLACK
    for name in white:
        position.cells[parse_square(name, 8)] = WHITE
    position.side = side
    return position


def reversi_cell(position, name):
    return position.cells[parse_square(name, 8)]


class TestReversiRules:
    def test_opening_position_and_moves(self):
        start = ReversiRules.start()
        assert ReversiRules.render(start) == (
            "___________________________bw______wb___________________________"
        )
        assert ReversiRules.legal_moves(start) == ["d3", "c4", "f5", "e6"]
        assert ReversiRules.side_to_move(start) == BLACK

    def test_notation_is_strict(self):
        assert ReversiRules.is_canonical("a1")
        assert ReversiRules.is_canonical("h8")
        for bad in ["", "a0", "a9", "i1", "a01", "D3", "d 3"]:
            assert not ReversiRules.is_canonical(bad), bad

    def test_a_move_flips_every_bracketed_line(self):
        game = ReversiRules.start()
        play_checked(ReversiRules, game, "d3")
        assert reversi_cell(game, "d4") == BLACK
        assert reversi_cell(game, "e5") == WHITE
        assert ReversiRules.side_to_move(game) == WHITE

        game = reversi_position(
            BLACK,
            ["a1", "e5", "h1"],
            ["b1", "c1", "d1", "e2", "e3", "e4", "f1", "g1"],
        )
        play_checked(ReversiRules, game, "e1")
        for name in ["b1", "c1", "d1", "e2", "e3", "e4", "f1", "g1"]:
            assert reversi_cell(game, name) == BLACK, name

    def test_unbracketed_and_occupied_squares_are_illegal(self):
        for square in ("a1", "d4"):
            with pytest.raises(ValueError):
                play_checked(ReversiRules, ReversiRules.start(), square)
        with pytest.raises(ValueError):
            play_checked(ReversiRules, reversi_position(BLACK, ["d5"], ["b1", "c1"]), "d1")

    def test_opponent_without_a_move_passes_automatically(self):
        game = reversi_position(BLACK, ["a1", "h8"], ["a2", "g7"])
        play_checked(ReversiRules, game, "a3")
        assert not reversi._has_move(game.cells, WHITE)
        assert ReversiRules.outcome(game) == ("", None)
        assert ReversiRules.side_to_move(game) == BLACK
        assert ReversiRules.legal_moves(game) == ["f6"]
        metadata = {}
        ReversiRules.extra_metadata(game, metadata)
        assert metadata["skipped_turn"] is True
        play_checked(ReversiRules, game, "f6")
        assert game.skipped is False

    def test_game_ends_when_neither_side_can_move(self):
        wipeout = reversi_position(BLACK, ["a1"], ["a2"])
        play_checked(ReversiRules, wipeout, "a3")
        assert ReversiRules.outcome(wipeout) == ("win", BLACK)
        assert ReversiRules.legal_moves(wipeout) == []
        with pytest.raises(ValueError):
            play_checked(ReversiRules, wipeout, "h8")

    def test_the_last_mover_can_lose_on_count(self):
        game = reversi_position(WHITE, ["a2", "h5", "h6", "h7", "h8"], ["a1"])
        play_checked(ReversiRules, game, "a3")
        assert game.cells.count(WHITE) == 3
        assert game.cells.count(BLACK) == 4
        assert ReversiRules.outcome(game) == ("win", BLACK)

    def test_equal_counts_end_in_a_draw(self):
        game = reversi_position(WHITE, ["a2", "h6", "h7", "h8"], ["a1"])
        play_checked(ReversiRules, game, "a3")
        assert ReversiRules.outcome(game) == ("draw", None)


# --- Dots and Boxes rules --------------------------------------------------


def dots_game_of(moves):
    return replay(DotsAndBoxesRules, list(moves))


class TestDotsAndBoxesRules:
    def test_notation_is_strict(self):
        for good in ("a1h", "a1v", "e6h", "f5v", "c3h"):
            assert DotsAndBoxesRules.is_canonical(good), good
        for bad in ("", "h", "a1", "f1h", "a6v", "g1h", "a7h", "a0h", "a01h",
                    "A1h", "a1H", "a1x", "a1 h", "a1hv", None, 3):
            assert not DotsAndBoxesRules.is_canonical(bad), bad

    def test_opening_lists_every_line_in_canonical_order(self):
        legal = DotsAndBoxesRules.legal_moves(DotsAndBoxesRules.start())
        assert len(legal) == 2 * dots_and_boxes.LINES
        assert legal[:4] == ["a1h", "a1v", "b1h", "b1v"]
        # f1 has no line to its right; a6 has none above it.
        assert legal[9:12] == ["e1v", "f1v", "a2h"]
        assert legal[-2:] == ["d6h", "e6h"]

    def test_completing_a_box_claims_it_and_moves_again(self):
        position = dots_game_of(["a1h", "a1v", "a2h"])
        assert DotsAndBoxesRules.side_to_move(position) == WHITE
        assert position.extra_turn is False
        play_checked(DotsAndBoxesRules, position, "b1v")
        assert position.boxes[0] == WHITE
        assert position.extra_turn is True
        assert DotsAndBoxesRules.side_to_move(position) == WHITE
        metadata = {}
        DotsAndBoxesRules.extra_metadata(position, metadata)
        assert metadata["white_boxes"] == 1
        assert metadata["extra_turn"] is True

        play_checked(DotsAndBoxesRules, position, "c1v")
        assert position.extra_turn is False
        assert DotsAndBoxesRules.side_to_move(position) == BLACK
        with pytest.raises(ValueError):
            play_checked(DotsAndBoxesRules, position, "a1h")

    def test_one_line_can_complete_two_boxes(self):
        # b1v is the shared side of a1 and b1.
        position = dots_game_of(["a1h", "a1v", "a2h", "b1h", "b2h", "c1v", "b1v"])
        assert position.boxes[:2] == [BLACK, BLACK]
        assert position.boxes.count(BLACK) == 2
        assert DotsAndBoxesRules.side_to_move(position) == BLACK

    def test_render_draws_lines_and_owners(self):
        board = DotsAndBoxesRules.render(dots_game_of(["a1h", "a1v", "a2h", "b1v"]))
        grid = dots_and_boxes.GRID
        assert len(board) == grid * grid
        rows = [board[row * grid:(row + 1) * grid] for row in range(grid)]
        assert rows[0] == "+ + + + + +"
        assert rows[1] == " _ _ _ _ _ "
        assert rows[8] == "+-+ + + + +"
        assert rows[9] == "|w|_ _ _ _ "
        assert rows[10] == "+-+ + + + +"

    def test_the_game_ends_when_every_box_is_claimed(self):
        every = DotsAndBoxesRules.legal_moves(DotsAndBoxesRules.start())
        position = dots_game_of(every)
        assert DotsAndBoxesRules.outcome(position)[0] == "win"
        assert EMPTY not in position.boxes
        assert DotsAndBoxesRules.legal_moves(position) == []
        with pytest.raises(ValueError):
            play_checked(DotsAndBoxesRules, position, "a1h")


# --- Mancala rules ---------------------------------------------------------


def mancala_position(side, holes):
    position = MancalaRules.start()
    position.holes = list(holes)
    position.side = side
    return position


class TestMancalaRules:
    def test_notation_is_strict(self):
        from lrgp.apps.mancala import _opposite, _parse_pit, _pit_name

        for letter, hole in (("a", 0), ("f", 5), ("g", 7), ("l", 12)):
            assert _parse_pit(letter) == hole
            assert _pit_name(hole) == letter
        for bad in ("", "m", "A", "aa", "a1", " a", "1", None, 3):
            assert not MancalaRules.is_canonical(bad), bad
        assert _opposite(0) == 12
        assert _opposite(5) == 7

    def test_opening_position_and_moves(self):
        start = MancalaRules.start()
        assert MancalaRules.render(start) == "4,4,4,4,4,4,0,4,4,4,4,4,4,0"
        assert MancalaRules.legal_moves(start) == list("abcdef")

    def test_sowing_passes_the_turn(self):
        position = replay(MancalaRules, ["a"])
        assert MancalaRules.render(position) == "0,5,5,5,5,4,0,4,4,4,4,4,4,0"
        assert MancalaRules.side_to_move(position) == WHITE
        assert MancalaRules.legal_moves(position) == list("ghijkl")
        for illegal in ("a", "b"):
            with pytest.raises(ValueError):
                play_checked(MancalaRules, position, illegal)

    def test_ending_in_the_own_store_moves_again(self):
        # c holds four seeds: d, e, f, store.
        position = replay(MancalaRules, ["c"])
        assert MancalaRules.render(position) == "4,4,0,5,5,5,1,4,4,4,4,4,4,0"
        assert position.extra_turn is True
        assert MancalaRules.side_to_move(position) == BLACK
        metadata = {}
        MancalaRules.extra_metadata(position, metadata)
        assert metadata["black_store"] == 1
        assert metadata["extra_turn"] is True

        play_checked(MancalaRules, position, "a")
        assert position.extra_turn is False
        assert MancalaRules.side_to_move(position) == WHITE

    def test_sowing_skips_the_opponent_store(self):
        # Ten seeds from l: White's store, a-f, (Black's store skipped), g, h, i.
        holes = [1] * mancala.HOLES
        holes[mancala.BLACK_STORE] = 0
        holes[mancala.WHITE_STORE] = 0
        holes[12] = 10
        position = mancala_position(WHITE, holes)
        play_checked(MancalaRules, position, "l")
        assert MancalaRules.render(position) == "2,2,2,2,2,2,0,2,2,2,1,1,0,1"

    def test_ending_in_an_own_empty_pit_captures_the_opposite_pit(self):
        # a's single seed lands in empty b, facing k's five seeds.
        position = mancala_position(BLACK, [1, 0, 3, 0, 0, 0, 0, 2, 0, 0, 0, 5, 0, 0])
        play_checked(MancalaRules, position, "a")
        assert MancalaRules.render(position) == "0,0,3,0,0,0,6,2,0,0,0,0,0,0"
        assert MancalaRules.side_to_move(position) == WHITE

        # Facing an empty pit captures nothing.
        position = mancala_position(BLACK, [1, 0, 3, 0, 0, 0, 0, 2, 0, 0, 0, 0, 5, 0])
        play_checked(MancalaRules, position, "a")
        assert MancalaRules.render(position) == "0,1,3,0,0,0,0,2,0,0,0,0,5,0"

    def test_an_empty_side_ends_the_game_and_sweeps_the_rest(self):
        # f's last seed reaches the store and empties Black's side; White
        # sweeps its six seeds and wins 7 to 6.
        position = mancala_position(BLACK, [0, 0, 0, 0, 0, 1, 5, 6, 0, 0, 0, 0, 0, 1])
        play_checked(MancalaRules, position, "f")
        assert MancalaRules.render(position) == "0,0,0,0,0,0,6,0,0,0,0,0,0,7"
        assert MancalaRules.outcome(position) == ("win", WHITE)
        assert position.extra_turn is False
        assert MancalaRules.legal_moves(position) == []

        draw = mancala_position(BLACK, [0, 0, 0, 0, 0, 1, 6, 6, 0, 0, 0, 0, 0, 1])
        play_checked(MancalaRules, draw, "f")
        assert MancalaRules.outcome(draw) == ("draw", None)


# --- Shared lifecycle (every game) ----------------------------------------


class TestLifecycle:
    def test_manifest_is_canonical(self, app):
        manifest = app.get_manifest()
        assert manifest["app_id"] == app.app_id
        assert manifest["version"] == 1
        assert manifest["display_name"] == app.display_name
        assert manifest["min_players"] == manifest["max_players"] == 2
        assert manifest["validation"] == "both"
        assert len(manifest["actions"]) == 9
        assert set(manifest["preferred_delivery"]) == set(manifest["actions"])
        assert manifest["preferred_delivery"][CMD_MOVE] == "opportunistic"
        assert manifest["preferred_delivery"][CMD_RESIGN] == "direct"

    @pytest.mark.parametrize("challenger_is_black", [True, False])
    def test_accept_assigns_colors_from_coin(self, game, challenger_is_black):
        app_class, module = game
        module.force_coin(challenger_is_black)
        app = app_class()
        accept = activate(app)
        black = CHALLENGER if challenger_is_black else RESPONDER
        assert accept == {"b": black}
        for identity in (CHALLENGER, RESPONDER):
            session = app._get_session(SESSION, identity)
            assert session.status == STATUS_ACTIVE
            assert session.metadata["first_turn"] == black
            assert session.metadata["turn"] == black
            assert session.metadata["my_color"] == (BLACK if identity == black else WHITE)
            assert session.metadata["legal_moves"] == app.rules.legal_moves(
                app.rules.start()
            )
        assert_shared_state(app)

    def test_accept_with_unbound_black_player_is_rejected(self, app):
        app.handle_outgoing(SESSION, CMD_CHALLENGE, {}, CHALLENGER)
        app.bind_peer(SESSION, CHALLENGER, RESPONDER)
        result = app.handle_incoming(SESSION, CMD_ACCEPT, {"b": "cc"}, RESPONDER, CHALLENGER)
        assert result["error"]["code"] == ERR_PROTOCOL_ERROR
        assert app._get_session(SESSION, CHALLENGER).status == STATUS_PENDING

    def test_decline_and_cancel(self, app):
        app.handle_outgoing(SESSION, CMD_CHALLENGE, {}, CHALLENGER)
        app.handle_incoming(SESSION, CMD_CHALLENGE, {}, CHALLENGER, RESPONDER)
        _wire, fallback = app.handle_outgoing(SESSION, CMD_DECLINE, {}, RESPONDER)
        assert fallback == "[LRGP {}] Challenge declined".format(app.display_name)
        app.handle_incoming(SESSION, CMD_DECLINE, {}, RESPONDER, CHALLENGER)
        for identity in (CHALLENGER, RESPONDER):
            assert app._get_session(SESSION, identity).status == STATUS_DECLINED

        app.handle_outgoing("1111111111111111", CMD_CHALLENGE, {}, CHALLENGER)
        _wire, fallback = app.handle_outgoing(
            "1111111111111111", CMD_DECLINE, {}, CHALLENGER
        )
        assert fallback == "[LRGP {}] Challenge cancelled".format(app.display_name)

    def test_out_of_turn_illegal_and_malformed_intents_are_rejected(self, app):
        activate(app)
        legal = meta(app, CHALLENGER)["legal_moves"]
        with pytest.raises(OutgoingActionError) as error:
            app.validate_outgoing(SESSION, CMD_MOVE, {"m": legal[0]}, RESPONDER)
        assert error.value.code == ERR_NOT_YOUR_TURN
        wire, fallback = app.handle_outgoing(SESSION, CMD_MOVE, {"m": legal[0]}, RESPONDER)
        assert wire == {}
        assert fallback == "[LRGP {}] Not your turn".format(app.display_name)

        for bad in ["", "A1", "a0", "a01", "zz", "swap ", "a1 "]:
            with pytest.raises(OutgoingActionError) as error:
                app.validate_outgoing(SESSION, CMD_MOVE, {"m": bad}, CHALLENGER)
            assert error.value.code == ERR_INVALID_MOVE
        for intent in ({}, {"m": 3}, {"m": legal[0], "n": 0}):
            with pytest.raises(OutgoingActionError) as error:
                app.validate_outgoing(SESSION, CMD_MOVE, intent, CHALLENGER)
            assert error.value.code == ERR_PROTOCOL_ERROR

        play(app, legal[0], CHALLENGER)
        mover = meta(app, CHALLENGER)["turn"]
        with pytest.raises(OutgoingActionError):
            app.validate_outgoing(SESSION, CMD_MOVE, {"m": legal[0]}, mover)

    def test_forged_wire_moves_are_rejected_without_mutation(self, app):
        activate(app)
        play_any(app)
        metadata = meta(app, CHALLENGER)
        mover = metadata["turn"]
        receiver = other(mover)
        legal = metadata["legal_moves"]
        occupied = metadata["last_move"]
        before = copy.deepcopy(meta(app, receiver))

        def wire(notation, ply, terminal=""):
            return {"m": notation, "n": ply, "x": terminal}

        cases = [
            (wire(legal[0], 0), mover),
            (wire(legal[0], 2), mover),
            (wire(occupied, 1), mover),
            (wire("A1", 1), mover),
            (wire(legal[0], 1), receiver),
            (wire(legal[0], 1, "draw"), mover),
            (dict(wire(legal[0], 1, "win"), w=mover), mover),
            (dict(wire(legal[0], 1), extra=None), mover),
            (wire(legal[0], True), mover),
            (wire(legal[0], -1), mover),
        ]
        for payload, sender in cases:
            result = app.handle_incoming(SESSION, CMD_MOVE, payload, sender, other(sender))
            assert result["error"] is not None, payload
        assert meta(app, receiver) == before

        result = app.handle_incoming(
            "2222222222222222", CMD_CHALLENGE, {"extra": True}, CHALLENGER, RESPONDER
        )
        assert result["error"]["code"] == ERR_PROTOCOL_ERROR
        assert app._get_session("2222222222222222", RESPONDER) is None

    def test_empty_turn_fails_closed(self, app):
        activate(app)
        session = app._get_session(SESSION, RESPONDER)
        first = session.metadata["legal_moves"][0]
        session.metadata["turn"] = ""
        valid, message, _position = app._validate_move_details(
            session, {"m": first, "n": 0, "x": ""}, CHALLENGER
        )
        assert not valid
        assert "Turn is required" in message

    def test_resign_completes_both_sides(self, app):
        activate(app)
        _wire, fallback = app.handle_outgoing(SESSION, CMD_RESIGN, {}, CHALLENGER)
        assert fallback == "[LRGP {}] Resigned.".format(app.display_name)
        app.handle_incoming(SESSION, CMD_RESIGN, {}, CHALLENGER, RESPONDER)
        for identity in (CHALLENGER, RESPONDER):
            session = app._get_session(SESSION, identity)
            assert session.status == STATUS_COMPLETED
            assert session.metadata["terminal"] == "win"
            assert session.metadata["terminal_reason"] == "rsn"
            assert session.metadata["winner"] == RESPONDER
            assert session.metadata["turn"] == ""
            type(app)().upsert_session(session.to_dict())
        assert_shared_state(app)

    def test_draw_offer_ownership_and_agreement(self, app):
        activate(app)
        app.validate_outgoing(SESSION, CMD_DRAW_OFFER, {}, CHALLENGER)
        app.handle_outgoing(SESSION, CMD_DRAW_OFFER, {}, CHALLENGER)
        offer = app.handle_incoming(SESSION, CMD_DRAW_OFFER, {}, CHALLENGER, RESPONDER)
        assert offer["error"] is None
        assert meta(app, RESPONDER)["draw_offered_by"] == CHALLENGER

        for command in (CMD_DRAW_ACCEPT, CMD_DRAW_OFFER):
            with pytest.raises(OutgoingActionError):
                app.validate_outgoing(SESSION, command, {}, CHALLENGER)
        for command in (CMD_DRAW_OFFER, CMD_DRAW_ACCEPT):
            result = app.handle_incoming(SESSION, command, {}, CHALLENGER, RESPONDER)
            assert result["error"] is not None

        app.validate_outgoing(SESSION, CMD_DRAW_ACCEPT, {}, RESPONDER)
        _wire, fallback = app.handle_outgoing(SESSION, CMD_DRAW_ACCEPT, {}, RESPONDER)
        assert fallback == "[LRGP {}] Draw accepted".format(app.display_name)
        app.handle_incoming(SESSION, CMD_DRAW_ACCEPT, {}, RESPONDER, CHALLENGER)
        for identity in (CHALLENGER, RESPONDER):
            session = app._get_session(SESSION, identity)
            assert session.status == STATUS_COMPLETED
            assert session.metadata["terminal"] == "draw"
            assert session.metadata["terminal_reason"] == "agr"
            assert session.metadata["draw_offered"] is False
            type(app)().upsert_session(session.to_dict())
        assert_shared_state(app)

    def test_draw_decline_and_move_clear_the_offer(self, app):
        activate(app)
        app.handle_outgoing(SESSION, CMD_DRAW_OFFER, {}, RESPONDER)
        app.handle_incoming(SESSION, CMD_DRAW_OFFER, {}, RESPONDER, CHALLENGER)
        app.handle_outgoing(SESSION, CMD_DRAW_DECLINE, {}, CHALLENGER)
        app.handle_incoming(SESSION, CMD_DRAW_DECLINE, {}, CHALLENGER, RESPONDER)
        assert not meta(app, CHALLENGER)["draw_offered"]
        assert not meta(app, RESPONDER)["draw_offered"]

        app.handle_outgoing(SESSION, CMD_DRAW_OFFER, {}, RESPONDER)
        app.handle_incoming(SESSION, CMD_DRAW_OFFER, {}, RESPONDER, CHALLENGER)
        play_any(app)
        assert not meta(app, CHALLENGER)["draw_offered"]
        assert not meta(app, RESPONDER)["draw_offered"]
        assert_shared_state(app)

    def test_router_prepares_the_compact_move_wire(self, game):
        app_class, _module = game
        app = app_class()
        router = LrgpRouter()
        router.register(app)
        router.dispatch_outgoing_to(
            app.app_id, CMD_CHALLENGE, {}, SESSION, CHALLENGER, RESPONDER
        )
        accept = pack_envelope(
            app.app_id, 1, CMD_ACCEPT, SESSION, {"b": CHALLENGER}, nonce=b"accept01"
        )
        applied = router.dispatch_incoming(accept, RESPONDER, CHALLENGER)
        assert applied["session"]["status"] == STATUS_ACTIVE
        first = app.rules.legal_moves(app.rules.start())[0]
        prepared = router.dispatch_outgoing_to(
            app.app_id, CMD_MOVE, {"m": first}, SESSION, CHALLENGER, RESPONDER
        )
        assert prepared.envelope["p"] == {"m": first, "n": 0, "x": ""}
        assert prepared.delivery_method == "opportunistic"

    def test_render_fallback(self, app):
        prefix = "[LRGP {}]".format(app.display_name)
        assert app.render_fallback(CMD_CHALLENGE, {}) == prefix + " Sent a challenge!"
        assert app.render_fallback(CMD_MOVE, {"m": "c3", "x": ""}) == prefix + " c3"
        assert app.render_fallback(CMD_MOVE, {"m": "c3", "x": "win"}) == (
            prefix + " c3 (game won)"
        )
        assert app.render_fallback(CMD_MOVE, {"m": "c3", "x": "draw"}) == (
            prefix + " c3 (game drawn)"
        )

    def test_worst_case_move_fits_envelope_and_opportunistic_budget(self, app):
        longest = app.rules.LONGEST_MOVE
        assert app.rules.is_canonical(longest)
        payload = {"m": longest, "n": 65535, "x": "win", "w": CHALLENGER}
        envelope = pack_envelope(app.app_id, 1, CMD_MOVE, SESSION, payload)
        assert validate_envelope_size(envelope) <= 150 <= ENVELOPE_MAX_PACKED
        content_size = measure_content_size(
            "", app.render_fallback(CMD_MOVE, payload), pack_lxmf_fields(envelope)
        )
        assert content_size <= OPPORTUNISTIC_MAX_CONTENT


def test_package_discovery_and_public_exports():
    import lrgp.apps
    from lrgp.apps import (
        DotsAndBoxesApp as D,
        GomokuApp as G,
        HexApp as H,
        MancalaApp as M,
        ReversiApp as R,
    )

    assert (D, G, H, M, R) == (DotsAndBoxesApp, GomokuApp, HexApp, MancalaApp, ReversiApp)
    router = LrgpRouter()
    router.discover(lrgp.apps)
    for app_id, (app_class, module) in GAMES.items():
        assert isinstance(router.get_app(app_id), app_class)
        for name in module.__all__:
            assert hasattr(module, name)
    # The shared engine is abstract and never registered.
    assert not PlacementApp.app_id


class TestHydration:
    def _record(self, app):
        activate(app)
        play_any(app)
        play_any(app)
        return app._get_session(SESSION, RESPONDER).to_dict()

    def test_valid_record_hydrates_and_can_continue(self, app):
        record = self._record(app)
        restored = type(app)().upsert_session(copy.deepcopy(record))
        assert restored.metadata == record["metadata"]

    @pytest.mark.parametrize(
        "field",
        ["board", "turn", "move_count", "my_color", "first_turn", "terminal",
         "last_move", "moves", "moves_str", "move_count_bool"],
    )
    def test_tampering_is_rejected(self, app, field):
        record = self._record(app)
        metadata = record["metadata"]
        moves = metadata["moves"]
        tampered = {
            "board": app.rules.render(app.rules.start()),
            "turn": other(metadata["turn"]),
            "move_count": 1,
            "my_color": BLACK,
            "first_turn": RESPONDER,
            "terminal": "win",
            "last_move": moves[0],
            "moves": [moves[0], moves[0]],
            "moves_str": moves[0],
            "move_count_bool": True,
        }[field]
        metadata[field.split("_str")[0].split("_bool")[0]] = tampered
        with pytest.raises(InvalidEnvelope):
            type(app)().upsert_session(record)

    def test_unverified_board_draw_is_rejected(self, app):
        record = self._record(app)
        record["status"] = STATUS_COMPLETED
        record["metadata"].update(
            {"turn": "", "terminal": "draw", "terminal_reason": app.rules.DRAW_REASON}
        )
        with pytest.raises(InvalidEnvelope):
            type(app)().upsert_session(record)

    def test_legacy_ownerless_draw_flag_is_normalized(self, app):
        record = self._record(app)
        record["metadata"]["draw_offered"] = True
        assert type(app)().upsert_session(record).metadata["draw_offered"] is False

    def test_wrong_app_or_session_id_is_rejected(self, app):
        record = self._record(app)
        for key, value in (("app_id", "chess"), ("session_id", "g1")):
            bad = copy.deepcopy(record)
            bad[key] = value
            with pytest.raises(InvalidEnvelope):
                type(app)().upsert_session(bad)

    def test_expiry_clears_draw_offer_and_round_trips(self, app):
        activate(app)
        app.handle_outgoing(SESSION, CMD_DRAW_OFFER, {}, CHALLENGER)
        record = app._get_session(SESSION, CHALLENGER).to_dict()
        assert record["metadata"]["draw_offered"] is True
        record["last_action_at"] = 0.0

        expired = type(app)().upsert_session(record)
        assert expired.status == STATUS_EXPIRED
        assert expired.metadata["draw_offered"] is False
        type(app)().upsert_session(expired.to_dict())

        stale = expired.to_dict()
        stale["metadata"].update({"draw_offered": True, "draw_offered_by": CHALLENGER})
        assert type(app)().upsert_session(stale).metadata["draw_offered"] is False


# --- Cross-language vectors and reference games ---------------------------


class TestVectors:
    DIGESTS = {
        "hex_accept.bin": "a84ac9e5dfdeed43eff70813bdcf8ba335cb0bc118fa87f47560c441c8af68e3",
        "hex_move.bin": "b9b3f7d027c2eaae5fe88c4147541ea04cb5f2f8c37129c62324d5fd2bc210e5",
        "hex_move_win.bin": "42a854e2f5fdcebf18fefd2981d64d1a6224f44377b5af78bbac6bcaf682c60c",
        "hex_swap.bin": "5f30e1d322c4b0aae5879c8f5b9c28f1726f5a701783c6c7e9e1aaf6504dadc7",
        "hex_reference.txt": "163b3acc3f5a3a707b76419173024cd964064bba11e95e912d29791bb8a42328",
        "gomoku_accept.bin": "18f9e8c1b3da95e93d608a3cbbd343878ce7bb090339b1fd13d03b96b292fc07",
        "gomoku_move.bin": "483efc467746aaea9dd94916a615975e88251c9487155e7211c5174aec4c5c95",
        "gomoku_move_win.bin": "00c284b09a24c5624daae7f76369397c266f41f427799763707b43e3a3ef7fb7",
        "gomoku_move_draw.bin": "c4fc709f7738291b6e109d20efeb0e555aabf4e644180537859826e761bcb2be",
        "gomoku_reference.txt": "109dcf71eb25b990a09a09228cc9aea92f8ae5963361f9bb3a396799ce09fb46",
        "gomoku_draw.txt": "dd6cf9f3185ab8957b2530b3db38cce1094600f12c2eb2f23dac946ff6ada145",
        "reversi_accept.bin": "25658d4ef22060b7bbb5c08da4b1e07f8827ef87bdf5adc2713a1cd34a320742",
        "reversi_move.bin": "c3fc956775a142132a85863146e0fd12bc26f14c345d05fcea86419d8bf32f9d",
        "reversi_move_win.bin": "c09e2a32f78823d3a4859b405ed49bdb18369107d583d77d173a15c326cf33d8",
        "reversi_reference.txt": "453b645dfbab6fd940121e8daff91f29a6d8bef56ea3b1413c0bd07dc42476ec",
        "dots_and_boxes_accept.bin": "168789197b211ee2bd2a5a800857d1e92704ee1db789cc5d661474c812641aad",
        "dots_and_boxes_move.bin": "baca0b464ddc11bb06454dada0a80af31e7979c291c798b5c08d83fb8e12b17e",
        "dots_and_boxes_move_win.bin": "1f4434a8124e72b3a3775e77cb61dddabcd1caa277b387dc6eab5239fbdfbfad",
        "dots_and_boxes_reference.txt": "2865e9368a310f844aef9e2802c0bb6a261c5c7aea305be846a6549c2490b557",
        "mancala_accept.bin": "520effa5bfe5e3165a4c72aef4f74615f748c08b2e8add652b663d9a0adfffc2",
        "mancala_move.bin": "ebf34d9dd6cf6b108f632fe265651a10c4576070a6fa06719089b156209a86f7",
        "mancala_move_win.bin": "b8eae12cd558af9f23a5db345d6f147096d62eb40f9d3ee6f0b3e16fd909421f",
        "mancala_move_draw.bin": "07560a7ce377ecf8ae8793fbfac3eb6c94a444c462b87a2dddc634b7d5375f79",
        "mancala_reference.txt": "8a33c3d6305ec2ca8df58a2c1e9c435c6becd31b6f7e0a1bf8a7c8f9cf8a069a",
        "mancala_draw.txt": "f7a5bbdf0bfea348e4c05c598095768986d91b8b7616a84fbd8669994762cf15",
        "ultimate_ttt_accept.bin": "0c62f4225a749075853c7338194df4f1b46bff9e8905543b9d13cba52a8b32c3",
        "ultimate_ttt_draw.txt": "64b50991d474f6b260f20b60abf63cecf4187f02e6aa41c3bb8c74507bb932db",
        "ultimate_ttt_move.bin": "c0bfed255122162ef21c06d9b807abec75f9d0dc5155531dfa7451cff706eff6",
        "ultimate_ttt_move_draw.bin": "a3bb4eb36f261cbbc8fd2094d54596de7c267522513f15aa905817fe65b85a7c",
        "ultimate_ttt_move_win.bin": "9b2c666d2af8caae179621b4b3a80ef7c9e01091bb427f345365d87fc4765331",
        "ultimate_ttt_reference.txt": "41c8d98faad227e69047c4845187108f6d53a30bb98525dcd93f4a537fe77e6f",
        "go_accept.bin": "eb0f225305d05f85b99a9e9a6134dfd42d785c2ec5086a4239bff3b1a8712cce",
        "go_move.bin": "61e7703eec7814b35da3e6f98c07066b94bd6e1dd863342012b9c0627ea817a2",
        "go_move_win.bin": "5cdc199d19dfbe175d363c39a829450787732754d6790798a2c7191740cd8060",
        "go_reference.txt": "95466c2bc151caa9d02e9f2a38f7efea6ff9486d91a4bb45ff61420fe4babfc1",
        "quoridor_accept.bin": "ecc926341fddca65e657cb83bfcf1b036736c7124b8aad6b327a73c46a07ae9a",
        "quoridor_draw.txt": "00f722e8605f8a9cb260ee372c032d4ed61de96d084476695c663e7d9a1607b5",
        "quoridor_move.bin": "03b2b988958f3f288274ecc7efd44b77a11d580223f502016b93c3366783b2dd",
        "quoridor_move_draw.bin": "0e3392a35b693be61b02ddc10d3bd075089dec815bb897ecb73b4f97e99ef19c",
        "quoridor_move_win.bin": "a3bea13b41c88e3d82fe8201fd50c03d18c4365f94527257979985991e50d159",
        "quoridor_reference.txt": "3e6ada9b7f8709514c2cd414bbb1fd7fb90aa8ba2ea5657a1c582abb311644cb",
        "morris_accept.bin": "f9e97db46ddbfc591ab251585906167bd51010f3a2b63781b3db56483de97c22",
        "morris_draw.txt": "d20b522c99f1e472b2caa28cbd15e7be4276913e2f34cab1e046a353b7a7d354",
        "morris_move.bin": "0d450dec389e318e05720bf5fc57a0c31b1c8d163515810fc6976d3f06da5c9e",
        "morris_move_draw.bin": "f7e0591c6ec960e640d0fa5a3fe542e6192c84c3fc129314485b212953169ddc",
        "morris_move_win.bin": "8d9da863c261f7a8ef0fe8ca9542b73b30d64293aeb7104721862031e9accdd5",
        "morris_reference.txt": "0cbd4edd67ef07ffd7f86d7aa912b6b905ea5613ca396086615f3b4a58fbcd5a",
        "breakthrough_accept.bin": "98ecee0e34125871243ebb77815159b977340cabd5efea1277017b0685549777",
        "breakthrough_move.bin": "38dd2d1ffe01c32b09fe1c7630aba984eec116603cf007cc51e9ccc9f83a527d",
        "breakthrough_move_win.bin": "1b0c7e19ae8e4680906768ad076e4e22b7a130f8d16e1dac53d600fcf592ce07",
        "breakthrough_reference.txt": "073e7fcc36188ca8ab59a5b3b87e5213b2c0353fdbddb385edf00c2d6c9d98ea",
        "amazons_accept.bin": "e7304c4a7ba4a2d37cc18fa5817ff681477dd64dc08525d7c5c998b8640765f4",
        "amazons_move.bin": "2bbeb49fb58267f5e15b2897b03a96ef2daf27d0679b4a3383869ef06ac92d8a",
        "amazons_move_win.bin": "69c80f8cd3900bf0fbc3776fc9ad6483320365e7a593b84e0e7dba2c3b4a6770",
        "amazons_reference.txt": "fd43e811b6cfb4a2531b164afa4193b94cd400a3148f04faaa3452c277577048",
    }

    @staticmethod
    def _raw(name):
        with open(os.path.join(VECTORS_DIR, name), "rb") as vector:
            return vector.read()

    def test_every_fixture_is_locked(self):
        generated = set(generate_placement.vectors()) | set(generate_placement.move_lists())
        assert generated == set(self.DIGESTS)

    @pytest.mark.parametrize("filename", sorted(DIGESTS))
    def test_fixture_bytes_have_locked_cross_language_digest(self, filename):
        assert hashlib.sha256(self._raw(filename)).hexdigest() == self.DIGESTS[filename]

    @pytest.mark.parametrize("filename", sorted(generate_placement.move_lists()))
    def test_generator_reproduces_move_lists(self, filename):
        moves = generate_placement.move_lists()[filename]
        assert self._raw(filename) == ("\n".join(moves) + "\n").encode("ascii")

    @pytest.mark.parametrize("filename", sorted(generate_placement.vectors()))
    def test_generator_reproduces_and_decodes_vectors(self, filename):
        app_id, command, payload, nonce = generate_placement.vectors()[filename]
        envelope = pack_envelope(app_id, 1, command, SESSION, payload, nonce=nonce)
        assert packb(envelope) == self._raw(filename)
        decoded = unpack_envelope(pack_lxmf_fields(unpackb(self._raw(filename))))
        assert decoded["a"] == "{}.1".format(app_id)
        assert decoded["c"] == command
        assert decoded["p"] == payload
        assert decoded["n"] == nonce
        assert GAMES[app_id][0]()._incoming_payload_error(command, payload) is None

    def test_reference_game_contents(self):
        hex_moves = read_moves("hex_reference.txt")
        assert hex_moves[1] == SWAP
        position = replay(ReversiRules, [])
        skipped = []
        for ply, move in enumerate(read_moves("reversi_reference.txt")):
            mover = ReversiRules.side_to_move(position)
            play_checked(ReversiRules, position, move)
            if position.skipped:
                skipped.append(ply)
        assert skipped == [56]
        # Black (the challenger) made the last move but White wins on count.
        assert mover == BLACK
        assert ReversiRules.outcome(position) == ("win", WHITE)

        # Dots and Boxes: extra turns, at least one double box, and White
        # wins although Black draws the last line.
        position = DotsAndBoxesRules.start()
        extra_turns = doubles = 0
        for move in read_moves("dots_and_boxes_reference.txt"):
            mover = DotsAndBoxesRules.side_to_move(position)
            claimed = position.boxes.count(EMPTY)
            play_checked(DotsAndBoxesRules, position, move)
            extra_turns += position.extra_turn
            doubles += claimed - position.boxes.count(EMPTY) == 2
        assert extra_turns and doubles
        assert mover == BLACK
        assert DotsAndBoxesRules.outcome(position) == ("win", WHITE)

        # Mancala: extra turns, captures, and White wins after Black's last move.
        position = MancalaRules.start()
        extra_turns = captures = 0
        for move in read_moves("mancala_reference.txt"):
            mover = MancalaRules.side_to_move(position)
            store = mancala.BLACK_STORE if mover == BLACK else mancala.WHITE_STORE
            before = position.holes[store]
            play_checked(MancalaRules, position, move)
            extra_turns += position.extra_turn
            captures += not position.extra_turn and position.holes[store] - before > 1
        assert extra_turns and captures
        assert mover == BLACK
        assert MancalaRules.outcome(position) == ("win", WHITE)

    @pytest.mark.parametrize("name", sorted(GAMES))
    def test_reference_game_converges_through_both_peers(self, name):
        app_class, module = GAMES[name]
        module.force_coin(True)
        try:
            app = app_class()
            activate(app)
        finally:
            module.force_coin(None)
        reference = read_moves("{}_reference.txt".format(name))
        last = None
        for ply, move in enumerate(reference):
            metadata = meta(app, CHALLENGER)
            legal = metadata["legal_moves"]
            assert legal[(ply * 7 + SEEDS[name]) % len(legal)] == move
            last = play(app, move, metadata["turn"])
            assert last["n"] == ply
            assert_shared_state(app)
        assert last == generate_placement.vectors()["{}_move_win.bin".format(name)][2]
        for identity in (CHALLENGER, RESPONDER):
            session = app._get_session(SESSION, identity)
            assert session.status == STATUS_COMPLETED
            assert session.metadata["winner"] == RESPONDER
            assert session.metadata["terminal_reason"] == terminal_reason(
                app.rules, replay(app.rules, reference)
            )
            assert session.metadata["legal_moves"] == []
            app_class().upsert_session(session.to_dict())

    @pytest.mark.parametrize(
        ("name", "reason"),
        [
            ("gomoku", "ful"),
            ("morris", "50m"),
            ("mancala", "cnt"),
            ("ultimate_ttt", "ful"),
            ("quoridor", "3fr"),
        ],
    )
    def test_draw_game_converges_through_both_peers(self, name, reason):
        app_class, module = GAMES[name]
        module.force_coin(True)
        try:
            app = app_class()
            activate(app)
        finally:
            module.force_coin(None)
        last = None
        for move in read_moves("{}_draw.txt".format(name)):
            last = play(app, move, meta(app, CHALLENGER)["turn"])
        assert last == generate_placement.vectors()["{}_move_draw.bin".format(name)][2]
        session = app._get_session(SESSION, RESPONDER)
        assert session.status == STATUS_COMPLETED
        assert session.metadata["terminal"] == "draw"
        assert session.metadata["terminal_reason"] == reason
        app_class().upsert_session(session.to_dict())
