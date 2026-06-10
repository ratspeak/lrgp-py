"""Tests for Chess app — vector decode, basic flow, terminal detection.

Skipped entirely if python-chess isn't installed (i.e. running tests
without the [chess] extra).
"""

import os
import pytest

chess = pytest.importorskip("chess")

from lrgp.envelope import pack_envelope, unpack_envelope, pack_lxmf_fields
from lrgp.constants import (
    STATUS_PENDING, STATUS_ACTIVE, STATUS_COMPLETED,
    CMD_CHALLENGE, CMD_ACCEPT, CMD_MOVE, CMD_RESIGN,
    CMD_DRAW_OFFER, CMD_DRAW_ACCEPT,
    KEY_APP, KEY_COMMAND, KEY_SESSION, KEY_PAYLOAD,
)
from lrgp.apps.chess import (
    ChessApp, force_coin, STARTING_FEN,
    R_CHECKMATE, R_STALEMATE, R_INSUFFICIENT, R_RESIGN, R_AGREEMENT,
    R_THREEFOLD, R_FIFTY_MOVE,
    KEY_MOVE, KEY_PLY, KEY_TERMINAL, KEY_REASON, KEY_WINNER, KEY_WHITE,
    _replay_board, _detect_auto_terminal,
)


VECTORS_DIR = os.path.join(os.path.dirname(__file__), "vectors")
SESSION = "f1e2d3c4b5a69788"  # matches the bytes in the .bin vectors
PLAYER_A = "aaaa1111bbbb2222"
PLAYER_B = "cccc3333dddd4444"


@pytest.fixture
def app():
    force_coin(True)  # Challenger plays White
    yield ChessApp()
    force_coin(None)


def _load(name):
    with open(os.path.join(VECTORS_DIR, name), "rb") as f:
        return f.read()


class TestBinaryVectors:
    """The chess_*.bin files are byte-identical between lrgp-rs and lrgp-py.

    We test decode-correctness only (msgpack maps are unordered by spec, so
    re-encoding may produce a different byte order; the bytes that hit the
    wire from one impl must always decode-correctly in the other).
    """

    def test_challenge_decodes(self):
        # Vector files are raw msgpack envelopes; wrap into LXMF fields.
        from lrgp._msgpack import unpackb
        raw = unpackb(_load("chess_challenge.bin"))
        fields = {0xFB: "lrgp.v1", 0xFD: raw}
        env = unpack_envelope(fields)
        assert env[KEY_APP] == "chess.1"
        assert env[KEY_COMMAND] == "challenge"
        assert env[KEY_SESSION] == SESSION

    def test_accept_carries_white(self):
        from lrgp._msgpack import unpackb
        raw = unpackb(_load("chess_accept.bin"))
        fields = {0xFB: "lrgp.v1", 0xFD: raw}
        env = unpack_envelope(fields)
        assert env[KEY_COMMAND] == "accept"
        assert env[KEY_PAYLOAD][KEY_WHITE] == PLAYER_A

    def test_move_e4(self):
        from lrgp._msgpack import unpackb
        raw = unpackb(_load("chess_move.bin"))
        fields = {0xFB: "lrgp.v1", 0xFD: raw}
        env = unpack_envelope(fields)
        assert env[KEY_COMMAND] == "move"
        assert env[KEY_PAYLOAD][KEY_MOVE] == "e2e4"
        assert env[KEY_PAYLOAD][KEY_PLY] == 0

    def test_move_promotion(self):
        from lrgp._msgpack import unpackb
        raw = unpackb(_load("chess_move_promotion.bin"))
        fields = {0xFB: "lrgp.v1", 0xFD: raw}
        env = unpack_envelope(fields)
        assert env[KEY_PAYLOAD][KEY_MOVE] == "e7e8q"

    def test_move_checkmate_carries_winner_and_reason(self):
        from lrgp._msgpack import unpackb
        raw = unpackb(_load("chess_move_checkmate.bin"))
        fields = {0xFB: "lrgp.v1", 0xFD: raw}
        env = unpack_envelope(fields)
        p = env[KEY_PAYLOAD]
        assert p[KEY_MOVE] == "h5f7"
        assert p[KEY_TERMINAL] == "win"
        assert p[KEY_REASON] == R_CHECKMATE
        assert p[KEY_WINNER] == PLAYER_A

    def test_resign_decodes(self):
        from lrgp._msgpack import unpackb
        raw = unpackb(_load("chess_resign.bin"))
        fields = {0xFB: "lrgp.v1", 0xFD: raw}
        env = unpack_envelope(fields)
        assert env[KEY_COMMAND] == "resign"

    def test_draw_offer_decodes(self):
        from lrgp._msgpack import unpackb
        raw = unpackb(_load("chess_draw_offer.bin"))
        fields = {0xFB: "lrgp.v1", 0xFD: raw}
        env = unpack_envelope(fields)
        assert env[KEY_COMMAND] == "draw_offer"


class TestEngineHelpers:
    def test_replay_empty_history_is_starting_position(self):
        board = _replay_board([])
        assert board.fen() == STARTING_FEN

    def test_replay_scholars_mate_reaches_checkmate(self):
        board = _replay_board(["e2e4", "e7e5", "f1c4", "b8c6", "d1h5", "g8f6", "h5f7"])
        assert board.is_checkmate()

    def test_replay_invalid_move_raises(self):
        with pytest.raises(ValueError):
            _replay_board(["e2e9"])

    def test_detect_terminal_checkmate(self):
        board = _replay_board(["e2e4", "e7e5", "f1c4", "b8c6", "d1h5", "g8f6", "h5f7"])
        terminal, reason = _detect_auto_terminal(board)
        assert (terminal, reason) == ("win", R_CHECKMATE)

    def test_detect_terminal_stalemate(self):
        # K vs K + Q stalemate position: white K on h8, black to move with no legal moves.
        # Easier: load a known stalemate FEN and assert.
        b = chess.Board("k7/8/1Q6/8/8/8/8/7K b - - 0 1")
        terminal, reason = _detect_auto_terminal(b)
        # Black has no legal moves but is not in check -> stalemate.
        assert (terminal, reason) == ("draw", R_STALEMATE)

    def test_detect_terminal_insufficient(self):
        # Two kings only.
        b = chess.Board("8/8/8/4k3/8/4K3/8/8 w - - 0 1")
        terminal, reason = _detect_auto_terminal(b)
        assert (terminal, reason) == ("draw", R_INSUFFICIENT)


class TestSessionLifecycle:
    def test_challenge_creates_pending_session(self, app):
        app.handle_incoming(SESSION, CMD_CHALLENGE, {}, PLAYER_A, PLAYER_B)
        s = app._get_session(SESSION, PLAYER_B)
        assert s is not None
        assert s.status == STATUS_PENDING

    def test_accept_assigns_white_and_activates(self, app):
        app.handle_incoming(SESSION, CMD_CHALLENGE, {}, PLAYER_A, PLAYER_B)
        # Responder accepts; ChessApp picks White via pinned coin
        app.handle_incoming(SESSION, CMD_ACCEPT, {KEY_WHITE: PLAYER_A}, PLAYER_B, PLAYER_B)
        s = app._get_session(SESSION, PLAYER_B)
        assert s.status == STATUS_ACTIVE
        assert s.metadata["white"] == PLAYER_A
        assert s.metadata["black"] == PLAYER_B
        assert s.metadata["turn"] == PLAYER_A

    def test_move_advances_ply_and_swaps_turn(self, app):
        app.handle_incoming(SESSION, CMD_CHALLENGE, {}, PLAYER_A, PLAYER_B)
        app.handle_incoming(SESSION, CMD_ACCEPT, {KEY_WHITE: PLAYER_A}, PLAYER_B, PLAYER_B)
        app.handle_incoming(
            SESSION, CMD_MOVE,
            {KEY_MOVE: "e2e4", KEY_PLY: 0, KEY_TERMINAL: "", KEY_REASON: "", KEY_WINNER: ""},
            PLAYER_A, PLAYER_B,
        )
        s = app._get_session(SESSION, PLAYER_B)
        assert s.metadata["moves"] == ["e2e4"]
        assert s.metadata["turn"] == PLAYER_B

    def test_illegal_move_returns_error(self, app):
        app.handle_incoming(SESSION, CMD_CHALLENGE, {}, PLAYER_A, PLAYER_B)
        app.handle_incoming(SESSION, CMD_ACCEPT, {KEY_WHITE: PLAYER_A}, PLAYER_B, PLAYER_B)
        result = app.handle_incoming(
            SESSION, CMD_MOVE,
            {KEY_MOVE: "e2e9", KEY_PLY: 0, KEY_TERMINAL: "", KEY_REASON: "", KEY_WINNER: ""},
            PLAYER_A, PLAYER_B,
        )
        assert result["error"] is not None

    def test_resign_completes_session(self, app):
        app.handle_incoming(SESSION, CMD_CHALLENGE, {}, PLAYER_A, PLAYER_B)
        app.handle_incoming(SESSION, CMD_ACCEPT, {KEY_WHITE: PLAYER_A}, PLAYER_B, PLAYER_B)
        result = app.handle_incoming(SESSION, CMD_RESIGN, {}, PLAYER_A, PLAYER_B)
        s = app._get_session(SESSION, PLAYER_B)
        assert s.status == STATUS_COMPLETED
        assert s.metadata["terminal"] == "win"
        assert s.metadata["reason"] == R_RESIGN
        assert s.metadata["winner"] == PLAYER_B  # opponent of resigner


class TestTerminalClaimValidation:
    """T1-6: claimed x/r/w must be recomputed from the replayed board and
    rejected on mismatch (mirrors lrgp-rs validate_move)."""

    def _setup_active(self, app, identity=PLAYER_B):
        app.handle_incoming(SESSION, CMD_CHALLENGE, {}, PLAYER_A, identity)
        app.handle_incoming(SESSION, CMD_ACCEPT, {KEY_WHITE: PLAYER_A}, PLAYER_B, identity)

    def _move(self, uci, ply, terminal="", reason="", winner=""):
        return {
            KEY_MOVE: uci, KEY_PLY: ply,
            KEY_TERMINAL: terminal, KEY_REASON: reason, KEY_WINNER: winner,
        }

    def test_forged_win_on_legal_move_rejected(self, app):
        self._setup_active(app)
        result = app.handle_incoming(
            SESSION, CMD_MOVE,
            self._move("e2e4", 0, terminal="win", reason=R_CHECKMATE, winner=PLAYER_A),
            PLAYER_A, PLAYER_B,
        )
        assert result["error"] is not None
        s = app._get_session(SESSION, PLAYER_B)
        assert s.metadata.get("terminal", "") == ""
        assert s.metadata.get("winner", "") == ""
        assert s.metadata.get("moves", []) == []
        assert s.status == STATUS_ACTIVE

    def test_forged_winner_on_real_checkmate_rejected(self, app):
        # Fool's mate, but the mating move claims the LOSER as winner.
        self._setup_active(app)
        for ply, (uci, sender) in enumerate(
            [("f2f3", PLAYER_A), ("e7e5", PLAYER_B), ("g2g4", PLAYER_A)]
        ):
            result = app.handle_incoming(
                SESSION, CMD_MOVE, self._move(uci, ply), sender, PLAYER_B)
            assert result["error"] is None
        result = app.handle_incoming(
            SESSION, CMD_MOVE,
            self._move("d8h4", 3, terminal="win", reason=R_CHECKMATE, winner=PLAYER_A),
            PLAYER_B, PLAYER_B,
        )
        assert result["error"] is not None

    def test_genuine_checkmate_claims_accepted(self, app):
        self._setup_active(app)
        for ply, (uci, sender) in enumerate(
            [("f2f3", PLAYER_A), ("e7e5", PLAYER_B), ("g2g4", PLAYER_A)]
        ):
            result = app.handle_incoming(
                SESSION, CMD_MOVE, self._move(uci, ply), sender, PLAYER_B)
            assert result["error"] is None
        result = app.handle_incoming(
            SESSION, CMD_MOVE,
            self._move("d8h4", 3, terminal="win", reason=R_CHECKMATE, winner=PLAYER_B),
            PLAYER_B, PLAYER_B,
        )
        assert result["error"] is None
        s = app._get_session(SESSION, PLAYER_B)
        assert s.status == STATUS_COMPLETED
        assert s.metadata["terminal"] == "win"
        assert s.metadata["winner"] == PLAYER_B

    def test_validate_action_rejects_forged_terminal(self, app):
        # validate_action resolves the session under the default identity.
        self._setup_active(app, identity="")
        ok, err = app.validate_action(
            SESSION, CMD_MOVE,
            self._move("e2e4", 0, terminal="win", reason=R_CHECKMATE, winner=PLAYER_A),
            PLAYER_A,
        )
        assert not ok
        assert "Terminal mismatch" in err

    def test_validate_action_rejects_wrong_ply(self, app):
        # T1-5: `n` is 0-based; the next move's ply equals len(moves).
        self._setup_active(app, identity="")
        ok, err = app.validate_action(
            SESSION, CMD_MOVE, self._move("e2e4", 1), PLAYER_A)
        assert not ok
        assert "Ply mismatch" in err
        ok, err = app.validate_action(
            SESSION, CMD_MOVE, self._move("e2e4", 0), PLAYER_A)
        assert ok, err

    def test_first_move_out_emits_zero_based_ply(self, app):
        # T1-5 vector consistency: first move on the wire carries n=0,
        # matching the shared chess_move.bin fixture.
        self._setup_active(app, identity=PLAYER_A)
        wire, _ = app.handle_outgoing(SESSION, CMD_MOVE, {KEY_MOVE: "e2e4"}, PLAYER_A)
        assert wire[KEY_PLY] == 0


class TestRenderFallback:
    def test_challenge_fallback(self, app):
        assert "Sent a challenge" in app.render_fallback(CMD_CHALLENGE, {})

    def test_move_fallback_normal(self, app):
        assert app.render_fallback(CMD_MOVE, {KEY_MOVE: "e2e4"}) == "[LRGP Chess] e2e4"

    def test_move_fallback_checkmate(self, app):
        out = app.render_fallback(CMD_MOVE, {KEY_MOVE: "h5f7", KEY_TERMINAL: "win"})
        assert out == "[LRGP Chess] h5f7#"


class TestManifest:
    def test_manifest_advertises_chess(self, app):
        m = app.get_manifest()
        assert m["app_id"] == "chess"
        assert m["version"] == 1
        assert m["session_type"] == "turn_based"
        assert m["validation"] == "both"
        assert CMD_CHALLENGE in m["actions"]
        assert CMD_DRAW_OFFER in m["actions"]
