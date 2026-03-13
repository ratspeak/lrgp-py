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
)
from ..errors import ValidationError

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
        CMD_DRAW_OFFER, CMD_DRAW_ACCEPT, CMD_DRAW_DECLINE,
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
    }
    ttl = {"pending": 86400, "active": 86400}

    def __init__(self):
        # In-memory session store for standalone use.
        # Can be replaced with LrgpStore for persistence.
        self._sessions = {}

    def _get_session(self, session_id, identity_id=""):
        key = (session_id, identity_id)
        return self._sessions.get(key)

    def _save_session(self, session):
        key = (session.session_id, session.identity_id)
        self._sessions[key] = session

    # --- GameBase required methods ---

    def handle_incoming(self, session_id, command, payload, sender_hash,
                        identity_id):
        if command == CMD_CHALLENGE:
            return self._handle_challenge_in(session_id, payload,
                                              sender_hash, identity_id)
        if command == CMD_ACCEPT:
            return self._handle_accept_in(session_id, payload,
                                           sender_hash, identity_id)
        if command == CMD_DECLINE:
            return self._handle_decline_in(session_id, sender_hash,
                                            identity_id)
        if command == CMD_MOVE:
            return self._handle_move_in(session_id, payload,
                                         sender_hash, identity_id)
        if command == CMD_RESIGN:
            return self._handle_resign_in(session_id, sender_hash,
                                           identity_id)
        if command == CMD_DRAW_OFFER:
            return self._handle_draw_offer_in(session_id, sender_hash,
                                               identity_id)
        if command == CMD_DRAW_ACCEPT:
            return self._handle_draw_accept_in(session_id, sender_hash,
                                                identity_id)
        if command == CMD_DRAW_DECLINE:
            return self._handle_draw_decline_in(session_id, sender_hash,
                                                 identity_id)
        if command == CMD_ERROR:
            return {"session": None, "emit": None, "error": payload}

        return {"session": None, "emit": None,
                "error": {"code": "protocol_error",
                          "msg": "Unknown command: {}".format(command)}}

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
            return {}, "[LRGP TTT] Offered a draw"
        if command == CMD_DRAW_ACCEPT:
            return self._handle_draw_accept_out(session_id, identity_id)
        if command == CMD_DRAW_DECLINE:
            return {}, "[LRGP TTT] Declined draw offer"
        return payload, "[LRGP TTT] {}".format(command)

    def validate_action(self, session_id, command, payload, sender_hash):
        session = self._get_session(session_id)
        if session is None:
            if command == CMD_CHALLENGE:
                return True, None
            return False, "Session not found"

        if SessionStateMachine.check_expiry(session, self.ttl):
            self._save_session(session)
            return False, "Session expired"

        if command == CMD_MOVE:
            return self._validate_move(session, payload, sender_hash)

        return True, None

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

        SessionStateMachine.apply_command(session, CMD_ACCEPT)
        meta = session.metadata
        meta["board"] = payload.get("b", EMPTY_BOARD)
        meta["turn"] = payload.get("t", meta["first_turn"])
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
        session.metadata["draw_offered"] = True
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

        SessionStateMachine.apply_command(session, CMD_DRAW_ACCEPT)
        session.metadata["terminal"] = "draw"
        session.metadata["draw_offered"] = False
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
        session.metadata["draw_offered"] = False
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
        session = Session(
            session_id=session_id,
            identity_id=identity_id,
            app_id=self.app_id,
            app_version=self.version,
            contact_hash="",  # set by caller
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
            return payload, self.render_fallback(CMD_MOVE, payload)

        meta = session.metadata
        board = list(meta["board"])
        index = payload["i"]
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
            next_turn = meta.get("first_turn", "") if marker == "O" else session.contact_hash
            if next_turn == identity_id:
                next_turn = session.contact_hash

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
            self._save_session(session)
        return {}, "[LRGP TTT] Resigned."

    def _handle_draw_accept_out(self, session_id, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is not None:
            SessionStateMachine.apply_command(session, CMD_DRAW_ACCEPT)
            session.metadata["terminal"] = "draw"
            session.metadata["draw_offered"] = False
            self._save_session(session)
        return {}, "[LRGP TTT] Draw accepted"

    # --- Validation ---

    def _validate_move(self, session, payload, sender_hash):
        meta = session.metadata

        # 1. Session must be active
        if session.status != STATUS_ACTIVE:
            return False, "Session is not active (status={})".format(session.status)

        # 2. Must be sender's turn
        if meta.get("turn") and meta["turn"] != sender_hash:
            return False, "Not your turn"

        index = payload.get("i")
        board_str = payload.get("b", "")
        move_num = payload.get("n", 0)
        terminal = payload.get("x", "")

        # 3. Index must be valid and cell must be empty
        if not isinstance(index, int) or index < 0 or index > 8:
            return False, "Invalid cell index: {}".format(index)

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

        # 7. Turn must be opponent (or empty if terminal)
        next_turn = payload.get("t", "")
        if terminal:
            if next_turn != "":
                return False, "Turn should be empty on terminal move"
        else:
            if next_turn == sender_hash:
                return False, "Turn cannot be the sender after their own move"

        return True, None
