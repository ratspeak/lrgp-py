"""LRGP Four in a Row — compact gravity moves with both-side validation.

The wire carries only the selected column, a one-based move number, and the
sender's terminal claim.  Both peers reconstruct the same 7-column by 6-row
board locally; board snapshots and next-turn identities are never transmitted
on move actions.
"""

import os

from ..app_base import GameBase
from ..constants import (
    CMD_ACCEPT,
    CMD_CHALLENGE,
    CMD_DECLINE,
    CMD_DRAW_ACCEPT,
    CMD_DRAW_DECLINE,
    CMD_DRAW_OFFER,
    CMD_ERROR,
    CMD_MOVE,
    CMD_RESIGN,
    ERR_INVALID_MOVE,
    ERR_NOT_YOUR_TURN,
    ERR_PROTOCOL_ERROR,
    ERR_SESSION_EXPIRED,
    STATUS_ACTIVE,
    STATUS_COMPLETED,
    STATUS_DECLINED,
    STATUS_EXPIRED,
    STATUS_PENDING,
)
from ..errors import (
    IllegalTransition,
    InvalidEnvelope,
    OutgoingActionError,
    SessionExpired,
    SessionNotFound,
    UnauthorizedPeer,
    UnsupportedAction,
    error_payload,
    incoming_error,
)
from ..session import Session, SessionStateMachine

COLUMNS = 7
ROWS = 6
CELL_COUNT = COLUMNS * ROWS
EMPTY_BOARD = "_" * CELL_COUNT
FIRST_MARKER = "A"
SECOND_MARKER = "B"

_DIRECTIONS = ((0, 1), (1, 0), (1, 1), (1, -1))


def _gen_session_id():
    return os.urandom(8).hex()


def _marker_for_move(move_num):
    """Return the canonical marker for a one-based move number."""
    return FIRST_MARKER if move_num % 2 == 1 else SECOND_MARKER


def _is_canonical_board(board):
    """Return whether *board* has the canonical shape and obeys gravity."""
    if (
        not isinstance(board, str)
        or len(board) != CELL_COUNT
        or any(cell not in "_AB" for cell in board)
    ):
        return False

    for column in range(COLUMNS):
        found_empty = False
        for row in range(ROWS - 1, -1, -1):
            cell = board[row * COLUMNS + column]
            if cell == "_":
                found_empty = True
            elif found_empty:
                return False
    return True


def _drop_cell(board, column):
    """Return ``(row, cell)`` for a gravity drop, or ``None`` if unavailable."""
    if (
        not _is_canonical_board(board)
        or isinstance(column, bool)
        or not isinstance(column, int)
        or not 0 <= column < COLUMNS
    ):
        return None
    for row in range(ROWS - 1, -1, -1):
        cell = row * COLUMNS + column
        if board[cell] == "_":
            return row, cell
    return None


def _check_winner(board):
    """Return ``A`` or ``B`` when the board contains four in a row."""
    if not isinstance(board, str) or len(board) != CELL_COUNT:
        return None
    for row in range(ROWS):
        for column in range(COLUMNS):
            marker = board[row * COLUMNS + column]
            if marker == "_":
                continue
            for delta_row, delta_column in _DIRECTIONS:
                cells = []
                for step in range(4):
                    target_row = row + delta_row * step
                    target_column = column + delta_column * step
                    if not 0 <= target_row < ROWS or not 0 <= target_column < COLUMNS:
                        break
                    cells.append(board[target_row * COLUMNS + target_column])
                if len(cells) == 4 and all(cell == marker for cell in cells):
                    return marker
    return None


def _winning_markers(board):
    """Return all markers with at least one four-cell line."""
    if not isinstance(board, str) or len(board) != CELL_COUNT:
        return []
    winners = []
    for marker in (FIRST_MARKER, SECOND_MARKER):
        for row in range(ROWS):
            found = False
            for column in range(COLUMNS):
                if board[row * COLUMNS + column] != marker:
                    continue
                for delta_row, delta_column in _DIRECTIONS:
                    if all(
                        0 <= row + delta_row * step < ROWS
                        and 0 <= column + delta_column * step < COLUMNS
                        and board[
                            (row + delta_row * step) * COLUMNS
                            + column
                            + delta_column * step
                        ]
                        == marker
                        for step in range(1, 4)
                    ):
                        found = True
                        break
                if found:
                    break
            if found:
                break
        if found:
            winners.append(marker)
    return winners


def _check_draw(board):
    return (
        isinstance(board, str)
        and len(board) == CELL_COUNT
        and "_" not in board
        and _check_winner(board) is None
    )


def _other_player(session, player):
    if player == session.identity_id and session.contact_hash:
        return session.contact_hash
    if player == session.contact_hash and session.identity_id:
        return session.identity_id
    return ""


def _initial_metadata(my_marker, first_turn):
    return {
        "board": EMPTY_BOARD,
        "turn": "",
        "first_turn": first_turn,
        "my_marker": my_marker,
        "first_marker": FIRST_MARKER,
        "second_marker": SECOND_MARKER,
        "move_count": 0,
        "last_column": None,
        "last_row": None,
        "last_cell": None,
        "winner": "",
        "terminal": "",
        "draw_offered": False,
        "draw_offered_by": "",
    }


def _clear_draw_offer(metadata):
    metadata["draw_offered"] = False
    metadata["draw_offered_by"] = ""


class FourInARowApp(GameBase):
    """Built-in deterministic two-player gravity game."""

    app_id = "four_in_a_row"
    version = 1
    display_name = "Four in a Row"
    icon = "four_in_a_row"
    session_type = "turn_based"
    max_players = 2
    min_players = 2
    validation = "both"
    genre = "strategy"
    turn_timeout = None
    actions = [
        CMD_CHALLENGE,
        CMD_ACCEPT,
        CMD_DECLINE,
        CMD_MOVE,
        CMD_RESIGN,
        CMD_DRAW_OFFER,
        CMD_DRAW_ACCEPT,
        CMD_DRAW_DECLINE,
        CMD_ERROR,
    ]
    preferred_delivery = {
        CMD_CHALLENGE: "opportunistic",
        CMD_ACCEPT: "opportunistic",
        CMD_DECLINE: "opportunistic",
        CMD_MOVE: "opportunistic",
        CMD_RESIGN: "direct",
        CMD_DRAW_OFFER: "opportunistic",
        CMD_DRAW_ACCEPT: "direct",
        CMD_DRAW_DECLINE: "direct",
        CMD_ERROR: "opportunistic",
    }
    ttl = {"pending": 86400, "active": 604800}

    def _get_session(self, session_id, identity_id="", now=None):
        """Load a session and normalize draw state after local expiry."""
        session = super()._get_session(session_id, identity_id, now=now)
        if (
            session is not None
            and session.status == STATUS_EXPIRED
            and isinstance(session.metadata, dict)
        ):
            if session.metadata.get("draw_offered") or session.metadata.get(
                "draw_offered_by"
            ):
                _clear_draw_offer(session.metadata)
                self._save_session(session)
        return session

    # --- GameBase required methods ---

    def handle_incoming(self, session_id, command, payload, sender_hash, identity_id):
        payload_error = self._incoming_payload_error(command, payload)
        if payload_error:
            return incoming_error(
                ERR_PROTOCOL_ERROR,
                payload_error,
                command,
                self._get_session(session_id, identity_id),
            )

        try:
            if command == CMD_CHALLENGE:
                result = self._handle_challenge_in(session_id, sender_hash, identity_id)
            else:
                session = self.require_live_session(session_id, identity_id)
                if command == CMD_ACCEPT:
                    result = self._handle_accept_in(
                        session_id, payload, sender_hash, identity_id
                    )
                elif command == CMD_DECLINE:
                    result = self._handle_decline_in(
                        session_id, sender_hash, identity_id
                    )
                elif command == CMD_MOVE:
                    result = self._handle_move_in(
                        session_id, payload, sender_hash, identity_id
                    )
                elif command == CMD_RESIGN:
                    result = self._handle_resign_in(
                        session_id, sender_hash, identity_id
                    )
                elif command == CMD_DRAW_OFFER:
                    result = self._handle_draw_offer_in(
                        session_id, sender_hash, identity_id
                    )
                elif command == CMD_DRAW_ACCEPT:
                    result = self._handle_draw_accept_in(
                        session_id, sender_hash, identity_id
                    )
                elif command == CMD_DRAW_DECLINE:
                    result = self._handle_draw_decline_in(
                        session_id, sender_hash, identity_id
                    )
                elif command == CMD_ERROR:
                    result = {
                        "session": session.to_dict(),
                        "emit": None,
                        "error": payload,
                    }
                else:
                    raise UnsupportedAction(self.app_id, command)
        except SessionNotFound as exc:
            return incoming_error(ERR_PROTOCOL_ERROR, str(exc), command)
        except SessionExpired as exc:
            return incoming_error(ERR_SESSION_EXPIRED, str(exc), command)
        except UnauthorizedPeer as exc:
            return incoming_error(ERR_PROTOCOL_ERROR, str(exc), command)
        except (IllegalTransition, UnsupportedAction) as exc:
            return incoming_error(ERR_PROTOCOL_ERROR, str(exc), command)

        if result.get("error") is not None:
            raw = result["error"]
            result["error"] = error_payload(
                raw.get("code", ERR_PROTOCOL_ERROR),
                raw.get("msg", "Action rejected"),
                raw.get("ref", command),
            )
        return result

    def handle_outgoing(self, session_id, command, payload, identity_id):
        if command == CMD_CHALLENGE:
            return self._handle_challenge_out(session_id, identity_id)
        if command == CMD_ACCEPT:
            return self._handle_accept_out(session_id, identity_id)
        if command == CMD_DECLINE:
            return self._handle_decline_out(session_id, identity_id)
        if command == CMD_MOVE:
            return self._handle_move_out(session_id, payload, identity_id)
        if command == CMD_RESIGN:
            return self._handle_resign_out(session_id, identity_id)
        if command == CMD_DRAW_OFFER:
            return self._handle_draw_offer_out(session_id, identity_id)
        if command == CMD_DRAW_ACCEPT:
            return self._handle_draw_accept_out(session_id, identity_id)
        if command == CMD_DRAW_DECLINE:
            return self._handle_draw_decline_out(session_id, identity_id)
        if command == CMD_ERROR:
            return payload, self.render_fallback(command, payload)
        raise UnsupportedAction(self.app_id, command)

    def validate_action(
        self, session_id, command, payload, sender_hash, identity_id=""
    ):
        payload_error = self._incoming_payload_error(command, payload)
        if payload_error:
            return False, payload_error
        session = self._get_session(session_id, identity_id)
        if command == CMD_CHALLENGE:
            if session is None:
                return True, None
            if session.contact_hash != sender_hash:
                raise UnauthorizedPeer(session_id)
            return True, None
        if session is None:
            raise SessionNotFound(session_id)
        if session.status == "expired":
            raise SessionExpired(session_id)
        self.authorize_session(session, sender_hash)
        if command == CMD_ACCEPT:
            expected_first = session.metadata.get("first_turn", "")
            if not expected_first or payload.get("t") != expected_first:
                return False, "Accept first turn does not match challenge"
        if command == CMD_MOVE:
            return self._validate_move(session, payload, sender_hash)
        return True, None

    def validate_outgoing(
        self, session_id, command, payload, identity_id, participant_hash=""
    ):
        payload_error = self._outgoing_payload_error(command, payload)
        if payload_error:
            raise OutgoingActionError(ERR_PROTOCOL_ERROR, payload_error, command)
        session = super().validate_outgoing(
            session_id, command, payload, identity_id, participant_hash
        )
        if command == CMD_CHALLENGE:
            return session
        if command == CMD_MOVE:
            valid, message = self._validate_local_move(session, payload, identity_id)
            if not valid:
                code = (
                    ERR_NOT_YOUR_TURN
                    if message == "Not your turn"
                    else ERR_INVALID_MOVE
                )
                raise OutgoingActionError(code, message, command)
        if command == CMD_DRAW_OFFER and session.metadata.get("draw_offered"):
            raise OutgoingActionError(
                ERR_PROTOCOL_ERROR,
                "A draw offer is already outstanding",
                command,
            )
        if command in (CMD_DRAW_ACCEPT, CMD_DRAW_DECLINE):
            offerer = session.metadata.get("draw_offered_by", "")
            if not session.metadata.get("draw_offered") or not offerer:
                raise OutgoingActionError(
                    ERR_PROTOCOL_ERROR, "No draw offer is outstanding", command
                )
            if offerer == identity_id:
                raise OutgoingActionError(
                    ERR_PROTOCOL_ERROR, "Cannot answer your own draw offer", command
                )
        return session

    def get_session_state(self, session_id, identity_id):
        session = self._get_session(session_id, identity_id)
        return session.to_dict() if session is not None else {}

    def render_fallback(self, command, payload):
        prefix = "[LRGP Four in a Row]"
        if command == CMD_CHALLENGE:
            return "{} Sent a challenge!".format(prefix)
        if command == CMD_ACCEPT:
            return "{} Challenge accepted".format(prefix)
        if command == CMD_DECLINE:
            return "{} Challenge declined".format(prefix)
        if command == CMD_MOVE:
            if payload.get("x") == "win":
                return "{} Four in a row!".format(prefix)
            if payload.get("x") == "draw":
                return "{} Game drawn!".format(prefix)
            return "{} Move {}".format(prefix, payload.get("n", 0))
        if command == CMD_RESIGN:
            return "{} Resigned.".format(prefix)
        if command == CMD_DRAW_OFFER:
            return "{} Offered a draw".format(prefix)
        if command == CMD_DRAW_ACCEPT:
            return "{} Draw accepted".format(prefix)
        if command == CMD_DRAW_DECLINE:
            return "{} Draw declined".format(prefix)
        return "{} {}".format(prefix, command)

    def upsert_session(self, record, now=None):
        """Hydrate only a complete, semantically consistent game record."""
        session = record if isinstance(record, Session) else Session.from_dict(record)
        # Historical records may already have crossed their expiry boundary
        # before Four-specific normalization existed. Clear only that stale
        # lifecycle state before semantic validation. A still-active record is
        # validated as active first so age cannot hide malformed game state.
        if session.status == STATUS_EXPIRED and isinstance(session.metadata, dict):
            _clear_draw_offer(session.metadata)
        self._validate_restored_session(session)
        restored = super().upsert_session(session, now=now)
        if restored.status == STATUS_EXPIRED:
            _clear_draw_offer(restored.metadata)
            self._save_session(restored)
        return restored

    hydrate_session = upsert_session

    @staticmethod
    def _validate_restored_session(session):
        """Mirror the Rust built-in's semantic persistence boundary."""
        metadata = session.metadata
        if not isinstance(metadata, dict):
            raise InvalidEnvelope("Restored metadata must be a map")

        board = metadata.get("board")
        if not isinstance(board, str):
            raise InvalidEnvelope("Restored board must be a string")
        if not _is_canonical_board(board):
            raise InvalidEnvelope("Restored board is not a canonical gravity board")

        move_count = metadata.get("move_count")
        if (
            isinstance(move_count, bool)
            or not isinstance(move_count, int)
            or not 0 <= move_count <= CELL_COUNT
        ):
            raise InvalidEnvelope(
                "Restored move_count must be an integer from 0 through 42"
            )
        occupied = CELL_COUNT - board.count("_")
        first_count = board.count(FIRST_MARKER)
        second_count = board.count(SECOND_MARKER)
        if (
            occupied != move_count
            or first_count != (move_count + 1) // 2
            or second_count != move_count // 2
        ):
            raise InvalidEnvelope(
                "Restored board counts do not match first-player alternation"
            )

        if (
            metadata.get("first_marker") != FIRST_MARKER
            or metadata.get("second_marker") != SECOND_MARKER
        ):
            raise InvalidEnvelope("Restored marker metadata is invalid")
        first_turn = metadata.get("first_turn")
        if not isinstance(first_turn, str) or not first_turn:
            raise InvalidEnvelope("Restored first_turn is required")
        if first_turn != session.initiator:
            raise InvalidEnvelope("Restored first_turn must equal the challenger")
        if first_turn not in (session.identity_id, session.contact_hash):
            raise InvalidEnvelope("Restored first_turn is not a bound participant")
        expected_my_marker = (
            FIRST_MARKER if session.identity_id == first_turn else SECOND_MARKER
        )
        if metadata.get("my_marker") != expected_my_marker:
            raise InvalidEnvelope("Restored my_marker does not match first_turn")

        last_keys = ("last_column", "last_row", "last_cell")
        last_values = tuple(metadata.get(key) for key in last_keys)
        if move_count == 0:
            if any(
                key not in metadata or metadata[key] is not None for key in last_keys
            ):
                raise InvalidEnvelope("An empty game must not have a last move")
        else:
            if any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in last_values
            ):
                raise InvalidEnvelope("A non-empty game requires a complete last move")
            column, row, cell = last_values
            if (
                not 0 <= column < COLUMNS
                or not 0 <= row < ROWS
                or cell != row * COLUMNS + column
                or board[cell] != _marker_for_move(move_count)
            ):
                raise InvalidEnvelope(
                    "Restored last move is inconsistent with the board"
                )
            if any(board[above * COLUMNS + column] != "_" for above in range(row)):
                raise InvalidEnvelope(
                    "Restored last move is not the top disc in its column"
                )

        winners = _winning_markers(board)
        if len(winners) > 1:
            raise InvalidEnvelope("Restored board has two winners")
        terminal = metadata.get("terminal")
        winner = metadata.get("winner")
        turn = metadata.get("turn")
        if not isinstance(terminal, str):
            raise InvalidEnvelope("Restored terminal must be a string")
        if not isinstance(winner, str):
            raise InvalidEnvelope("Restored winner must be a string")
        if not isinstance(turn, str):
            raise InvalidEnvelope("Restored turn must be a string")
        second_turn = _other_player(session, first_turn)
        if not second_turn:
            raise InvalidEnvelope("Restored session has no bound opponent")
        expected_turn = first_turn if move_count % 2 == 0 else second_turn

        if session.status in (STATUS_PENDING, STATUS_DECLINED):
            if move_count != 0 or turn or terminal or winner:
                raise InvalidEnvelope(
                    "Pending or declined session contains game progress"
                )
        elif session.status == STATUS_ACTIVE:
            if (
                turn != expected_turn
                or terminal
                or winner
                or winners
                or _check_draw(board)
            ):
                raise InvalidEnvelope(
                    "Active session metadata is terminal or out of turn"
                )
        elif session.status == STATUS_COMPLETED:
            if turn:
                raise InvalidEnvelope("Completed session must not have a turn")
            if terminal == "win":
                if not winners:
                    raise InvalidEnvelope("Winning session has no four-in-a-row")
                marker = winners[0]
                if marker != _marker_for_move(move_count):
                    raise InvalidEnvelope("Winning marker was not the final mover")
                expected_winner = first_turn if marker == FIRST_MARKER else second_turn
                if winner != expected_winner:
                    raise InvalidEnvelope("Winning identity does not match the board")
                before = list(board)
                before[last_values[2]] = "_"
                if _winning_markers("".join(before)):
                    raise InvalidEnvelope(
                        "Restored game continued after a winning move"
                    )
            elif terminal == "draw":
                if winner or winners:
                    raise InvalidEnvelope("Draw session contains a winner")
            elif terminal == "resign":
                if winner not in (session.identity_id, session.contact_hash):
                    raise InvalidEnvelope("Resignation winner is not a participant")
                if winners or _check_draw(board):
                    raise InvalidEnvelope("Resignation followed a terminal board")
            else:
                raise InvalidEnvelope("Completed session has invalid terminal metadata")
        elif session.status == STATUS_EXPIRED:
            if terminal or winner or winners:
                raise InvalidEnvelope("Expired session contains terminal game metadata")
            if turn and turn != expected_turn:
                raise InvalidEnvelope("Expired session has an invalid turn")
        else:
            raise InvalidEnvelope("Restored session has an unknown status")

        draw_offered = metadata.get("draw_offered")
        draw_owner = metadata.get("draw_offered_by")
        if not isinstance(draw_offered, bool):
            raise InvalidEnvelope("Restored draw_offered must be a boolean")
        if not isinstance(draw_owner, str):
            raise InvalidEnvelope("Restored draw_offered_by must be a string")
        if draw_offered:
            if session.status != STATUS_ACTIVE or draw_owner not in (
                session.identity_id,
                session.contact_hash,
            ):
                raise InvalidEnvelope(
                    "Restored draw offer has an invalid owner or status"
                )
        elif draw_owner:
            raise InvalidEnvelope("Restored cleared draw offer still has an owner")

    # --- Incoming handlers ---

    def _handle_challenge_in(self, session_id, sender_hash, identity_id):
        existing = self._get_session(session_id, identity_id)
        if existing is not None:
            if existing.contact_hash != sender_hash:
                return incoming_error(
                    ERR_PROTOCOL_ERROR,
                    "Session id is already bound to another participant",
                    CMD_CHALLENGE,
                    existing,
                )
            return {"session": existing.to_dict(), "emit": None, "error": None}

        session = Session(
            session_id=session_id,
            identity_id=identity_id,
            app_id=self.app_id,
            app_version=self.version,
            contact_hash=sender_hash,
            initiator=sender_hash,
            status=STATUS_PENDING,
            metadata=_initial_metadata(SECOND_MARKER, sender_hash),
            unread=1,
        )
        self._save_session(session)
        return {
            "session": session.to_dict(),
            "emit": {
                "type": "challenge",
                "session_id": session_id,
                "app_id": self.app_id,
                "from": sender_hash,
            },
            "error": None,
        }

    def _handle_accept_in(self, session_id, payload, sender_hash, identity_id):
        session = self._get_session(session_id, identity_id)
        expected_first = session.metadata.get("first_turn", "")
        if not expected_first or payload.get("t") != expected_first:
            return incoming_error(
                ERR_PROTOCOL_ERROR,
                "Accept first turn does not match challenge",
                CMD_ACCEPT,
                session,
            )
        SessionStateMachine.apply_command(session, CMD_ACCEPT)
        session.metadata["turn"] = expected_first
        session.unread = 1
        self._save_session(session)
        return {
            "session": session.to_dict(),
            "emit": {
                "type": "accept",
                "session_id": session_id,
                "app_id": self.app_id,
                "from": sender_hash,
            },
            "error": None,
        }

    def _handle_decline_in(self, session_id, sender_hash, identity_id):
        session = self._get_session(session_id, identity_id)
        SessionStateMachine.apply_command(session, CMD_DECLINE)
        session.unread = 1
        self._save_session(session)
        return self._incoming_event(session, "decline", sender_hash)

    def _handle_move_in(self, session_id, payload, sender_hash, identity_id):
        session = self._get_session(session_id, identity_id)
        valid, message, projection = self._validate_move_details(
            session, payload, sender_hash
        )
        if not valid:
            return incoming_error(ERR_INVALID_MOVE, message, CMD_MOVE, session)
        self._apply_move(session, payload, sender_hash, projection)
        session.unread = 1
        self._save_session(session)
        result = self._incoming_event(session, "move", sender_hash)
        result["emit"]["payload"] = payload
        return result

    def _handle_resign_in(self, session_id, sender_hash, identity_id):
        session = self._get_session(session_id, identity_id)
        SessionStateMachine.apply_command(session, CMD_RESIGN)
        metadata = session.metadata
        metadata["turn"] = ""
        metadata["terminal"] = "resign"
        metadata["winner"] = _other_player(session, sender_hash)
        _clear_draw_offer(metadata)
        session.unread = 1
        self._save_session(session)
        return self._incoming_event(session, "resign", sender_hash)

    def _handle_draw_offer_in(self, session_id, sender_hash, identity_id):
        session = self._get_session(session_id, identity_id)
        if session.metadata.get("draw_offered"):
            return incoming_error(
                ERR_PROTOCOL_ERROR,
                "A draw offer is already outstanding",
                CMD_DRAW_OFFER,
                session,
            )
        SessionStateMachine.apply_command(session, CMD_DRAW_OFFER)
        session.metadata["draw_offered"] = True
        session.metadata["draw_offered_by"] = sender_hash
        session.unread = 1
        self._save_session(session)
        return self._incoming_event(session, "draw_offer", sender_hash)

    def _handle_draw_accept_in(self, session_id, sender_hash, identity_id):
        session = self._get_session(session_id, identity_id)
        error = self._draw_response_error(session, sender_hash)
        if error:
            return incoming_error(ERR_PROTOCOL_ERROR, error, CMD_DRAW_ACCEPT, session)
        SessionStateMachine.apply_command(session, CMD_DRAW_ACCEPT)
        metadata = session.metadata
        metadata["turn"] = ""
        metadata["terminal"] = "draw"
        metadata["winner"] = ""
        _clear_draw_offer(metadata)
        session.unread = 1
        self._save_session(session)
        return self._incoming_event(session, "draw_accept", sender_hash)

    def _handle_draw_decline_in(self, session_id, sender_hash, identity_id):
        session = self._get_session(session_id, identity_id)
        error = self._draw_response_error(session, sender_hash)
        if error:
            return incoming_error(ERR_PROTOCOL_ERROR, error, CMD_DRAW_DECLINE, session)
        SessionStateMachine.apply_command(session, CMD_DRAW_DECLINE)
        _clear_draw_offer(session.metadata)
        session.unread = 1
        self._save_session(session)
        return self._incoming_event(session, "draw_decline", sender_hash)

    def _incoming_event(self, session, event_type, sender_hash):
        return {
            "session": session.to_dict(),
            "emit": {
                "type": event_type,
                "session_id": session.session_id,
                "app_id": self.app_id,
                "from": sender_hash,
            },
            "error": None,
        }

    @staticmethod
    def _draw_response_error(session, sender_hash):
        offerer = session.metadata.get("draw_offered_by", "")
        if not session.metadata.get("draw_offered") or not offerer:
            return "No draw offer is outstanding"
        if offerer == sender_hash:
            return "Cannot answer your own draw offer"
        return None

    # --- Outgoing handlers ---

    def _handle_challenge_out(self, session_id, identity_id):
        if not session_id:
            session_id = _gen_session_id()
        if self._get_session(session_id, identity_id) is not None:
            return {}, "[LRGP Four in a Row] Sent a challenge!"
        session = Session(
            session_id=session_id,
            identity_id=identity_id,
            app_id=self.app_id,
            app_version=self.version,
            contact_hash="",
            initiator=identity_id,
            status=STATUS_PENDING,
            metadata=_initial_metadata(FIRST_MARKER, identity_id),
        )
        self._save_session(session)
        return {}, "[LRGP Four in a Row] Sent a challenge!"

    def _handle_accept_out(self, session_id, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return {}, "[LRGP Four in a Row] Session not found"
        SessionStateMachine.apply_command(session, CMD_ACCEPT)
        first_turn = session.metadata.get("first_turn", "")
        session.metadata["turn"] = first_turn
        self._save_session(session)
        return {"t": first_turn}, "[LRGP Four in a Row] Challenge accepted"

    def _handle_decline_out(self, session_id, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is not None:
            SessionStateMachine.apply_command(session, CMD_DECLINE)
            self._save_session(session)
        return {}, "[LRGP Four in a Row] Challenge declined"

    def _handle_move_out(self, session_id, payload, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return {}, "[LRGP Four in a Row] Session not found"
        valid, message = self._validate_local_move(session, payload, identity_id)
        if not valid:
            return {}, "[LRGP Four in a Row] {}".format(message)

        column = payload["c"]
        move_num = session.metadata.get("move_count", 0) + 1
        landing = _drop_cell(session.metadata["board"], column)
        row, cell = landing
        board = list(session.metadata["board"])
        board[cell] = _marker_for_move(move_num)
        new_board = "".join(board)
        terminal = (
            "win"
            if _check_winner(new_board)
            else "draw"
            if _check_draw(new_board)
            else ""
        )
        wire = {"c": column, "n": move_num, "x": terminal}
        if terminal == "win":
            wire["w"] = identity_id

        valid, message, projection = self._validate_move_details(
            session, wire, identity_id
        )
        if not valid:  # Defensive for direct hook callers.
            return {}, "[LRGP Four in a Row] {}".format(message)
        self._apply_move(session, wire, identity_id, projection)
        self._save_session(session)
        return wire, self.render_fallback(CMD_MOVE, wire)

    def _handle_resign_out(self, session_id, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is not None:
            SessionStateMachine.apply_command(session, CMD_RESIGN)
            metadata = session.metadata
            metadata["turn"] = ""
            metadata["terminal"] = "resign"
            metadata["winner"] = session.contact_hash
            _clear_draw_offer(metadata)
            self._save_session(session)
        return {}, "[LRGP Four in a Row] Resigned."

    def _handle_draw_offer_out(self, session_id, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is not None:
            SessionStateMachine.apply_command(session, CMD_DRAW_OFFER)
            session.metadata["draw_offered"] = True
            session.metadata["draw_offered_by"] = identity_id
            self._save_session(session)
        return {}, "[LRGP Four in a Row] Offered a draw"

    def _handle_draw_accept_out(self, session_id, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is not None:
            SessionStateMachine.apply_command(session, CMD_DRAW_ACCEPT)
            metadata = session.metadata
            metadata["turn"] = ""
            metadata["terminal"] = "draw"
            metadata["winner"] = ""
            _clear_draw_offer(metadata)
            self._save_session(session)
        return {}, "[LRGP Four in a Row] Draw accepted"

    def _handle_draw_decline_out(self, session_id, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is not None:
            SessionStateMachine.apply_command(session, CMD_DRAW_DECLINE)
            _clear_draw_offer(session.metadata)
            self._save_session(session)
        return {}, "[LRGP Four in a Row] Draw declined"

    # --- Validation and move reconstruction ---

    @staticmethod
    def _incoming_payload_error(command, payload):
        if not isinstance(payload, dict):
            return "{} payload must be a map".format(command)
        empty_commands = {
            CMD_CHALLENGE,
            CMD_DECLINE,
            CMD_RESIGN,
            CMD_DRAW_OFFER,
            CMD_DRAW_ACCEPT,
            CMD_DRAW_DECLINE,
        }
        if command in empty_commands:
            return None if payload == {} else "{} payload must be empty".format(command)
        if command == CMD_ACCEPT:
            if set(payload) != {"t"} or not isinstance(payload.get("t"), str):
                return "accept payload must contain exactly string t"
        elif command == CMD_MOVE:
            terminal = payload.get("x")
            if not isinstance(terminal, str) or terminal not in ("", "win", "draw"):
                return "move x must be '', 'win', or 'draw'"
            expected = {"c", "n", "x", "w"} if terminal == "win" else {"c", "n", "x"}
            if set(payload) != expected:
                return "move payload has non-canonical keys"
            if (
                isinstance(payload.get("c"), bool)
                or not isinstance(payload.get("c"), int)
                or payload.get("c") < 0
                or isinstance(payload.get("n"), bool)
                or not isinstance(payload.get("n"), int)
                or payload.get("n") < 0
            ):
                return "move c and n must be non-negative integers"
            if terminal == "win" and not isinstance(payload.get("w"), str):
                return "move winner must be a string"
        return None

    @staticmethod
    def _outgoing_payload_error(command, payload):
        if not isinstance(payload, dict):
            return "{} local payload must be a map".format(command)
        if command == CMD_MOVE:
            if (
                set(payload) != {"c"}
                or isinstance(payload.get("c"), bool)
                or not isinstance(payload.get("c"), int)
            ):
                return "local move intent must contain exactly integer c"
            return None
        if command != CMD_ERROR and payload != {}:
            return "{} local payload must be empty".format(command)
        return None

    def _validate_local_move(self, session, payload, identity_id):
        if session.status != STATUS_ACTIVE:
            return False, "Session is not active ({})".format(session.status)
        if session.metadata.get("turn", "") != identity_id:
            return False, "Not your turn"
        column = payload.get("c")
        if (
            isinstance(column, bool)
            or not isinstance(column, int)
            or not 0 <= column < COLUMNS
        ):
            return False, "Invalid column"
        board = session.metadata.get("board")
        if not _is_canonical_board(board):
            return False, "Stored board is invalid"
        if _drop_cell(board, column) is None:
            return False, "Column {} is full".format(column)
        if not _other_player(session, identity_id):
            return False, "Opponent unknown"
        return True, None

    def _validate_move(self, session, payload, sender_hash):
        valid, message, _projection = self._validate_move_details(
            session, payload, sender_hash
        )
        return valid, message

    def _validate_move_details(self, session, payload, sender_hash):
        if session.status != STATUS_ACTIVE:
            return False, "Session is not active ({})".format(session.status), None
        if session.metadata.get("turn", "") != sender_hash:
            return False, "Not your turn", None

        column = payload.get("c")
        if (
            isinstance(column, bool)
            or not isinstance(column, int)
            or not 0 <= column < COLUMNS
        ):
            return False, "Invalid column", None
        move_num = payload.get("n")
        if isinstance(move_num, bool) or not isinstance(move_num, int):
            return False, "Move number is required", None
        previous_count = session.metadata.get("move_count", 0)
        if isinstance(previous_count, bool) or not isinstance(previous_count, int):
            return False, "Stored move count is invalid", None
        expected_num = previous_count + 1
        if move_num != expected_num:
            return (
                False,
                (
                    "Move number mismatch: expected {}, got {}".format(
                        expected_num, move_num
                    )
                ),
                None,
            )

        old_board = session.metadata.get("board")
        if not _is_canonical_board(old_board):
            return False, "Stored board is invalid", None
        landing = _drop_cell(old_board, column)
        if landing is None:
            return False, "Column {} is full".format(column), None
        row, cell = landing
        board = list(old_board)
        board[cell] = _marker_for_move(move_num)
        new_board = "".join(board)
        computed = (
            "win"
            if _check_winner(new_board)
            else "draw"
            if _check_draw(new_board)
            else ""
        )
        claimed = payload.get("x", "")
        if claimed != computed:
            return (
                False,
                (
                    "Terminal mismatch: expected '{}', got '{}'".format(
                        computed, claimed
                    )
                ),
                None,
            )

        winner = payload.get("w", "")
        if computed == "win" and winner != sender_hash:
            return (
                False,
                ("Winner mismatch: expected {}, got {}".format(sender_hash, winner)),
                None,
            )
        if computed != "win" and winner:
            return False, "Winner is only valid on a winning move", None

        next_turn = "" if computed else _other_player(session, sender_hash)
        if not computed and not next_turn:
            return False, "Opponent unknown", None
        projection = (new_board, column, row, cell, next_turn)
        return True, None, projection

    @staticmethod
    def _apply_move(session, payload, sender_hash, projection):
        board, column, row, cell, next_turn = projection
        terminal = payload["x"]
        metadata = session.metadata
        metadata["board"] = board
        metadata["turn"] = next_turn
        metadata["move_count"] = payload["n"]
        metadata["last_column"] = column
        metadata["last_row"] = row
        metadata["last_cell"] = cell
        metadata["terminal"] = terminal
        metadata["winner"] = sender_hash if terminal == "win" else ""
        _clear_draw_offer(metadata)
        SessionStateMachine.apply_command(session, CMD_MOVE, terminal=bool(terminal))


__all__ = [
    "CELL_COUNT",
    "COLUMNS",
    "EMPTY_BOARD",
    "FIRST_MARKER",
    "FourInARowApp",
    "ROWS",
    "SECOND_MARKER",
]
