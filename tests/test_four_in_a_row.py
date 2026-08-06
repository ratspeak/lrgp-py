"""Protocol, rule, persistence, and interop tests for Four in a Row."""

import copy
import hashlib
import os

import pytest

from lrgp._msgpack import packb, unpackb
from lrgp.apps.four_in_a_row import (
    CELL_COUNT,
    COLUMNS,
    EMPTY_BOARD,
    FIRST_MARKER,
    ROWS,
    SECOND_MARKER,
    FourInARowApp,
    _check_draw,
    _check_winner,
    _drop_cell,
    _initial_metadata,
    _is_canonical_board,
    _marker_for_move,
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
    ERR_INVALID_MOVE,
    ERR_PROTOCOL_ERROR,
    ENVELOPE_MAX_PACKED,
    OPPORTUNISTIC_MAX_CONTENT,
    STATUS_ACTIVE,
    STATUS_COMPLETED,
    STATUS_DECLINED,
    STATUS_EXPIRED,
    STATUS_PENDING,
    TTL_GRACE_PERIOD,
)
from lrgp.envelope import (
    measure_content_size,
    pack_envelope,
    pack_lxmf_fields,
    unpack_envelope,
    validate_envelope_size,
)
from lrgp.errors import EnvelopeTooLarge, InvalidEnvelope, OutgoingActionError
from lrgp.router import LrgpRouter
from lrgp.session import Session

SESSION = "0123456789abcdef"
CHALLENGER = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
RESPONDER = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
VECTORS_DIR = os.path.join(os.path.dirname(__file__), "vectors")

WIN_SEQUENCES = {
    "horizontal": [0, 6, 1, 6, 2, 5, 3],
    "vertical": [0, 1, 0, 1, 0, 1, 0],
    "diagonal_up_right": [0, 1, 1, 2, 4, 2, 2, 3, 4, 3, 5, 3, 3],
    "diagonal_up_left": [6, 5, 5, 4, 2, 4, 4, 3, 2, 3, 1, 3, 3],
}

# A complete legal game with no earlier terminal position.  The final move
# fills the board and must carry x="draw".
DRAW_SEQUENCE = [
    0,
    4,
    4,
    0,
    2,
    1,
    5,
    1,
    6,
    4,
    2,
    6,
    5,
    0,
    2,
    1,
    0,
    6,
    4,
    0,
    6,
    0,
    4,
    5,
    1,
    2,
    4,
    3,
    1,
    2,
    1,
    5,
    2,
    3,
    3,
    3,
    6,
    6,
    3,
    5,
    5,
    3,
]


@pytest.fixture
def app():
    return FourInARowApp()


def _active_responder(app, session_id=SESSION):
    challenge = app.handle_incoming(
        session_id, CMD_CHALLENGE, {}, CHALLENGER, RESPONDER
    )
    assert challenge["error"] is None
    wire, _fallback = app.handle_outgoing(session_id, CMD_ACCEPT, {}, RESPONDER)
    assert wire == {"t": CHALLENGER}
    return app._get_session(session_id, RESPONDER)


def _wire_for(session, column, sender):
    move_num = session.metadata["move_count"] + 1
    row, cell = _drop_cell(session.metadata["board"], column)
    board = list(session.metadata["board"])
    board[cell] = _marker_for_move(move_num)
    board = "".join(board)
    terminal = "win" if _check_winner(board) else "draw" if _check_draw(board) else ""
    payload = {"c": column, "n": move_num, "x": terminal}
    if terminal == "win":
        payload["w"] = sender
    return payload


def _play_incoming(app, columns, session_id=SESSION):
    results = []
    for move_num, column in enumerate(columns, 1):
        session = app._get_session(session_id, RESPONDER)
        sender = CHALLENGER if move_num % 2 == 1 else RESPONDER
        payload = _wire_for(session, column, sender)
        result = app.handle_incoming(session_id, CMD_MOVE, payload, sender, RESPONDER)
        assert result["error"] is None, result["error"]
        results.append((payload, result))
    return results


class TestBoardRules:
    def test_dimensions_and_markers_are_canonical(self):
        assert (COLUMNS, ROWS, CELL_COUNT) == (7, 6, 42)
        assert EMPTY_BOARD == "_" * 42
        assert (FIRST_MARKER, SECOND_MARKER) == ("A", "B")
        assert [_marker_for_move(n) for n in range(1, 5)] == ["A", "B", "A", "B"]

    def test_gravity_lands_bottom_then_stacks_upward(self):
        assert _drop_cell(EMPTY_BOARD, 3) == (5, 38)
        board = EMPTY_BOARD[:38] + "A" + EMPTY_BOARD[39:]
        assert _is_canonical_board(board)
        assert _drop_cell(board, 3) == (4, 31)

    @pytest.mark.parametrize("column", [-1, 7, True, "3"])
    def test_drop_rejects_noncanonical_columns(self, column):
        assert _drop_cell(EMPTY_BOARD, column) is None

    def test_gravity_rejects_floating_markers(self):
        floating = "A" + EMPTY_BOARD[1:]
        assert not _is_canonical_board(floating)
        assert not _is_canonical_board("_" * 41)
        assert not _is_canonical_board("_" * 41 + "X")

    @pytest.mark.parametrize("direction,columns", WIN_SEQUENCES.items())
    def test_every_win_direction_is_detected_through_real_moves(
        self, app, direction, columns
    ):
        _active_responder(app)
        results = _play_incoming(app, columns)
        final_wire, final_result = results[-1]
        assert final_wire["x"] == "win", direction
        assert final_wire["w"] == CHALLENGER
        metadata = final_result["session"]["metadata"]
        assert _check_winner(metadata["board"]) == FIRST_MARKER
        assert metadata["winner"] == CHALLENGER
        assert metadata["turn"] == ""
        assert final_result["session"]["status"] == STATUS_COMPLETED

    def test_full_board_without_winner_is_draw(self, app):
        _active_responder(app)
        results = _play_incoming(app, DRAW_SEQUENCE)
        final_wire, final_result = results[-1]
        assert final_wire == {"c": 3, "n": 42, "x": "draw"}
        assert final_result["session"]["status"] == STATUS_COMPLETED
        metadata = final_result["session"]["metadata"]
        assert metadata["terminal"] == "draw"
        assert metadata["winner"] == ""
        assert _check_draw(metadata["board"])


class TestLifecycleAndMetadata:
    def test_challenge_assigns_fixed_roles_and_complete_metadata(self, app):
        result = app.handle_incoming(SESSION, CMD_CHALLENGE, {}, CHALLENGER, RESPONDER)
        assert result["error"] is None
        assert result["session"]["status"] == STATUS_PENDING
        metadata = result["session"]["metadata"]
        assert set(metadata) == {
            "board",
            "turn",
            "first_turn",
            "my_marker",
            "first_marker",
            "second_marker",
            "move_count",
            "last_column",
            "last_row",
            "last_cell",
            "winner",
            "terminal",
            "draw_offered",
            "draw_offered_by",
        }
        assert metadata == _initial_metadata(SECOND_MARKER, CHALLENGER)
        assert result["emit"]["type"] == "challenge"

    def test_outgoing_challenger_is_a_and_moves_first(self, app):
        app.handle_outgoing(SESSION, CMD_CHALLENGE, {}, CHALLENGER)
        app.bind_peer(SESSION, CHALLENGER, RESPONDER)
        session = app._get_session(SESSION, CHALLENGER)
        assert session.metadata["my_marker"] == FIRST_MARKER
        assert session.metadata["first_turn"] == CHALLENGER
        assert session.metadata["turn"] == ""

        result = app.handle_incoming(
            SESSION, CMD_ACCEPT, {"t": CHALLENGER}, RESPONDER, CHALLENGER
        )
        assert result["error"] is None
        assert result["session"]["status"] == STATUS_ACTIVE
        assert result["session"]["metadata"]["turn"] == CHALLENGER

    def test_accept_transmits_only_first_turn(self, app):
        _active_responder(app)
        session = app._get_session(SESSION, RESPONDER)
        assert session.status == STATUS_ACTIVE
        assert session.metadata["turn"] == CHALLENGER

    def test_accept_rejects_wrong_first_turn_without_mutation(self, app):
        app.handle_outgoing(SESSION, CMD_CHALLENGE, {}, CHALLENGER)
        app.bind_peer(SESSION, CHALLENGER, RESPONDER)
        before = app._get_session(SESSION, CHALLENGER).to_dict()
        valid, message = app.validate_action(
            SESSION,
            CMD_ACCEPT,
            {"t": RESPONDER},
            RESPONDER,
            CHALLENGER,
        )
        assert valid is False
        assert message == "Accept first turn does not match challenge"
        result = app.handle_incoming(
            SESSION, CMD_ACCEPT, {"t": RESPONDER}, RESPONDER, CHALLENGER
        )
        assert result["error"]["code"] == ERR_PROTOCOL_ERROR
        assert app._get_session(SESSION, CHALLENGER).to_dict() == before

    def test_decline_completes_pending_lifecycle(self, app):
        app.handle_incoming(SESSION, CMD_CHALLENGE, {}, CHALLENGER, RESPONDER)
        result = app.handle_incoming(SESSION, CMD_DECLINE, {}, CHALLENGER, RESPONDER)
        assert result["error"] is None
        assert result["session"]["status"] == STATUS_DECLINED


class TestMoveReconstructionAndValidation:
    def test_two_peers_converge_without_board_or_turn_on_wire(self):
        challenger = FourInARowApp()
        responder = FourInARowApp()

        challenger.handle_outgoing(SESSION, CMD_CHALLENGE, {}, CHALLENGER)
        challenger.bind_peer(SESSION, CHALLENGER, RESPONDER)
        responder.handle_incoming(SESSION, CMD_CHALLENGE, {}, CHALLENGER, RESPONDER)
        accept, _ = responder.handle_outgoing(SESSION, CMD_ACCEPT, {}, RESPONDER)
        assert accept == {"t": CHALLENGER}
        challenger.handle_incoming(SESSION, CMD_ACCEPT, accept, RESPONDER, CHALLENGER)

        for move_num, column in enumerate(WIN_SEQUENCES["horizontal"], 1):
            if move_num % 2 == 1:
                wire, _ = challenger.handle_outgoing(
                    SESSION, CMD_MOVE, {"c": column}, CHALLENGER
                )
                result = responder.handle_incoming(
                    SESSION, CMD_MOVE, wire, CHALLENGER, RESPONDER
                )
            else:
                wire, _ = responder.handle_outgoing(
                    SESSION, CMD_MOVE, {"c": column}, RESPONDER
                )
                result = challenger.handle_incoming(
                    SESSION, CMD_MOVE, wire, RESPONDER, CHALLENGER
                )
            assert result["error"] is None
            assert set(wire) == (
                {"c", "n", "x", "w"} if wire["x"] == "win" else {"c", "n", "x"}
            )
            assert "board" not in wire and "turn" not in wire
            left = challenger._get_session(SESSION, CHALLENGER)
            right = responder._get_session(SESSION, RESPONDER)
            assert left.metadata["board"] == right.metadata["board"]
            assert left.metadata["turn"] == right.metadata["turn"]
            assert left.metadata["move_count"] == right.metadata["move_count"]

        assert challenger._get_session(SESSION, CHALLENGER).status == STATUS_COMPLETED
        assert responder._get_session(SESSION, RESPONDER).status == STATUS_COMPLETED

    def test_move_tracks_landing_coordinates_and_swaps_turn(self, app):
        _active_responder(app)
        result = app.handle_incoming(
            SESSION,
            CMD_MOVE,
            {"c": 3, "n": 1, "x": ""},
            CHALLENGER,
            RESPONDER,
        )
        assert result["error"] is None
        metadata = result["session"]["metadata"]
        assert metadata["board"] == EMPTY_BOARD[:38] + "A" + EMPTY_BOARD[39:]
        assert metadata["move_count"] == 1
        assert metadata["turn"] == RESPONDER
        assert (
            metadata["last_column"],
            metadata["last_row"],
            metadata["last_cell"],
        ) == (3, 5, 38)

    def test_wrong_turn_and_move_number_do_not_mutate(self, app):
        _active_responder(app)
        before = app._get_session(SESSION, RESPONDER).to_dict()
        wrong_turn = app.handle_incoming(
            SESSION,
            CMD_MOVE,
            {"c": 0, "n": 1, "x": ""},
            RESPONDER,
            RESPONDER,
        )
        assert wrong_turn["error"]["code"] == ERR_INVALID_MOVE
        assert app._get_session(SESSION, RESPONDER).to_dict() == before

        wrong_number = app.handle_incoming(
            SESSION,
            CMD_MOVE,
            {"c": 0, "n": 2, "x": ""},
            CHALLENGER,
            RESPONDER,
        )
        assert wrong_number["error"]["code"] == ERR_INVALID_MOVE
        assert app._get_session(SESSION, RESPONDER).to_dict() == before

    def test_full_column_rejects_seventh_drop(self, app):
        _active_responder(app)
        _play_incoming(app, [0, 0, 0, 0, 0, 0])
        before = app._get_session(SESSION, RESPONDER).to_dict()
        result = app.handle_incoming(
            SESSION,
            CMD_MOVE,
            {"c": 0, "n": 7, "x": ""},
            CHALLENGER,
            RESPONDER,
        )
        assert result["error"]["code"] == ERR_INVALID_MOVE
        assert "full" in result["error"]["msg"]
        assert app._get_session(SESSION, RESPONDER).to_dict() == before

    @pytest.mark.parametrize(
        "payload",
        [
            {"c": -1, "n": 1, "x": ""},
            {"c": 7, "n": 1, "x": ""},
            {"c": True, "n": 1, "x": ""},
            {"c": 0, "n": True, "x": ""},
            {"c": 0, "n": 1, "x": "", "board": EMPTY_BOARD},
            {"c": 0, "n": 1, "x": "", "t": RESPONDER},
            {"c": 0, "n": 1, "x": "bogus"},
            {"c": 0, "n": 1, "x": "", "w": CHALLENGER},
        ],
    )
    def test_malformed_wire_moves_are_rejected_without_mutation(self, app, payload):
        _active_responder(app)
        before = app._get_session(SESSION, RESPONDER).to_dict()
        result = app.handle_incoming(SESSION, CMD_MOVE, payload, CHALLENGER, RESPONDER)
        assert result["error"] is not None
        assert app._get_session(SESSION, RESPONDER).to_dict() == before

    def test_negative_wire_integer_is_a_protocol_shape_error(self, app):
        _active_responder(app)
        result = app.handle_incoming(
            SESSION,
            CMD_MOVE,
            {"c": -1, "n": 1, "x": ""},
            CHALLENGER,
            RESPONDER,
        )
        assert result["error"]["code"] == ERR_PROTOCOL_ERROR

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"c": True},
            {"c": "0"},
            {"c": 0, "n": 1},
        ],
    )
    def test_local_move_intent_is_exact(self, app, payload):
        _active_responder(app)
        with pytest.raises(OutgoingActionError):
            app.validate_outgoing(SESSION, CMD_MOVE, payload, RESPONDER, CHALLENGER)

    def test_local_column_range_is_validated(self, app):
        _active_responder(app)
        session = app._get_session(SESSION, RESPONDER)
        session.metadata["turn"] = RESPONDER
        for column in (-1, 7):
            with pytest.raises(OutgoingActionError) as exc:
                app.validate_outgoing(
                    SESSION, CMD_MOVE, {"c": column}, RESPONDER, CHALLENGER
                )
            assert exc.value.code == ERR_INVALID_MOVE


class TestTerminalClaims:
    def test_forged_early_win_is_rejected(self, app):
        _active_responder(app)
        before = app._get_session(SESSION, RESPONDER).to_dict()
        result = app.handle_incoming(
            SESSION,
            CMD_MOVE,
            {"c": 0, "n": 1, "x": "win", "w": CHALLENGER},
            CHALLENGER,
            RESPONDER,
        )
        assert result["error"]["code"] == ERR_INVALID_MOVE
        assert "Terminal mismatch" in result["error"]["msg"]
        assert app._get_session(SESSION, RESPONDER).to_dict() == before

    @pytest.mark.parametrize(
        "claim",
        [
            {"c": 3, "n": 7, "x": ""},
            {"c": 3, "n": 7, "x": "win", "w": RESPONDER},
        ],
    )
    def test_real_win_requires_terminal_and_authenticated_winner(self, app, claim):
        _active_responder(app)
        _play_incoming(app, WIN_SEQUENCES["horizontal"][:-1])
        before = app._get_session(SESSION, RESPONDER).to_dict()
        result = app.handle_incoming(SESSION, CMD_MOVE, claim, CHALLENGER, RESPONDER)
        assert result["error"]["code"] == ERR_INVALID_MOVE
        assert app._get_session(SESSION, RESPONDER).to_dict() == before

    def test_winner_key_is_present_only_on_win(self, app):
        _active_responder(app)
        session = app._get_session(SESSION, RESPONDER)
        session.metadata["turn"] = RESPONDER
        wire, _ = app.handle_outgoing(SESSION, CMD_MOVE, {"c": 2}, RESPONDER)
        assert wire == {"c": 2, "n": 1, "x": ""}


class TestDrawAndResignation:
    def test_incoming_draw_offer_can_be_accepted_locally(self, app):
        _active_responder(app)
        offered = app.handle_incoming(
            SESSION, CMD_DRAW_OFFER, {}, CHALLENGER, RESPONDER
        )
        assert offered["error"] is None
        assert offered["session"]["metadata"]["draw_offered_by"] == CHALLENGER
        app.validate_outgoing(SESSION, CMD_DRAW_ACCEPT, {}, RESPONDER, CHALLENGER)
        wire, _ = app.handle_outgoing(SESSION, CMD_DRAW_ACCEPT, {}, RESPONDER)
        assert wire == {}
        session = app._get_session(SESSION, RESPONDER)
        assert session.status == STATUS_COMPLETED
        assert session.metadata["terminal"] == "draw"
        assert session.metadata["turn"] == ""
        assert session.metadata["draw_offered"] is False

    def test_draw_decline_clears_owner_and_game_continues(self, app):
        _active_responder(app)
        app.handle_incoming(SESSION, CMD_DRAW_OFFER, {}, CHALLENGER, RESPONDER)
        result = app.handle_incoming(
            SESSION, CMD_DRAW_DECLINE, {}, RESPONDER, RESPONDER
        )
        assert result["error"] is None
        assert result["session"]["status"] == STATUS_ACTIVE
        assert result["session"]["metadata"]["draw_offered"] is False
        assert result["session"]["metadata"]["draw_offered_by"] == ""

    def test_offerer_cannot_answer_own_offer(self, app):
        _active_responder(app)
        app.handle_incoming(SESSION, CMD_DRAW_OFFER, {}, CHALLENGER, RESPONDER)
        before = app._get_session(SESSION, RESPONDER).to_dict()
        result = app.handle_incoming(
            SESSION, CMD_DRAW_ACCEPT, {}, CHALLENGER, RESPONDER
        )
        assert result["error"]["code"] == ERR_PROTOCOL_ERROR
        assert app._get_session(SESSION, RESPONDER).to_dict() == before

    def test_move_clears_outstanding_draw_offer(self, app):
        _active_responder(app)
        app.handle_incoming(SESSION, CMD_DRAW_OFFER, {}, RESPONDER, RESPONDER)
        result = app.handle_incoming(
            SESSION,
            CMD_MOVE,
            {"c": 0, "n": 1, "x": ""},
            CHALLENGER,
            RESPONDER,
        )
        assert result["error"] is None
        assert result["session"]["metadata"]["draw_offered"] is False
        assert result["session"]["metadata"]["draw_offered_by"] == ""

    def test_incoming_resignation_awards_other_player(self, app):
        _active_responder(app)
        result = app.handle_incoming(SESSION, CMD_RESIGN, {}, CHALLENGER, RESPONDER)
        assert result["error"] is None
        assert result["session"]["status"] == STATUS_COMPLETED
        assert result["session"]["metadata"]["terminal"] == "resign"
        assert result["session"]["metadata"]["winner"] == RESPONDER
        assert result["session"]["metadata"]["turn"] == ""

    def test_outgoing_resignation_awards_remote_player(self, app):
        _active_responder(app)
        wire, _ = app.handle_outgoing(SESSION, CMD_RESIGN, {}, RESPONDER)
        assert wire == {}
        session = app._get_session(SESSION, RESPONDER)
        assert session.status == STATUS_COMPLETED
        assert session.metadata["terminal"] == "resign"
        assert session.metadata["winner"] == CHALLENGER


class TestHydration:
    def test_valid_record_hydrates_and_can_continue(self):
        source = FourInARowApp()
        _active_responder(source)
        _play_incoming(source, [3])
        record = source._get_session(SESSION, RESPONDER).to_dict()

        restored = FourInARowApp()
        hydrated = restored.upsert_session(record)
        assert hydrated.metadata["board"] == record["metadata"]["board"]
        wire, _ = restored.handle_outgoing(SESSION, CMD_MOVE, {"c": 3}, RESPONDER)
        assert wire == {"c": 3, "n": 2, "x": ""}
        assert restored._get_session(SESSION, RESPONDER).metadata["last_row"] == 4

    def test_floating_or_malformed_board_is_rejected_before_insert(self):
        for board in ("A" + EMPTY_BOARD[1:], "_" * 41, "_" * 41 + "X"):
            app = FourInARowApp()
            record = Session(
                session_id=SESSION,
                identity_id=RESPONDER,
                app_id="four_in_a_row",
                app_version=1,
                contact_hash=CHALLENGER,
                initiator=CHALLENGER,
                status=STATUS_ACTIVE,
                metadata=_initial_metadata(SECOND_MARKER, CHALLENGER),
            )
            record.metadata["board"] = board
            with pytest.raises(InvalidEnvelope):
                app.upsert_session(record)
            assert app.get_session_state(SESSION, RESPONDER) == {}

    def test_empty_record_requires_explicit_null_last_move_fields(self):
        source = FourInARowApp()
        source.handle_incoming(SESSION, CMD_CHALLENGE, {}, CHALLENGER, RESPONDER)
        record = source._get_session(SESSION, RESPONDER).to_dict()
        del record["metadata"]["last_cell"]
        with pytest.raises(InvalidEnvelope, match="last move"):
            FourInARowApp().upsert_session(record)

    @pytest.mark.parametrize(
        "field,value",
        [
            ("move_count", 2),
            ("turn", CHALLENGER),
            ("last_column", 2),
            ("last_row", 4),
            ("last_cell", 31),
            ("my_marker", FIRST_MARKER),
            ("first_marker", SECOND_MARKER),
            ("first_turn", RESPONDER),
        ],
    )
    def test_count_turn_marker_and_last_move_tampering_is_rejected(self, field, value):
        source = FourInARowApp()
        _active_responder(source)
        _play_incoming(source, [3])
        record = source._get_session(SESSION, RESPONDER).to_dict()
        record["metadata"][field] = value
        with pytest.raises(InvalidEnvelope):
            FourInARowApp().upsert_session(record)

    def test_initiator_must_remain_the_first_turn_challenger(self):
        source = FourInARowApp()
        _active_responder(source)
        record = source._get_session(SESSION, RESPONDER).to_dict()
        record["initiator"] = RESPONDER
        with pytest.raises(InvalidEnvelope, match="challenger"):
            FourInARowApp().upsert_session(record)

    def test_completed_win_requires_board_winner_and_winning_identity(self):
        source = FourInARowApp()
        _active_responder(source)
        _play_incoming(source, WIN_SEQUENCES["horizontal"])
        record = source._get_session(SESSION, RESPONDER).to_dict()

        restored = FourInARowApp()
        restored.upsert_session(record)
        assert (
            restored.get_session_state(SESSION, RESPONDER)["metadata"]["winner"]
            == CHALLENGER
        )

        wrong_winner = copy.deepcopy(record)
        wrong_winner["metadata"]["winner"] = RESPONDER
        with pytest.raises(InvalidEnvelope):
            FourInARowApp().upsert_session(wrong_winner)

        missing_terminal = copy.deepcopy(record)
        missing_terminal["metadata"]["terminal"] = ""
        with pytest.raises(InvalidEnvelope):
            FourInARowApp().upsert_session(missing_terminal)

    def test_win_must_have_been_created_by_recorded_last_move(self):
        board = list(EMPTY_BOARD)
        for column in range(5):
            board[5 * COLUMNS + column] = FIRST_MARKER
        for row in (4, 5):
            for column in (5, 6):
                board[row * COLUMNS + column] = SECOND_MARKER
        record = Session(
            session_id=SESSION,
            identity_id=CHALLENGER,
            app_id="four_in_a_row",
            app_version=1,
            contact_hash=RESPONDER,
            initiator=CHALLENGER,
            status=STATUS_COMPLETED,
            metadata=_initial_metadata(FIRST_MARKER, CHALLENGER),
        )
        record.metadata.update(
            {
                "board": "".join(board),
                "turn": "",
                "move_count": 9,
                "last_column": 4,
                "last_row": 5,
                "last_cell": 39,
                "terminal": "win",
                "winner": CHALLENGER,
            }
        )
        with pytest.raises(InvalidEnvelope, match="continued"):
            FourInARowApp().upsert_session(record)

    def test_full_board_and_negotiated_draws_both_hydrate(self):
        drawn = FourInARowApp()
        _active_responder(drawn)
        _play_incoming(drawn, DRAW_SEQUENCE)
        full_record = drawn._get_session(SESSION, RESPONDER).to_dict()
        FourInARowApp().upsert_session(full_record)

        partial = FourInARowApp()
        _active_responder(partial)
        _play_incoming(partial, [3])
        negotiated = partial._get_session(SESSION, RESPONDER).to_dict()
        negotiated["status"] = STATUS_COMPLETED
        negotiated["metadata"]["turn"] = ""
        negotiated["metadata"]["terminal"] = "draw"
        FourInARowApp().upsert_session(negotiated)

        resurrected = copy.deepcopy(full_record)
        resurrected["status"] = STATUS_ACTIVE
        resurrected["metadata"]["turn"] = CHALLENGER
        resurrected["metadata"]["terminal"] = ""
        with pytest.raises(InvalidEnvelope):
            FourInARowApp().upsert_session(resurrected)

    def test_expiry_clears_draw_offer_and_round_trips(self):
        source = FourInARowApp()
        _active_responder(source)
        source.handle_incoming(SESSION, CMD_DRAW_OFFER, {}, CHALLENGER, RESPONDER)
        live = source._get_session(SESSION, RESPONDER)
        live.last_action_at = 0
        expiry_time = source.ttl[STATUS_ACTIVE] + TTL_GRACE_PERIOD + 1

        # Hydrating a valid but stale active record validates its active state,
        # applies expiry, then clears an offer that can no longer be answered.
        restored = FourInARowApp()
        expired = restored.upsert_session(live.to_dict(), now=expiry_time)
        assert expired.status == STATUS_EXPIRED
        assert expired.metadata["draw_offered"] is False
        assert expired.metadata["draw_offered_by"] == ""

        # Loading/listing performs the same Four-specific normalization, and
        # the resulting serialized record must hydrate again without repair.
        loaded = source.get_session_record(SESSION, RESPONDER, now=expiry_time)
        assert loaded.status == STATUS_EXPIRED
        assert loaded.metadata["draw_offered"] is False
        assert loaded.metadata["draw_offered_by"] == ""
        round_trip = FourInARowApp().upsert_session(loaded.to_dict(), now=expiry_time)
        assert round_trip.metadata == loaded.metadata


class TestManifestDiscoveryAndFallback:
    def test_manifest_is_canonical(self, app):
        manifest = app.get_manifest()
        assert manifest["app_id"] == "four_in_a_row"
        assert manifest["version"] == 1
        assert manifest["display_name"] == "Four in a Row"
        assert manifest["min_players"] == manifest["max_players"] == 2
        assert manifest["validation"] == "both"
        assert set(manifest["actions"]) >= {
            CMD_CHALLENGE,
            CMD_ACCEPT,
            CMD_DECLINE,
            CMD_MOVE,
            CMD_RESIGN,
            CMD_DRAW_OFFER,
            CMD_DRAW_ACCEPT,
            CMD_DRAW_DECLINE,
        }
        assert manifest["preferred_delivery"][CMD_MOVE] == "opportunistic"
        assert manifest["preferred_delivery"][CMD_RESIGN] == "direct"

    def test_package_discovery_and_public_export(self):
        import lrgp.apps
        from lrgp.apps import FourInARowApp as ExportedApp

        assert ExportedApp is FourInARowApp
        router = LrgpRouter()
        router.discover(lrgp.apps)
        discovered = router.get_app("four_in_a_row")
        assert isinstance(discovered, FourInARowApp)

    def test_router_prepares_the_compact_move_wire(self):
        router = LrgpRouter()
        router.register(FourInARowApp())
        router.dispatch_outgoing_to(
            "four_in_a_row",
            CMD_CHALLENGE,
            {},
            SESSION,
            CHALLENGER,
            RESPONDER,
        )
        accept = pack_envelope(
            "four_in_a_row",
            1,
            CMD_ACCEPT,
            SESSION,
            {"t": CHALLENGER},
            nonce=b"accept01",
        )
        applied = router.dispatch_incoming(accept, RESPONDER, CHALLENGER)
        assert applied["session"]["status"] == STATUS_ACTIVE

        prepared = router.dispatch_outgoing_to(
            "four_in_a_row",
            CMD_MOVE,
            {"c": 3},
            SESSION,
            CHALLENGER,
            RESPONDER,
        )
        assert prepared.envelope["p"] == {"c": 3, "n": 1, "x": ""}
        assert prepared.delivery_method == "opportunistic"

    def test_fallbacks_describe_normal_and_terminal_moves(self, app):
        assert app.render_fallback(CMD_MOVE, {"n": 1, "x": ""}) == (
            "[LRGP Four in a Row] Move 1"
        )
        assert app.render_fallback(CMD_MOVE, {"n": 7, "x": "win"}) == (
            "[LRGP Four in a Row] Four in a row!"
        )
        assert app.render_fallback(CMD_MOVE, {"n": 42, "x": "draw"}) == (
            "[LRGP Four in a Row] Game drawn!"
        )


class TestBinaryVectors:
    VECTOR_HASHES = {
        "four_in_a_row_challenge.bin": "b5f7610b897ebefe5f284b48ef80167258bbc78482d47b8c723ef60766a7ed3e",
        "four_in_a_row_accept.bin": "ca118a7542eb5df799585d4ad4770f1a7e6e2fb848604342c4a124e119fcb5a4",
        "four_in_a_row_move.bin": "e60e0241bd0602f19db1a69b0ea35fa42adaa509b69eccd00f76507c05ff64fa",
        "four_in_a_row_move_win.bin": "1880f4286f9708b17ee5b2aaae1bbdbebff874effe3a70118857dca0de16955a",
    }

    @staticmethod
    def _raw(name):
        with open(os.path.join(VECTORS_DIR, name), "rb") as vector:
            return vector.read()

    @classmethod
    def _load(cls, name):
        return unpack_envelope(pack_lxmf_fields(unpackb(cls._raw(name))))

    @pytest.mark.parametrize("filename", VECTOR_HASHES)
    def test_vector_bytes_have_locked_cross_language_digest(self, filename):
        assert (
            hashlib.sha256(self._raw(filename)).hexdigest()
            == self.VECTOR_HASHES[filename]
        )

    @pytest.mark.parametrize(
        "filename,command,payload,nonce",
        [
            ("four_in_a_row_challenge.bin", CMD_CHALLENGE, {}, "0001020304050607"),
            (
                "four_in_a_row_accept.bin",
                CMD_ACCEPT,
                {"t": CHALLENGER},
                "0102030405060708",
            ),
            (
                "four_in_a_row_move.bin",
                CMD_MOVE,
                {"c": 3, "n": 1, "x": ""},
                "0203040506070809",
            ),
            (
                "four_in_a_row_move_win.bin",
                CMD_MOVE,
                {"c": 0, "n": 7, "x": "win", "w": CHALLENGER},
                "030405060708090a",
            ),
        ],
    )
    def test_generator_reproduces_exact_fixture_bytes(
        self, filename, command, payload, nonce
    ):
        envelope = pack_envelope(
            "four_in_a_row",
            1,
            command,
            SESSION,
            payload,
            nonce=bytes.fromhex(nonce),
        )
        assert packb(envelope) == self._raw(filename)

    def test_challenge_vector(self):
        envelope = self._load("four_in_a_row_challenge.bin")
        assert envelope["a"] == "four_in_a_row.1"
        assert envelope["c"] == CMD_CHALLENGE
        assert envelope["s"] == SESSION
        assert envelope["p"] == {}
        assert envelope["n"] == bytes.fromhex("0001020304050607")

    def test_accept_vector(self):
        envelope = self._load("four_in_a_row_accept.bin")
        assert envelope["c"] == CMD_ACCEPT
        assert envelope["p"] == {"t": CHALLENGER}
        assert envelope["n"] == bytes.fromhex("0102030405060708")

    def test_ordinary_move_vector_has_no_board_or_turn(self):
        envelope = self._load("four_in_a_row_move.bin")
        assert envelope["c"] == CMD_MOVE
        assert envelope["p"] == {"c": 3, "n": 1, "x": ""}
        assert envelope["n"] == bytes.fromhex("0203040506070809")

    def test_winning_move_vector_carries_winner(self):
        envelope = self._load("four_in_a_row_move_win.bin")
        assert envelope["c"] == CMD_MOVE
        assert envelope["p"] == {
            "c": 0,
            "n": 7,
            "x": "win",
            "w": CHALLENGER,
        }
        assert envelope["n"] == bytes.fromhex("030405060708090a")


class TestWireBudget:
    def test_largest_canonical_move_fits_envelope_and_opportunistic_budget(self):
        envelope = pack_envelope(
            "four_in_a_row",
            1,
            CMD_MOVE,
            SESSION,
            {"c": 6, "n": 42, "x": "win", "w": CHALLENGER},
        )
        assert validate_envelope_size(envelope) <= ENVELOPE_MAX_PACKED
        content_size = measure_content_size(
            "",
            "[LRGP Four in a Row] Four in a row!",
            pack_lxmf_fields(envelope),
        )
        assert content_size <= OPPORTUNISTIC_MAX_CONTENT

    def test_oversized_wire_payload_is_rejected(self):
        with pytest.raises(EnvelopeTooLarge):
            pack_envelope(
                "four_in_a_row",
                1,
                CMD_MOVE,
                SESSION,
                {
                    "c": 0,
                    "n": 1,
                    "x": "",
                    "padding": "x" * 200,
                },
            )
