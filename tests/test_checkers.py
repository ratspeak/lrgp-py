"""Protocol, rule, persistence, and interop tests for Checkers."""

import copy
import hashlib
import os
import sys

import pytest

from lrgp._msgpack import packb, unpackb
from lrgp.apps import checkers as checkers_module
from lrgp.apps.checkers import (
    BLACK,
    BLACK_KING,
    BLACK_MAN,
    EMPTY,
    FORTY_MOVE_PLIES,
    MAX_PATH_SQUARES,
    STARTING_BOARD,
    WHITE,
    WHITE_KING,
    WHITE_MAN,
    CheckersApp,
    _Game,
    _notation,
    _render,
    force_coin,
    parse_path,
    parse_square,
    square_name,
)
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
from lrgp.session import Session

VECTORS_DIR = os.path.join(os.path.dirname(__file__), "vectors")
sys.path.insert(0, VECTORS_DIR)
from generate_checkers import (  # noqa: E402
    CHALLENGER,
    REFERENCE_GAME,
    SESSION,
    VECTORS,
)

RESPONDER = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def sq(name):
    square = parse_square(name)
    assert square is not None, name
    return square


def position(side, pieces):
    board = [EMPTY] * 64
    for name, piece in pieces:
        board[sq(name)] = piece
    return _Game(board, side)


def notations(game):
    return sorted(_notation(path) for path, _captured in game.legal_moves())


def policy_choice(legal, ply):
    """Deterministic self-play policy shared with lrgp-rs's unit tests."""
    longest = max(len(move) for move in legal)
    candidates = [move for move in legal if len(move) == longest]
    return candidates[(ply * 7 + 5) % len(candidates)]


@pytest.fixture
def challenger_black():
    force_coin(True)
    yield
    force_coin(None)


@pytest.fixture
def app(challenger_black):
    return CheckersApp()


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


def play(app, path, mover, receiver, session_id=SESSION):
    app.validate_outgoing(session_id, CMD_MOVE, {"m": path}, mover)
    wire, fallback = app.handle_outgoing(session_id, CMD_MOVE, {"m": path}, mover)
    assert wire, fallback
    result = app.handle_incoming(session_id, CMD_MOVE, wire, mover, receiver)
    assert result["error"] is None, result["error"]
    return wire, fallback


def other(identity):
    return RESPONDER if identity == CHALLENGER else CHALLENGER


def meta(app, identity, session_id=SESSION):
    return app._get_session(session_id, identity).metadata


def assert_shared_state(app, session_id=SESSION):
    left = dict(meta(app, CHALLENGER, session_id))
    right = dict(meta(app, RESPONDER, session_id))
    left.pop("my_color")
    right.pop("my_color")
    assert left == right


class TestRules:
    def test_starting_position_and_opening_moves(self):
        game = _Game()
        assert _render(game.board) == STARTING_BOARD
        assert game.side == BLACK
        assert game.board.count(BLACK_MAN) == 12
        assert game.board.count(WHITE_MAN) == 12
        assert notations(game) == [
            "b6 a5", "b6 c5", "d6 c5", "d6 e5", "f6 e5", "f6 g5", "h6 g5",
        ]

    def test_square_and_path_notation_is_strict(self):
        assert parse_square("a1") == 0
        assert parse_square("h8") == 63
        assert square_name(sq("e7")) == "e7"
        assert parse_path("a3 b4") == [sq("a3"), sq("b4")]
        assert parse_path("e7 c5 a3 c1") is not None
        for bad in [
            "", "a3", "a3 b4 ", " a3 b4", "a3  b4", "a3-b4", "A3 b4", "a3 b5",
            "i1 h2", "a0 b1", "a9 b8", "a3\tb4", None, 3, b"a3 b4",
        ]:
            assert parse_path(bad) is None, bad
        assert parse_path(" ".join(["a1"] * (MAX_PATH_SQUARES + 1))) is None

    def test_simple_move_example(self):
        game = position(WHITE, [("a3", WHITE_MAN), ("h8", BLACK_MAN)])
        assert notations(game) == ["a3 b4"]

    def test_double_jump_example_must_be_completed_and_crowns(self):
        game = position(
            BLACK, [("a5", BLACK_MAN), ("b4", WHITE_MAN), ("d2", WHITE_MAN)]
        )
        assert notations(game) == ["a5 c3 e1"]
        with pytest.raises(ValueError):
            game.play("a5 c3")
        game.play("a5 c3 e1")
        assert game.board[sq("e1")] == BLACK_KING
        assert game.board[sq("b4")] == EMPTY
        assert game.board[sq("d2")] == EMPTY
        assert game.terminal() == ("win", "np")

    def test_triple_jump_example(self):
        game = position(
            BLACK,
            [
                ("e7", BLACK_MAN),
                ("d6", WHITE_MAN),
                ("b4", WHITE_MAN),
                ("b2", WHITE_MAN),
                ("h2", WHITE_MAN),
            ],
        )
        assert notations(game) == ["e7 c5 a3 c1"]

    def test_captures_are_mandatory(self):
        game = position(
            WHITE, [("c3", WHITE_MAN), ("g3", WHITE_MAN), ("d4", BLACK_MAN)]
        )
        assert notations(game) == ["c3 e5"]

    def test_crowning_ends_the_jump(self):
        game = position(
            WHITE, [("b6", WHITE_MAN), ("c7", BLACK_MAN), ("e7", BLACK_MAN)]
        )
        assert notations(game) == ["b6 d8"]
        game.play("b6 d8")
        assert game.board[sq("d8")] == WHITE_KING
        assert game.board[sq("e7")] == BLACK_MAN

    def test_kings_move_and_capture_backward_men_do_not(self):
        king = position(WHITE, [("d4", WHITE_KING), ("h8", BLACK_MAN)])
        assert notations(king) == ["d4 c3", "d4 c5", "d4 e3", "d4 e5"]
        man = position(WHITE, [("d4", WHITE_MAN), ("c3", BLACK_MAN)])
        assert notations(man) == ["d4 c5", "d4 e5"]
        king_capture = position(WHITE, [("d4", WHITE_KING), ("c3", BLACK_MAN)])
        assert notations(king_capture) == ["d4 b2"]

    def test_king_circuit_returns_to_origin_without_rejumping(self):
        game = position(
            WHITE,
            [
                ("d4", WHITE_KING),
                ("e5", BLACK_MAN),
                ("g5", BLACK_MAN),
                ("g3", BLACK_MAN),
                ("e3", BLACK_MAN),
            ],
        )
        assert "d4 f6 h4 f2 d4" in notations(game)
        for _path, captured in game.legal_moves():
            assert len(set(captured)) == len(captured)

    def test_blocked_side_loses(self):
        game = position(
            BLACK, [("a3", BLACK_MAN), ("b2", WHITE_MAN), ("c1", WHITE_MAN)]
        )
        assert game.legal_moves() == []
        assert game.terminal() == ("win", "nm")

    def test_threefold_and_forty_move_claims(self):
        game = position(WHITE, [("d4", WHITE_KING), ("h8", BLACK_KING)])
        assert game.claim_reason() is None
        for _ in range(2):
            for move in ["d4 e5", "h8 g7", "e5 d4", "g7 h8"]:
                game.play(move)
        assert game.claim_is_valid("3fr")
        assert not game.claim_is_valid("40m")
        assert game.claim_reason() == "3fr"

        game.quiet_plies = FORTY_MOVE_PLIES - 1
        game.play("d4 e5")
        assert game.claim_is_valid("40m")
        assert game.claim_reason() == "40m"

    def test_man_moves_reset_the_forty_move_counter(self):
        game = position(
            WHITE, [("d4", WHITE_KING), ("a3", WHITE_MAN), ("h8", BLACK_KING)]
        )
        game.play("d4 e5")
        game.play("h8 g7")
        assert game.quiet_plies == 2
        game.play("a3 b4")
        assert game.quiet_plies == 0


class TestReferenceGame:
    def test_policy_reproduces_the_shared_reference_game(self):
        game = _Game()
        played = []
        for ply in range(400):
            if game.terminal()[0]:
                break
            legal = [_notation(path) for path, _captured in game.legal_moves()]
            move = policy_choice(legal, ply)
            game.play(move)
            played.append(move)
        assert played == REFERENCE_GAME
        assert game.terminal() == ("win", "np")

    def test_reference_game_converges_through_both_peers(self, app):
        activate(app)
        last = None
        for ply, move in enumerate(REFERENCE_GAME):
            mover = meta(app, CHALLENGER)["turn"]
            assert mover == (CHALLENGER if ply % 2 == 0 else RESPONDER)
            assert move in meta(app, mover)["legal_moves"]
            last, _fallback = play(app, move, mover, other(mover))
            assert last["n"] == ply
            assert_shared_state(app)

        assert last == VECTORS["checkers_move_win.bin"][1]
        for identity in (CHALLENGER, RESPONDER):
            session = app._get_session(SESSION, identity)
            assert session.status == STATUS_COMPLETED
            assert session.metadata["terminal"] == "win"
            assert session.metadata["terminal_reason"] == "np"
            assert session.metadata["winner"] == CHALLENGER
            assert session.metadata["legal_moves"] == []
            CheckersApp().upsert_session(Session.from_dict(session.to_dict()))


class TestLifecycle:
    def test_manifest_is_canonical(self):
        manifest = CheckersApp().get_manifest()
        assert manifest["app_id"] == "checkers"
        assert manifest["version"] == 1
        assert manifest["display_name"] == "Checkers"
        assert manifest["min_players"] == manifest["max_players"] == 2
        assert manifest["validation"] == "both"
        assert len(manifest["actions"]) == 9
        assert set(manifest["preferred_delivery"]) == set(manifest["actions"])
        assert manifest["preferred_delivery"][CMD_MOVE] == "opportunistic"
        assert manifest["preferred_delivery"][CMD_RESIGN] == "direct"
        assert manifest["ttl"] == {"pending": 86400, "active": 604800}

    @pytest.mark.parametrize("challenger_is_black", [True, False])
    def test_accept_assigns_colors_from_coin(self, challenger_is_black):
        force_coin(challenger_is_black)
        try:
            app = CheckersApp()
            accept = activate(app)
        finally:
            force_coin(None)
        black = CHALLENGER if challenger_is_black else RESPONDER
        assert accept == {"b": black}
        for identity in (CHALLENGER, RESPONDER):
            session = app._get_session(SESSION, identity)
            assert session.status == STATUS_ACTIVE
            assert session.metadata["first_turn"] == black
            assert session.metadata["turn"] == black
            assert session.metadata["my_color"] == (
                BLACK if identity == black else WHITE
            )
            assert len(session.metadata["legal_moves"]) == 7
        assert_shared_state(app)

    def test_accept_with_unbound_black_player_is_rejected(self, app):
        app.handle_outgoing(SESSION, CMD_CHALLENGE, {}, CHALLENGER)
        app.bind_peer(SESSION, CHALLENGER, RESPONDER)
        result = app.handle_incoming(
            SESSION, CMD_ACCEPT, {"b": "cc"}, RESPONDER, CHALLENGER
        )
        assert result["error"]["code"] == ERR_PROTOCOL_ERROR
        assert app._get_session(SESSION, CHALLENGER).status == STATUS_PENDING

    def test_decline_and_cancel(self, app):
        app.handle_outgoing(SESSION, CMD_CHALLENGE, {}, CHALLENGER)
        app.handle_incoming(SESSION, CMD_CHALLENGE, {}, CHALLENGER, RESPONDER)
        _wire, fallback = app.handle_outgoing(SESSION, CMD_DECLINE, {}, RESPONDER)
        assert fallback == "[LRGP Checkers] Challenge declined"
        app.handle_incoming(SESSION, CMD_DECLINE, {}, RESPONDER, CHALLENGER)
        for identity in (CHALLENGER, RESPONDER):
            assert app._get_session(SESSION, identity).status == STATUS_DECLINED

        app.handle_outgoing("1111111111111111", CMD_CHALLENGE, {}, CHALLENGER)
        _wire, fallback = app.handle_outgoing(
            "1111111111111111", CMD_DECLINE, {}, CHALLENGER
        )
        assert fallback == "[LRGP Checkers] Challenge cancelled"

    def test_forced_capture_exchange_converges(self, app):
        activate(app)
        first, fallback = play(app, "d6 e5", CHALLENGER, RESPONDER)
        assert first == {"m": "d6 e5", "n": 0, "x": ""}
        assert fallback == "[LRGP Checkers] d6 e5"
        play(app, "c3 d4", RESPONDER, CHALLENGER)

        metadata = meta(app, CHALLENGER)
        assert metadata["must_capture"] is True
        assert metadata["legal_moves"] == ["e5 c3"]
        with pytest.raises(OutgoingActionError) as error:
            app.validate_outgoing(SESSION, CMD_MOVE, {"m": "b6 a5"}, CHALLENGER)
        assert error.value.code == ERR_INVALID_MOVE

        play(app, "e5 c3", CHALLENGER, RESPONDER)
        assert sorted(meta(app, RESPONDER)["legal_moves"]) == ["b2 d4", "d2 b4"]
        last, _fallback = play(app, "d2 b4", RESPONDER, CHALLENGER)
        assert last["n"] == 3
        assert_shared_state(app)
        metadata = meta(app, CHALLENGER)
        assert metadata["move_count"] == 4
        assert metadata["turn"] == CHALLENGER
        assert metadata["last_move"] == "d2 b4"

    def test_out_of_turn_and_illegal_intents_are_rejected(self, app):
        activate(app)
        with pytest.raises(OutgoingActionError) as error:
            app.validate_outgoing(SESSION, CMD_MOVE, {"m": "c3 d4"}, RESPONDER)
        assert error.value.code == ERR_NOT_YOUR_TURN
        for intent in ({"m": "d6 d5"}, {"m": "d6 c7"}):
            with pytest.raises(OutgoingActionError):
                app.validate_outgoing(SESSION, CMD_MOVE, intent, CHALLENGER)
        for intent in ({}, {"m": 3}, {"m": "d6 e5", "n": 0}):
            with pytest.raises(OutgoingActionError) as error:
                app.validate_outgoing(SESSION, CMD_MOVE, intent, CHALLENGER)
            assert error.value.code == ERR_PROTOCOL_ERROR
        wire, fallback = app.handle_outgoing(SESSION, CMD_MOVE, {"m": "c3 d4"}, RESPONDER)
        assert wire == {}
        assert fallback == "[LRGP Checkers] Not your turn"
        assert meta(app, CHALLENGER)["move_count"] == 0

    @pytest.mark.parametrize(
        "payload,sender",
        [
            ({"m": "d6 e5", "n": 1, "x": ""}, CHALLENGER),
            ({"m": "d6 d5", "n": 0, "x": ""}, CHALLENGER),
            ({"m": "d6 c7", "n": 0, "x": ""}, CHALLENGER),
            ({"m": "c3 d4", "n": 0, "x": ""}, RESPONDER),
            ({"m": "d6 e5", "n": 0, "x": "draw"}, CHALLENGER),
            ({"m": "d6 e5", "n": 0, "x": "", "extra": None}, CHALLENGER),
            ({"m": "d6 e5", "n": True, "x": ""}, CHALLENGER),
            ({"m": "d6 e5", "n": -1, "x": ""}, CHALLENGER),
            (
                {"m": "d6 e5", "n": 0, "x": "win", "r": "nm", "w": CHALLENGER},
                CHALLENGER,
            ),
            (
                {"m": "d6 e5", "n": 0, "x": "win", "r": "rsn", "w": CHALLENGER},
                CHALLENGER,
            ),
        ],
    )
    def test_forged_wire_moves_are_rejected_without_mutation(
        self, app, payload, sender
    ):
        activate(app)
        receiver = other(sender)
        before = copy.deepcopy(meta(app, receiver))
        result = app.handle_incoming(SESSION, CMD_MOVE, payload, sender, receiver)
        assert result["error"] is not None
        assert meta(app, receiver) == before

    def test_malformed_challenge_creates_no_session(self, app):
        result = app.handle_incoming(
            SESSION, CMD_CHALLENGE, {"extra": True}, CHALLENGER, RESPONDER
        )
        assert result["error"]["code"] == ERR_PROTOCOL_ERROR
        assert app._get_session(SESSION, RESPONDER) is None

    def test_empty_turn_fails_closed(self, app):
        activate(app)
        session = app._get_session(SESSION, RESPONDER)
        session.metadata["turn"] = ""
        valid, message, _game = app._validate_move_details(
            session, {"m": "d6 e5", "n": 0, "x": ""}, CHALLENGER
        )
        assert not valid
        assert "Turn is required" in message

    def test_resign_completes_both_sides(self, app):
        activate(app)
        _wire, fallback = app.handle_outgoing(SESSION, CMD_RESIGN, {}, CHALLENGER)
        assert fallback == "[LRGP Checkers] Resigned."
        app.handle_incoming(SESSION, CMD_RESIGN, {}, CHALLENGER, RESPONDER)
        for identity in (CHALLENGER, RESPONDER):
            metadata = meta(app, identity)
            assert app._get_session(SESSION, identity).status == STATUS_COMPLETED
            assert metadata["terminal"] == "win"
            assert metadata["terminal_reason"] == "rsn"
            assert metadata["winner"] == RESPONDER
            assert metadata["turn"] == ""
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
        assert (
            app.handle_incoming(SESSION, CMD_DRAW_OFFER, {}, CHALLENGER, RESPONDER)[
                "error"
            ]
            is not None
        )
        assert (
            app.handle_incoming(SESSION, CMD_DRAW_ACCEPT, {}, CHALLENGER, RESPONDER)[
                "error"
            ]
            is not None
        )

        app.validate_outgoing(SESSION, CMD_DRAW_ACCEPT, {}, RESPONDER)
        _wire, fallback = app.handle_outgoing(SESSION, CMD_DRAW_ACCEPT, {}, RESPONDER)
        assert fallback == "[LRGP Checkers] Draw accepted"
        app.handle_incoming(SESSION, CMD_DRAW_ACCEPT, {}, RESPONDER, CHALLENGER)
        for identity in (CHALLENGER, RESPONDER):
            metadata = meta(app, identity)
            assert app._get_session(SESSION, identity).status == STATUS_COMPLETED
            assert metadata["terminal"] == "draw"
            assert metadata["terminal_reason"] == "agr"
            assert metadata["draw_offered"] is False
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
        play(app, "d6 e5", CHALLENGER, RESPONDER)
        assert not meta(app, CHALLENGER)["draw_offered"]
        assert not meta(app, RESPONDER)["draw_offered"]
        assert_shared_state(app)

    def test_ineligible_claim_degrades_to_plain_offer(self, app):
        activate(app)
        app.validate_outgoing(SESSION, CMD_DRAW_OFFER, {"r": "3fr"}, CHALLENGER)
        wire, fallback = app.handle_outgoing(
            SESSION, CMD_DRAW_OFFER, {"r": "3fr"}, CHALLENGER
        )
        assert wire == {"r": "3fr"}
        assert fallback == "[LRGP Checkers] Claimed threefold repetition"
        app.handle_incoming(SESSION, CMD_DRAW_OFFER, wire, CHALLENGER, RESPONDER)
        for identity in (CHALLENGER, RESPONDER):
            assert app._get_session(SESSION, identity).status == STATUS_ACTIVE
            assert meta(app, identity)["draw_offered_by"] == CHALLENGER

        with pytest.raises(OutgoingActionError):
            app.validate_outgoing(SESSION, CMD_DRAW_OFFER, {"r": "50m"}, RESPONDER)
        assert (
            app.handle_incoming(SESSION, CMD_DRAW_OFFER, {"r": "50m"}, CHALLENGER, RESPONDER)[
                "error"
            ]
            is not None
        )

    def test_router_prepares_the_compact_move_wire(self):
        router = LrgpRouter()
        router.register(CheckersApp())
        router.dispatch_outgoing_to(
            "checkers", CMD_CHALLENGE, {}, SESSION, CHALLENGER, RESPONDER
        )
        accept = pack_envelope(
            "checkers", 1, CMD_ACCEPT, SESSION, {"b": CHALLENGER}, nonce=b"accept01"
        )
        applied = router.dispatch_incoming(accept, RESPONDER, CHALLENGER)
        assert applied["session"]["status"] == STATUS_ACTIVE

        prepared = router.dispatch_outgoing_to(
            "checkers", CMD_MOVE, {"m": "f6 g5"}, SESSION, CHALLENGER, RESPONDER
        )
        assert prepared.envelope["p"] == VECTORS["checkers_move.bin"][1]
        assert prepared.delivery_method == "opportunistic"

    def test_package_discovery_and_public_export(self):
        import lrgp.apps
        from lrgp.apps import CheckersApp as ExportedApp

        assert ExportedApp is CheckersApp
        router = LrgpRouter()
        router.discover(lrgp.apps)
        assert isinstance(router.get_app("checkers"), CheckersApp)

    def test_render_fallback(self):
        app = CheckersApp()
        assert app.render_fallback(CMD_CHALLENGE, {}) == "[LRGP Checkers] Sent a challenge!"
        assert (
            app.render_fallback(CMD_MOVE, {"m": "a5 c3 e1", "x": ""})
            == "[LRGP Checkers] a5 c3 e1"
        )
        assert (
            app.render_fallback(CMD_MOVE, {"m": "a5 c3 e1", "x": "win", "r": "np"})
            == "[LRGP Checkers] a5 c3 e1 wins (no pieces left)"
        )
        assert (
            app.render_fallback(CMD_DRAW_OFFER, {"r": "40m"})
            == "[LRGP Checkers] Claimed forty-move rule"
        )


class TestHydration:
    def _record(self, app):
        activate(app)
        play(app, "d6 e5", CHALLENGER, RESPONDER)
        play(app, "c3 d4", RESPONDER, CHALLENGER)
        return app._get_session(SESSION, RESPONDER).to_dict()

    def test_valid_record_hydrates_and_can_continue(self, app):
        record = self._record(app)
        fresh = CheckersApp()
        restored = fresh.upsert_session(copy.deepcopy(record))
        assert restored.metadata == record["metadata"]
        fresh.validate_action(
            SESSION, CMD_MOVE, {"m": "e5 c3", "n": 2, "x": ""}, CHALLENGER, RESPONDER
        )

    @pytest.mark.parametrize(
        "field,value",
        [
            ("board", STARTING_BOARD),
            ("turn", RESPONDER),
            ("move_count", 1),
            ("move_count", True),
            ("my_color", BLACK),
            ("first_turn", RESPONDER),
            ("terminal", "win"),
            ("last_move", "d6 e5"),
            ("moves", ["d6 e5", "c3 c4"]),
            ("moves", "d6 e5"),
        ],
    )
    def test_tampering_is_rejected(self, app, field, value):
        record = self._record(app)
        record["metadata"][field] = value
        with pytest.raises(InvalidEnvelope):
            CheckersApp().upsert_session(record)

    def test_unverified_claim_draw_is_rejected(self, app):
        record = self._record(app)
        record["status"] = STATUS_COMPLETED
        record["metadata"].update(
            {"turn": "", "terminal": "draw", "terminal_reason": "3fr"}
        )
        with pytest.raises(InvalidEnvelope):
            CheckersApp().upsert_session(record)

    def test_legacy_ownerless_draw_flag_is_normalized(self, app):
        record = self._record(app)
        record["metadata"]["draw_offered"] = True
        restored = CheckersApp().upsert_session(record)
        assert restored.metadata["draw_offered"] is False

    def test_wrong_app_or_session_id_is_rejected(self, app):
        record = self._record(app)
        for key, value in (("app_id", "chess"), ("session_id", "g1")):
            bad = copy.deepcopy(record)
            bad[key] = value
            with pytest.raises(InvalidEnvelope):
                CheckersApp().upsert_session(bad)

    def test_expiry_clears_draw_offer_and_round_trips(self, app):
        activate(app)
        app.handle_outgoing(SESSION, CMD_DRAW_OFFER, {}, CHALLENGER)
        record = app._get_session(SESSION, CHALLENGER).to_dict()
        assert record["metadata"]["draw_offered"] is True
        record["last_action_at"] = 0.0

        fresh = CheckersApp()
        expired = fresh.upsert_session(record)
        assert expired.status == STATUS_EXPIRED
        assert expired.metadata["draw_offered"] is False
        # The expired record, as persisted, hydrates again.
        CheckersApp().upsert_session(expired.to_dict())

        stale = expired.to_dict()
        stale["metadata"].update(
            {"draw_offered": True, "draw_offered_by": CHALLENGER}
        )
        assert CheckersApp().upsert_session(stale).metadata["draw_offered"] is False


class TestBinaryVectors:
    VECTOR_HASHES = {
        "checkers_challenge.bin": "88c9054a80899e07da974e18947849c1640d76b97198059cd2fac8ec9495489a",
        "checkers_accept.bin": "e1e4374c8d447fe0b25fda5f3f1b3661f9a1dc131b67219f513feeab7fd3653b",
        "checkers_move.bin": "a9343378454b60989c2a75ac62a06186be44c705aa2f9c5888d79cea2554f298",
        "checkers_move_jump.bin": "f916e2fc405779c92c6d35bb353f3a2e79c0444d9c030b79ebb9624d87561d03",
        "checkers_move_win.bin": "a35c3ef56db3a84139eec4bd00efd7c83acb3b18b7483082009a8f46452131a1",
        "checkers_draw_claim.bin": "170b41bcfa186d1c74609dc2a82822dbe12254b9bc6218996cf6579c076d7f0d",
    }

    @staticmethod
    def _raw(name):
        with open(os.path.join(VECTORS_DIR, name), "rb") as vector:
            return vector.read()

    def test_every_generated_vector_is_locked(self):
        assert set(self.VECTOR_HASHES) == set(VECTORS)

    @pytest.mark.parametrize("filename", sorted(VECTORS))
    def test_vector_bytes_have_locked_cross_language_digest(self, filename):
        assert (
            hashlib.sha256(self._raw(filename)).hexdigest()
            == self.VECTOR_HASHES[filename]
        )

    @pytest.mark.parametrize("filename", sorted(VECTORS))
    def test_generator_reproduces_exact_fixture_bytes(self, filename):
        command, payload, nonce = VECTORS[filename]
        envelope = pack_envelope("checkers", 1, command, SESSION, payload, nonce=nonce)
        assert packb(envelope) == self._raw(filename)

    @pytest.mark.parametrize("filename", sorted(VECTORS))
    def test_vector_decodes_canonically(self, filename):
        command, payload, nonce = VECTORS[filename]
        envelope = unpack_envelope(pack_lxmf_fields(unpackb(self._raw(filename))))
        assert envelope["a"] == "checkers.1"
        assert envelope["c"] == command
        assert envelope["s"] == SESSION
        assert envelope["p"] == payload
        assert envelope["n"] == nonce

    def test_vector_payloads_pass_strict_wire_validation(self):
        for command, payload, _nonce in VECTORS.values():
            assert CheckersApp._incoming_payload_error(command, payload) is None


class TestWireBudget:
    def test_worst_case_move_fits_envelope_and_opportunistic_budget(self):
        path = " ".join(
            ["a1", "c3", "e5", "g7", "e5", "c3", "a1", "c3", "e5", "g7", "e5", "c3", "a1"]
        )
        assert len(path.split(" ")) == MAX_PATH_SQUARES
        payload = {"m": path, "n": 65535, "x": "win", "r": "np", "w": CHALLENGER}
        envelope = pack_envelope("checkers", 1, CMD_MOVE, SESSION, payload)
        assert validate_envelope_size(envelope) <= 150 <= ENVELOPE_MAX_PACKED
        content_size = measure_content_size(
            "",
            CheckersApp().render_fallback(CMD_MOVE, payload),
            pack_lxmf_fields(envelope),
        )
        assert content_size <= OPPORTUNISTIC_MAX_CONTENT


def test_module_exports():
    for name in checkers_module.__all__:
        assert hasattr(checkers_module, name)
