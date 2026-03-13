"""Tests for Tic-Tac-Toe app — full game sequences and validation."""

import pytest
from lrgp.apps.tictactoe import (
    TicTacToeApp, _check_winner, _check_draw, _marker_for_move,
    EMPTY_BOARD, WIN_LINES,
)
from lrgp.constants import (
    STATUS_PENDING, STATUS_ACTIVE, STATUS_COMPLETED, STATUS_DECLINED,
    CMD_CHALLENGE, CMD_ACCEPT, CMD_DECLINE, CMD_MOVE, CMD_RESIGN,
    CMD_DRAW_OFFER, CMD_DRAW_ACCEPT, CMD_DRAW_DECLINE,
)


@pytest.fixture
def app():
    return TicTacToeApp()


CHALLENGER = "challenger_hash"
RESPONDER = "responder_hash"
SESSION = "test_session_id"


def start_game(app, session_id=SESSION):
    """Helper: create a game through challenge + accept."""
    # Challenger sends challenge (outgoing)
    app.handle_incoming(session_id, CMD_CHALLENGE, {}, CHALLENGER, RESPONDER)

    # Responder accepts (outgoing from responder's perspective)
    # But for testing, we simulate the accept arriving at challenger
    session = app._get_session(session_id, RESPONDER)
    assert session is not None
    assert session.status == STATUS_PENDING

    # Responder accepts
    result = app.handle_incoming(session_id, CMD_ACCEPT,
                                  {"b": EMPTY_BOARD, "t": CHALLENGER},
                                  RESPONDER, RESPONDER)
    return result


class TestBoardHelpers:
    def test_empty_board_no_winner(self):
        assert _check_winner(EMPTY_BOARD) is None

    def test_row_wins(self):
        assert _check_winner("XXX______") == "X"
        assert _check_winner("___OOO___") == "O"
        assert _check_winner("______XXX") == "X"

    def test_column_wins(self):
        assert _check_winner("X__X__X__") == "X"
        assert _check_winner("_O__O__O_") == "O"
        assert _check_winner("__X__X__X") == "X"

    def test_diagonal_wins(self):
        assert _check_winner("X___X___X") == "X"
        assert _check_winner("__O_O_O__") == "O"

    def test_all_win_lines(self):
        for line in WIN_LINES:
            board = list("_________")
            for pos in line:
                board[pos] = "X"
            assert _check_winner("".join(board)) == "X"

    def test_no_draw_with_empty(self):
        assert _check_draw("XOXOXOX__") is False

    def test_draw_full_board(self):
        assert _check_draw("XOXXOOOXX") is True

    def test_full_board_with_winner_not_draw(self):
        assert _check_draw("XXXOO____") is False  # not full
        # Full board with winner
        assert _check_draw("XXXOOXOOX") is False  # X wins top row

    def test_marker_for_move(self):
        assert _marker_for_move(1) == "X"
        assert _marker_for_move(2) == "O"
        assert _marker_for_move(3) == "X"
        assert _marker_for_move(9) == "X"


class TestChallengeFlow:
    def test_incoming_challenge(self, app):
        result = app.handle_incoming(SESSION, CMD_CHALLENGE, {},
                                      CHALLENGER, RESPONDER)
        assert result["error"] is None
        assert result["session"]["status"] == STATUS_PENDING
        assert result["session"]["metadata"]["my_marker"] == "O"
        assert result["emit"]["type"] == "challenge"

    def test_decline(self, app):
        app.handle_incoming(SESSION, CMD_CHALLENGE, {}, CHALLENGER, RESPONDER)
        result = app.handle_incoming(SESSION, CMD_DECLINE, {},
                                      RESPONDER, RESPONDER)
        assert result["session"]["status"] == STATUS_DECLINED

    def test_accept(self, app):
        app.handle_incoming(SESSION, CMD_CHALLENGE, {}, CHALLENGER, RESPONDER)
        result = app.handle_incoming(SESSION, CMD_ACCEPT,
                                      {"b": EMPTY_BOARD, "t": CHALLENGER},
                                      RESPONDER, RESPONDER)
        assert result["error"] is None
        assert result["session"]["status"] == STATUS_ACTIVE


class TestMoveValidation:
    def test_valid_first_move(self, app):
        start_game(app)
        result = app.handle_incoming(SESSION, CMD_MOVE, {
            "i": 4, "b": "____X____", "n": 1, "t": RESPONDER, "x": "",
        }, CHALLENGER, RESPONDER)
        assert result["error"] is None
        assert result["session"]["metadata"]["board"] == "____X____"

    def test_wrong_turn_rejected(self, app):
        start_game(app)
        # Responder tries to move but it's challenger's turn
        result = app.handle_incoming(SESSION, CMD_MOVE, {
            "i": 0, "b": "O________", "n": 1, "t": CHALLENGER, "x": "",
        }, RESPONDER, RESPONDER)
        assert result["error"] is not None
        assert result["error"]["code"] == "invalid_move"

    def test_occupied_cell_rejected(self, app):
        start_game(app)
        # Challenger makes first move
        app.handle_incoming(SESSION, CMD_MOVE, {
            "i": 4, "b": "____X____", "n": 1, "t": RESPONDER, "x": "",
        }, CHALLENGER, RESPONDER)
        # Responder tries to play same cell
        result = app.handle_incoming(SESSION, CMD_MOVE, {
            "i": 4, "b": "____O____", "n": 2, "t": CHALLENGER, "x": "",
        }, RESPONDER, RESPONDER)
        assert result["error"] is not None

    def test_board_mismatch_rejected(self, app):
        start_game(app)
        result = app.handle_incoming(SESSION, CMD_MOVE, {
            "i": 4, "b": "X________", "n": 1, "t": RESPONDER, "x": "",
        }, CHALLENGER, RESPONDER)
        assert result["error"] is not None

    def test_wrong_move_number_rejected(self, app):
        start_game(app)
        result = app.handle_incoming(SESSION, CMD_MOVE, {
            "i": 4, "b": "____X____", "n": 3, "t": RESPONDER, "x": "",
        }, CHALLENGER, RESPONDER)
        assert result["error"] is not None

    def test_invalid_index_rejected(self, app):
        start_game(app)
        result = app.handle_incoming(SESSION, CMD_MOVE, {
            "i": 9, "b": "____X____", "n": 1, "t": RESPONDER, "x": "",
        }, CHALLENGER, RESPONDER)
        assert result["error"] is not None

    def test_false_win_claim_rejected(self, app):
        start_game(app)
        result = app.handle_incoming(SESSION, CMD_MOVE, {
            "i": 4, "b": "____X____", "n": 1, "t": "", "x": "win",
            "w": CHALLENGER,
        }, CHALLENGER, RESPONDER)
        assert result["error"] is not None

    def test_missed_win_claim_rejected(self, app):
        """Board shows a win but terminal is empty."""
        start_game(app)
        # Play through to a near-win position manually
        moves = [
            (CHALLENGER, 0, "X________", 1),
            (RESPONDER, 3, "X__O_____", 2),
            (CHALLENGER, 1, "XX_O_____", 3),
            (RESPONDER, 4, "XX_OO____", 4),
        ]
        for sender, idx, board, n in moves:
            app.handle_incoming(SESSION, CMD_MOVE, {
                "i": idx, "b": board, "n": n,
                "t": RESPONDER if sender == CHALLENGER else CHALLENGER,
                "x": "",
            }, sender, RESPONDER)

        # Challenger plays winning move (top row) but doesn't claim win
        result = app.handle_incoming(SESSION, CMD_MOVE, {
            "i": 2, "b": "XXXOO____", "n": 5,
            "t": RESPONDER, "x": "",
        }, CHALLENGER, RESPONDER)
        assert result["error"] is not None


class TestFullGame:
    def test_x_wins(self, app):
        """X wins with top row: positions 0, 1, 2."""
        start_game(app)
        moves = [
            (CHALLENGER, 0, "X________", 1, RESPONDER, ""),
            (RESPONDER, 3, "X__O_____", 2, CHALLENGER, ""),
            (CHALLENGER, 1, "XX_O_____", 3, RESPONDER, ""),
            (RESPONDER, 4, "XX_OO____", 4, CHALLENGER, ""),
            (CHALLENGER, 2, "XXXOO____", 5, "", "win"),
        ]
        for sender, idx, board, n, turn, terminal in moves:
            payload = {"i": idx, "b": board, "n": n, "t": turn, "x": terminal}
            if terminal == "win":
                payload["w"] = CHALLENGER
            result = app.handle_incoming(SESSION, CMD_MOVE, payload,
                                          sender, RESPONDER)
            assert result["error"] is None, \
                "Move {} failed: {}".format(n, result["error"])

        session = app._get_session(SESSION, RESPONDER)
        assert session.status == STATUS_COMPLETED
        assert session.metadata["terminal"] == "win"
        assert session.metadata["winner"] == CHALLENGER

    def test_draw_game(self, app):
        """Full board draw: XOXXOOOXX."""
        start_game(app)
        moves = [
            (CHALLENGER, 0, "X________", 1, RESPONDER, ""),
            (RESPONDER, 1, "XO_______", 2, CHALLENGER, ""),
            (CHALLENGER, 2, "XOX______", 3, RESPONDER, ""),
            (RESPONDER, 4, "XOX_O____", 4, CHALLENGER, ""),
            (CHALLENGER, 3, "XOXXO____", 5, RESPONDER, ""),
            (RESPONDER, 5, "XOXXOO___", 6, CHALLENGER, ""),
            (CHALLENGER, 8, "XOXXOO__X", 7, RESPONDER, ""),
            (RESPONDER, 6, "XOXXOOOX_", 8, CHALLENGER, ""),  # O at 6 is wrong, let me redo
        ]
        # Let me plan a proper draw: XOXXOOOXX
        # Positions: X=0,2,3,7,8  O=1,4,5,6
        app2 = TicTacToeApp()
        start_game(app2, "draw_session")
        draw_moves = [
            (CHALLENGER, 0, "X________", 1, RESPONDER, ""),
            (RESPONDER, 1, "XO_______", 2, CHALLENGER, ""),
            (CHALLENGER, 2, "XOX______", 3, RESPONDER, ""),
            (RESPONDER, 4, "XOX_O____", 4, CHALLENGER, ""),
            (CHALLENGER, 3, "XOXXO____", 5, RESPONDER, ""),
            (RESPONDER, 5, "XOXXOO___", 6, CHALLENGER, ""),
            (CHALLENGER, 8, "XOXXOO__X", 7, RESPONDER, ""),
            (RESPONDER, 6, "XOXXOOO_X", 8, CHALLENGER, ""),
            (CHALLENGER, 7, "XOXXOOOXX", 9, "", "draw"),
        ]
        for sender, idx, board, n, turn, terminal in draw_moves:
            payload = {"i": idx, "b": board, "n": n, "t": turn, "x": terminal}
            result = app2.handle_incoming("draw_session", CMD_MOVE, payload,
                                           sender, RESPONDER)
            assert result["error"] is None, \
                "Move {} failed: {}".format(n, result["error"])

        session = app2._get_session("draw_session", RESPONDER)
        assert session.status == STATUS_COMPLETED
        assert session.metadata["terminal"] == "draw"

    def test_resign(self, app):
        start_game(app)
        result = app.handle_incoming(SESSION, CMD_RESIGN, {},
                                      CHALLENGER, RESPONDER)
        assert result["error"] is None
        session = app._get_session(SESSION, RESPONDER)
        assert session.status == STATUS_COMPLETED
        assert session.metadata["terminal"] == "resign"


class TestDrawNegotiation:
    def test_draw_offer_accept(self, app):
        start_game(app)
        app.handle_incoming(SESSION, CMD_DRAW_OFFER, {}, CHALLENGER, RESPONDER)
        session = app._get_session(SESSION, RESPONDER)
        assert session.metadata["draw_offered"] is True

        result = app.handle_incoming(SESSION, CMD_DRAW_ACCEPT, {},
                                      RESPONDER, RESPONDER)
        assert result["error"] is None
        session = app._get_session(SESSION, RESPONDER)
        assert session.status == STATUS_COMPLETED
        assert session.metadata["terminal"] == "draw"

    def test_draw_offer_decline(self, app):
        start_game(app)
        app.handle_incoming(SESSION, CMD_DRAW_OFFER, {}, CHALLENGER, RESPONDER)
        result = app.handle_incoming(SESSION, CMD_DRAW_DECLINE, {},
                                      RESPONDER, RESPONDER)
        session = app._get_session(SESSION, RESPONDER)
        assert session.status == STATUS_ACTIVE
        assert session.metadata["draw_offered"] is False


class TestFallback:
    def test_challenge_fallback(self, app):
        assert app.render_fallback(CMD_CHALLENGE, {}) == "[LRGP TTT] Sent a challenge!"

    def test_move_fallback(self, app):
        assert "Move 3" in app.render_fallback(CMD_MOVE, {"n": 3, "x": ""})

    def test_win_fallback(self, app):
        text = app.render_fallback(CMD_MOVE, {"n": 5, "x": "win"})
        assert "wins" in text

    def test_draw_fallback(self, app):
        assert "drawn" in app.render_fallback(CMD_MOVE, {"n": 9, "x": "draw"})

    def test_resign_fallback(self, app):
        assert "Resigned" in app.render_fallback(CMD_RESIGN, {})


class TestOutgoing:
    def test_challenge_out(self, app):
        payload, fallback = app.handle_outgoing("new_sess", CMD_CHALLENGE,
                                                 {}, "my_id")
        assert "[LRGP TTT]" in fallback
        # Session should be created
        session = app._get_session("new_sess", "my_id")
        assert session is not None
        assert session.metadata["my_marker"] == "X"

    def test_accept_out(self, app):
        # First create incoming challenge
        app.handle_incoming("s1", CMD_CHALLENGE, {}, CHALLENGER, "my_id")
        payload, fallback = app.handle_outgoing("s1", CMD_ACCEPT, {}, "my_id")
        assert payload["b"] == EMPTY_BOARD
        assert payload["t"] == CHALLENGER  # challenger goes first

    def test_move_out(self, app):
        app.handle_incoming("s1", CMD_CHALLENGE, {}, CHALLENGER, "my_id")
        app.handle_outgoing("s1", CMD_ACCEPT, {}, "my_id")
        # Now it's challenger's turn, but let's test outgoing from challenger
        # We need to set up from challenger's perspective
        app2 = TicTacToeApp()
        app2.handle_outgoing("s2", CMD_CHALLENGE, {}, "challenger_id")
        # Simulate accept arriving
        app2.handle_incoming("s2", CMD_ACCEPT,
                              {"b": EMPTY_BOARD, "t": "challenger_id"},
                              "responder", "challenger_id")
        # Now make a move
        payload, fallback = app2.handle_outgoing("s2", CMD_MOVE,
                                                  {"i": 4}, "challenger_id")
        assert payload["b"] == "____X____"
        assert payload["n"] == 1
        assert payload["i"] == 4
