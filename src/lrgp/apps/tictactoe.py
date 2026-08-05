"""LRGP TicTacToe — built-in turn-based game with both-side validation."""

import os
import time

from ..app_base import GameBase
from ..session import Session, SessionStateMachine
from ..constants import (
    STATUS_PENDING, STATUS_ACTIVE, STATUS_COMPLETED,
    CMD_CHALLENGE, CMD_ACCEPT, CMD_DECLINE, CMD_MOVE,
    CMD_RESIGN, CMD_DRAW_OFFER, CMD_DRAW_ACCEPT, CMD_DRAW_DECLINE,
    CMD_ERROR, ERR_INVALID_MOVE, ERR_NOT_YOUR_TURN, ERR_SESSION_EXPIRED,
    ERR_PROTOCOL_ERROR,
)
from ..errors import (
    IllegalTransition, OutgoingActionError, SessionExpired, SessionNotFound,
    UnauthorizedPeer, UnsupportedAction, error_payload, incoming_error,
)

EMPTY_BOARD = "_________"

WIN_LINES = [
    (0, 1, 2), (3, 4, 5), (6, 7, 8),  # rows
    (0, 3, 6), (1, 4, 7), (2, 5, 8),  # columns
    (0, 4, 8), (2, 4, 6),             # diagonals
]


def _check_winner(board):
    """Check board for a winner. Returns 'X', 'O', or None."""
    for a, b, c in WIN_LINES:
        if board[a] != "_" and board[a] == board[b] == board[c]:
            return board[a]
    return None


def _check_draw(board):
    """Check if board is full with no winner."""
    return "_" not in board and _check_winner(board) is None


def _marker_for_move(move_num):
    """Odd moves = X, even moves = O."""
    return "X" if move_num % 2 == 1 else "O"


def _gen_session_id():
    """Generate a 16-char hex session ID (8 random bytes)."""
    return os.urandom(8).hex()


class TicTacToeApp(GameBase):
    app_id = "ttt"
    version = 1
    display_name = "Tic-Tac-Toe"
    icon = "ttt"
    session_type = "turn_based"
    max_players = 2
    min_players = 2
    validation = "both"
    genre = "strategy"
    turn_timeout = None
    actions = [
        CMD_CHALLENGE, CMD_ACCEPT, CMD_DECLINE, CMD_MOVE, CMD_RESIGN,
        CMD_DRAW_OFFER, CMD_DRAW_ACCEPT, CMD_DRAW_DECLINE, CMD_ERROR,
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
    ttl = {"pending": 86400, "active": 86400}

    def __init__(self):
        super().__init__()

    # --- GameBase required methods ---

    def handle_incoming(self, session_id, command, payload, sender_hash,
                        identity_id):
        payload_error = self._incoming_payload_error(command, payload)
        if payload_error:
            return incoming_error(
                ERR_PROTOCOL_ERROR, payload_error, command,
                self._get_session(session_id, identity_id),
            )
        try:
            if command == CMD_CHALLENGE:
                result = self._handle_challenge_in(
                    session_id, payload, sender_hash, identity_id
                )
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
                    result = {"session": session.to_dict(), "emit": None,
                              "error": payload}
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
            session = self.require_live_session(session_id, identity_id)
            session.metadata["draw_offered"] = True
            session.metadata["draw_offered_by"] = identity_id
            SessionStateMachine.apply_command(session, CMD_DRAW_OFFER)
            self._save_session(session)
            return {}, "[LRGP TTT] Offered a draw"
        if command == CMD_DRAW_ACCEPT:
            return self._handle_draw_accept_out(session_id, identity_id)
        if command == CMD_DRAW_DECLINE:
            session = self.require_live_session(session_id, identity_id)
            session.metadata["draw_offered"] = False
            session.metadata["draw_offered_by"] = ""
            SessionStateMachine.apply_command(session, CMD_DRAW_DECLINE)
            self._save_session(session)
            return {}, "[LRGP TTT] Declined draw offer"
        if command == CMD_ERROR:
            return payload, self.render_fallback(command, payload)
        raise UnsupportedAction(self.app_id, command)

    def validate_action(self, session_id, command, payload, sender_hash,
                        identity_id=""):
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
        if command == CMD_MOVE:
            return self._validate_move(session, payload, sender_hash)
        return True, None

    def validate_outgoing(self, session_id, command, payload, identity_id,
                          participant_hash=""):
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
                code = (ERR_NOT_YOUR_TURN if message == "Not your turn"
                        else ERR_INVALID_MOVE)
                raise OutgoingActionError(code, message, command)
        if command == CMD_DRAW_OFFER and session.metadata.get("draw_offered"):
            raise OutgoingActionError(
                ERR_PROTOCOL_ERROR, "A draw offer is already outstanding", command
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
        if session is None:
            return {}
        return session.to_dict()

    def render_fallback(self, command, payload):
        if command == CMD_CHALLENGE:
            return "[LRGP TTT] Sent a challenge!"
        if command == CMD_ACCEPT:
            return "[LRGP TTT] Challenge accepted"
        if command == CMD_DECLINE:
            return "[LRGP TTT] Challenge declined"
        if command == CMD_MOVE:
            terminal = payload.get("x", "")
            if terminal == "win":
                return "[LRGP TTT] X wins!" if _marker_for_move(payload.get("n", 0)) == "X" else "[LRGP TTT] O wins!"
            if terminal == "draw":
                return "[LRGP TTT] Game drawn!"
            return "[LRGP TTT] Move {}".format(payload.get("n", "?"))
        if command == CMD_RESIGN:
            return "[LRGP TTT] Resigned."
        if command == CMD_DRAW_OFFER:
            return "[LRGP TTT] Offered a draw"
        if command == CMD_DRAW_ACCEPT:
            return "[LRGP TTT] Draw accepted"
        if command == CMD_DRAW_DECLINE:
            return "[LRGP TTT] Draw declined"
        if command == CMD_ERROR:
            return "[LRGP TTT] Error: {}".format(payload.get("msg", "Unknown"))
        return "[LRGP TTT] {}".format(command)

    # --- Internal: incoming handlers ---

    def _handle_challenge_in(self, session_id, payload, sender_hash,
                              identity_id):
        existing = self._get_session(session_id, identity_id)
        if existing is not None:
            if existing.contact_hash != sender_hash:
                return incoming_error(
                    ERR_PROTOCOL_ERROR,
                    "Session id is already bound to another participant",
                    CMD_CHALLENGE,
                    existing,
                )
            # A retransmitted challenge with a fresh nonce is idempotent. It
            # must not overwrite progress or create a second UI event.
            return {"session": existing.to_dict(), "emit": None, "error": None}
        session = Session(
            session_id=session_id,
            identity_id=identity_id,
            app_id=self.app_id,
            app_version=self.version,
            contact_hash=sender_hash,
            initiator=sender_hash,
            status=STATUS_PENDING,
            metadata={
                "board": EMPTY_BOARD,
                "turn": "",
                "first_turn": sender_hash,
                "my_marker": "O",
                "move_count": 0,
                "winner": "",
                "terminal": "",
                "draw_offered": False,
                "draw_offered_by": "",
            },
            unread=1,
        )
        self._save_session(session)
        return {"session": session.to_dict(), "emit": {
            "type": "challenge", "session_id": session_id,
            "app_id": self.app_id, "from": sender_hash,
        }, "error": None}

    def _handle_accept_in(self, session_id, payload, sender_hash,
                           identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return {"session": None, "emit": None,
                    "error": {"code": "protocol_error", "msg": "Unknown session"}}

        expected_first = session.metadata.get("first_turn", "")
        if payload.get("b") != EMPTY_BOARD:
            return incoming_error(
                ERR_PROTOCOL_ERROR, "Accept board must be empty", CMD_ACCEPT,
                session,
            )
        if not expected_first or payload.get("t") != expected_first:
            return incoming_error(
                ERR_PROTOCOL_ERROR, "Accept first turn does not match session",
                CMD_ACCEPT, session,
            )

        SessionStateMachine.apply_command(session, CMD_ACCEPT)
        meta = session.metadata
        meta["board"] = EMPTY_BOARD
        meta["turn"] = expected_first
        session.unread = 1
        self._save_session(session)

        return {"session": session.to_dict(), "emit": {
            "type": "accept", "session_id": session_id,
            "app_id": self.app_id, "from": sender_hash,
        }, "error": None}

    def _handle_decline_in(self, session_id, sender_hash, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return {"session": None, "emit": None,
                    "error": {"code": "protocol_error", "msg": "Unknown session"}}

        SessionStateMachine.apply_command(session, CMD_DECLINE)
        session.unread = 1
        self._save_session(session)

        return {"session": session.to_dict(), "emit": {
            "type": "decline", "session_id": session_id,
            "app_id": self.app_id, "from": sender_hash,
        }, "error": None}

    def _handle_move_in(self, session_id, payload, sender_hash,
                         identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return {"session": None, "emit": None,
                    "error": {"code": "protocol_error", "msg": "Unknown session"}}

        # Validate (receiver side in "both" model)
        valid, err_msg = self._validate_move(session, payload, sender_hash)
        if not valid:
            return {"session": session.to_dict(), "emit": None,
                    "error": {"code": ERR_INVALID_MOVE, "msg": err_msg,
                              "ref": CMD_MOVE}}

        # Apply move
        meta = session.metadata
        meta["board"] = payload["b"]
        meta["move_count"] = payload["n"]
        meta["turn"] = payload.get("t", "")
        meta["terminal"] = payload.get("x", "")
        meta["winner"] = payload.get("w", "")
        meta["draw_offered"] = False
        meta["draw_offered_by"] = ""

        terminal = payload.get("x", "")
        SessionStateMachine.apply_command(session, CMD_MOVE,
                                          terminal=bool(terminal))
        session.unread = 1
        self._save_session(session)

        return {"session": session.to_dict(), "emit": {
            "type": "move", "session_id": session_id,
            "app_id": self.app_id, "from": sender_hash,
            "payload": payload,
        }, "error": None}

    def _handle_resign_in(self, session_id, sender_hash, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return {"session": None, "emit": None,
                    "error": {"code": "protocol_error", "msg": "Unknown session"}}

        SessionStateMachine.apply_command(session, CMD_RESIGN)
        meta = session.metadata
        meta["terminal"] = "resign"
        meta["draw_offered"] = False
        meta["draw_offered_by"] = ""
        if sender_hash == meta.get("first_turn", ""):
            meta["winner"] = identity_id
        else:
            meta["winner"] = meta.get("first_turn", "")
        session.unread = 1
        self._save_session(session)

        return {"session": session.to_dict(), "emit": {
            "type": "resign", "session_id": session_id,
            "app_id": self.app_id, "from": sender_hash,
        }, "error": None}

    def _handle_draw_offer_in(self, session_id, sender_hash, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return {"session": None, "emit": None,
                    "error": {"code": "protocol_error", "msg": "Unknown session"}}
        if session.metadata.get("draw_offered"):
            return incoming_error(
                ERR_PROTOCOL_ERROR, "A draw offer is already outstanding",
                CMD_DRAW_OFFER, session,
            )
        session.metadata["draw_offered"] = True
        session.metadata["draw_offered_by"] = sender_hash
        SessionStateMachine.apply_command(session, CMD_DRAW_OFFER)
        session.unread = 1
        self._save_session(session)
        return {"session": session.to_dict(), "emit": {
            "type": "draw_offer", "session_id": session_id,
            "app_id": self.app_id, "from": sender_hash,
        }, "error": None}

    def _handle_draw_accept_in(self, session_id, sender_hash, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return {"session": None, "emit": None,
                    "error": {"code": "protocol_error", "msg": "Unknown session"}}

        offerer = session.metadata.get("draw_offered_by", "")
        if not session.metadata.get("draw_offered") or not offerer:
            return incoming_error(
                ERR_PROTOCOL_ERROR, "No draw offer is outstanding",
                CMD_DRAW_ACCEPT, session,
            )
        if offerer == sender_hash:
            return incoming_error(
                ERR_PROTOCOL_ERROR, "Cannot answer your own draw offer",
                CMD_DRAW_ACCEPT, session,
            )
        SessionStateMachine.apply_command(session, CMD_DRAW_ACCEPT)
        session.metadata["terminal"] = "draw"
        session.metadata["draw_offered"] = False
        session.metadata["draw_offered_by"] = ""
        session.unread = 1
        self._save_session(session)

        return {"session": session.to_dict(), "emit": {
            "type": "draw_accept", "session_id": session_id,
            "app_id": self.app_id, "from": sender_hash,
        }, "error": None}

    def _handle_draw_decline_in(self, session_id, sender_hash, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return {"session": None, "emit": None,
                    "error": {"code": "protocol_error", "msg": "Unknown session"}}
        offerer = session.metadata.get("draw_offered_by", "")
        if not session.metadata.get("draw_offered") or not offerer:
            return incoming_error(
                ERR_PROTOCOL_ERROR, "No draw offer is outstanding",
                CMD_DRAW_DECLINE, session,
            )
        if offerer == sender_hash:
            return incoming_error(
                ERR_PROTOCOL_ERROR, "Cannot answer your own draw offer",
                CMD_DRAW_DECLINE, session,
            )
        session.metadata["draw_offered"] = False
        session.metadata["draw_offered_by"] = ""
        SessionStateMachine.apply_command(session, CMD_DRAW_DECLINE)
        session.unread = 1
        self._save_session(session)
        return {"session": session.to_dict(), "emit": {
            "type": "draw_decline", "session_id": session_id,
            "app_id": self.app_id, "from": sender_hash,
        }, "error": None}

    # --- Internal: outgoing handlers ---

    def _handle_challenge_out(self, session_id, identity_id):
        if not session_id:
            session_id = _gen_session_id()
        existing = self._get_session(session_id, identity_id)
        if existing is not None:
            return {}, "[LRGP TTT] Sent a challenge!"
        session = Session(
            session_id=session_id,
            identity_id=identity_id,
            app_id=self.app_id,
            app_version=self.version,
            contact_hash="",
            initiator=identity_id,
            status=STATUS_PENDING,
            metadata={
                "board": EMPTY_BOARD,
                "turn": "",
                "first_turn": identity_id,
                "my_marker": "X",
                "move_count": 0,
                "winner": "",
                "terminal": "",
                "draw_offered": False,
                "draw_offered_by": "",
            },
        )
        self._save_session(session)
        return {}, "[LRGP TTT] Sent a challenge!"

    def _handle_accept_out(self, session_id, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return {}, "[LRGP TTT] Challenge accepted"

        SessionStateMachine.apply_command(session, CMD_ACCEPT)
        meta = session.metadata
        first = meta.get("first_turn", session.initiator)
        meta["board"] = EMPTY_BOARD
        meta["turn"] = first
        self._save_session(session)

        return {
            "b": EMPTY_BOARD,
            "t": first,
        }, "[LRGP TTT] Challenge accepted"

    def _handle_decline_out(self, session_id, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is not None:
            SessionStateMachine.apply_command(session, CMD_DECLINE)
            self._save_session(session)
        return {}, "[LRGP TTT] Challenge declined"

    def _handle_move_out(self, session_id, payload, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return {}, "[LRGP TTT] Session not found"

        meta = session.metadata
        if session.status != STATUS_ACTIVE:
            return {}, "[LRGP TTT] Session is not active ({})".format(session.status)

        current_turn = meta.get("turn", "")
        if current_turn != identity_id:
            return {}, "[LRGP TTT] Not your turn"

        index = payload.get("i")
        if not isinstance(index, int) or index < 0 or index > 8:
            return {}, "[LRGP TTT] Invalid cell index"

        board = list(meta["board"])
        if index >= len(board) or board[index] != "_":
            return {}, "[LRGP TTT] Cell {} is already occupied".format(index)

        move_num = meta["move_count"] + 1
        marker = _marker_for_move(move_num)

        board[index] = marker
        new_board = "".join(board)

        winner = _check_winner(new_board)
        is_draw = _check_draw(new_board)

        if winner:
            terminal = "win"
            winner_hash = identity_id
            next_turn = ""
        elif is_draw:
            terminal = "draw"
            winner_hash = ""
            next_turn = ""
        else:
            terminal = ""
            winner_hash = ""
            first_turn = meta.get("first_turn", "")
            next_turn = (
                session.contact_hash if identity_id == first_turn else first_turn
            )
            if not next_turn:
                return {}, "[LRGP TTT] Opponent unknown"

        enriched = {
            "i": index,
            "b": new_board,
            "n": move_num,
            "t": next_turn,
            "x": terminal,
        }
        if terminal == "win":
            enriched["w"] = winner_hash

        # Update local session
        meta["board"] = new_board
        meta["move_count"] = move_num
        meta["turn"] = next_turn
        meta["terminal"] = terminal
        meta["winner"] = winner_hash if terminal == "win" else ""
        meta["draw_offered"] = False
        meta["draw_offered_by"] = ""
        SessionStateMachine.apply_command(session, CMD_MOVE,
                                          terminal=bool(terminal))
        self._save_session(session)

        return enriched, self.render_fallback(CMD_MOVE, enriched)

    def _handle_resign_out(self, session_id, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is not None:
            SessionStateMachine.apply_command(session, CMD_RESIGN)
            meta = session.metadata
            meta["terminal"] = "resign"
            meta["winner"] = session.contact_hash
            meta["draw_offered"] = False
            meta["draw_offered_by"] = ""
            self._save_session(session)
        return {}, "[LRGP TTT] Resigned."

    def _handle_draw_accept_out(self, session_id, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is not None:
            SessionStateMachine.apply_command(session, CMD_DRAW_ACCEPT)
            session.metadata["terminal"] = "draw"
            session.metadata["draw_offered"] = False
            session.metadata["draw_offered_by"] = ""
            self._save_session(session)
        return {}, "[LRGP TTT] Draw accepted"

    # --- Validation ---

    @staticmethod
    def _incoming_payload_error(command, payload):
        empty_commands = {
            CMD_CHALLENGE, CMD_DECLINE, CMD_RESIGN, CMD_DRAW_OFFER,
            CMD_DRAW_ACCEPT, CMD_DRAW_DECLINE,
        }
        if command in empty_commands:
            return None if payload == {} else "{} payload must be empty".format(command)
        if command == CMD_ACCEPT:
            if set(payload) != {"b", "t"}:
                return "accept payload must contain exactly b and t"
            if not isinstance(payload.get("b"), str) or not isinstance(payload.get("t"), str):
                return "accept b and t must be strings"
        elif command == CMD_MOVE:
            terminal = payload.get("x")
            expected = {"i", "b", "n", "t", "x"}
            if terminal == "win":
                expected.add("w")
            if set(payload) != expected:
                return "move payload has non-canonical keys"
            if (isinstance(payload.get("i"), bool)
                    or not isinstance(payload.get("i"), int)
                    or isinstance(payload.get("n"), bool)
                    or not isinstance(payload.get("n"), int)
                    or not isinstance(payload.get("b"), str)
                    or not isinstance(payload.get("t"), str)
                    or terminal not in ("", "win", "draw")):
                return "move payload has invalid value types"
            if terminal == "win" and not isinstance(payload.get("w"), str):
                return "move winner must be a string"
        return None

    @staticmethod
    def _outgoing_payload_error(command, payload):
        if command == CMD_MOVE:
            if (set(payload) != {"i"}
                    or isinstance(payload.get("i"), bool)
                    or not isinstance(payload.get("i"), int)):
                return "local move intent must contain exactly integer i"
            return None
        if command != CMD_ERROR and payload != {}:
            return "{} local payload must be empty".format(command)
        return None

    def _validate_local_move(self, session, payload, identity_id):
        meta = session.metadata
        if session.status != STATUS_ACTIVE:
            return False, "Session is not active (status={})".format(session.status)
        turn = meta.get("turn", "")
        if not turn:
            return False, "Turn is required before moves"
        if turn != identity_id:
            return False, "Not your turn"
        index = payload.get("i")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index <= 8:
            return False, "Invalid cell index: {}".format(index)
        board = meta.get("board", "")
        if len(board) != 9 or board[index] != "_":
            return False, "Cell {} is already occupied".format(index)
        return True, None

    def _validate_move(self, session, payload, sender_hash):
        meta = session.metadata

        # 1. Session must be active
        if session.status != STATUS_ACTIVE:
            return False, "Session is not active (status={})".format(session.status)

        # 2. Must be sender's turn
        turn = meta.get("turn", "")
        if not turn:
            return False, "Turn is required before moves"
        if turn != sender_hash:
            return False, "Not your turn"

        index = payload.get("i")
        board_str = payload.get("b", "")
        move_num = payload.get("n", 0)
        terminal = payload.get("x", "")

        # 3. Index must be valid and cell must be empty
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index <= 8:
            return False, "Invalid cell index: {}".format(index)
        if isinstance(move_num, bool) or not isinstance(move_num, int):
            return False, "Invalid move number: {}".format(move_num)

        old_board = meta.get("board", EMPTY_BOARD)
        if old_board[index] != "_":
            return False, "Cell {} is already occupied".format(index)

        # 4. Marker must match move number
        marker = _marker_for_move(move_num)
        expected_board = old_board[:index] + marker + old_board[index + 1:]
        if board_str != expected_board:
            return False, "Board mismatch: expected {}, got {}".format(
                expected_board, board_str)

        # 5. Move number must be sequential
        expected_num = meta.get("move_count", 0) + 1
        if move_num != expected_num:
            return False, "Move number mismatch: expected {}, got {}".format(
                expected_num, move_num)

        # 6. Terminal status must match computed result
        winner = _check_winner(board_str)
        is_draw = _check_draw(board_str)

        if winner and terminal != "win":
            return False, "Board shows a win but terminal='{}'".format(terminal)
        if is_draw and terminal != "draw":
            return False, "Board is full (draw) but terminal='{}'".format(terminal)
        if not winner and not is_draw and terminal:
            return False, "No win/draw but terminal='{}'".format(terminal)

        winner_claim = payload.get("w", "")
        if terminal == "win":
            if winner_claim != sender_hash:
                return False, "Winner must be the authenticated move sender"
        elif winner_claim != "":
            return False, "Winner must be empty on a non-winning move"

        # 7. Turn must be opponent (or empty if terminal)
        next_turn = payload.get("t", "")
        if terminal:
            if next_turn != "":
                return False, "Turn should be empty on terminal move"
        else:
            if next_turn == sender_hash:
                return False, "Turn cannot be the sender after their own move"
            if not next_turn:
                return False, "Turn is required on non-terminal move"
            first_turn = meta.get("first_turn", "")
            expected_next_turn = (
                session.identity_id if sender_hash == first_turn else first_turn
            )
            if expected_next_turn and next_turn != expected_next_turn:
                return False, "Turn mismatch: expected {}, got {}".format(
                    expected_next_turn, next_turn)

        return True, None
