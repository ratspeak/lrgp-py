"""LRGP Chess — built-in turn-based game with both-side validation.

Wire format mirrors lrgp-rs's chess.rs exactly: UCI moves only on the wire,
local board state reconstructed by replaying the UCI history. Backed by
``python-chess`` (install via ``pip install 'lrgp[chess]'``).

Wire payload keys::

    m   UCI move, e.g. "e2e4" or "e7e8q" (promotion)
    n   ply counter, 0-based (0 = White's first move)
    x   terminal status: "" | "win" | "draw"
    r   terminal / draw-claim reason (2-3 chars, see below)
    w   winner identity hash (move with x="win") OR White-player hash (accept)

Terminal reason codes (kept short to fit ``ENVELOPE_MAX_PACKED``)::

    cm   checkmate
    sm   stalemate
    ins  insufficient material
    3fr  threefold repetition (claimed)
    50m  fifty-move rule (claimed)
    rsn  resignation
    agr  draw by agreement
"""

import os
from typing import Optional

try:
    import chess as _chess
except ImportError as exc:  # pragma: no cover - exercised via integration only
    raise ImportError(
        "lrgp.apps.chess requires the [chess] extra. "
        "Install with: pip install 'lrgp[chess]'"
    ) from exc

from ..app_base import GameBase
from ..session import Session, SessionStateMachine
from ..constants import (
    STATUS_PENDING, STATUS_ACTIVE, STATUS_COMPLETED,
    CMD_CHALLENGE, CMD_ACCEPT, CMD_DECLINE, CMD_MOVE,
    CMD_RESIGN, CMD_DRAW_OFFER, CMD_DRAW_ACCEPT, CMD_DRAW_DECLINE,
    CMD_ERROR, ERR_INVALID_MOVE, ERR_NOT_YOUR_TURN, ERR_PROTOCOL_ERROR,
    ERR_SESSION_EXPIRED,
)
from ..errors import (
    IllegalTransition, OutgoingActionError, SessionExpired, SessionNotFound,
    UnauthorizedPeer, UnsupportedAction, error_payload, incoming_error,
)

STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

R_CHECKMATE = "cm"
R_STALEMATE = "sm"
R_INSUFFICIENT = "ins"
R_THREEFOLD = "3fr"
R_FIFTY_MOVE = "50m"
R_RESIGN = "rsn"
R_AGREEMENT = "agr"

KEY_MOVE = "m"
KEY_PLY = "n"
KEY_TERMINAL = "x"
KEY_REASON = "r"
KEY_WINNER = "w"
KEY_WHITE = "w"  # Reused in ACCEPT payload — disambiguated by command

# Process-global coin pin for deterministic test vectors.
_FORCED_COIN: Optional[bool] = None


def force_coin(challenger_is_white: Optional[bool]) -> None:
    """Pin the coin flip for tests. ``None`` clears the pin.

    Process-global; callers must serialize their own access in parallel runs.
    """
    global _FORCED_COIN
    _FORCED_COIN = challenger_is_white


def _flip_responder_coin() -> bool:
    """``True`` if the challenger gets White."""
    if _FORCED_COIN is not None:
        return _FORCED_COIN
    return os.urandom(1)[0] & 1 == 0


def _gen_session_id() -> str:
    return os.urandom(8).hex()


def _replay_board(moves):
    board = _chess.Board(STARTING_FEN)
    for uci in moves:
        try:
            move = board.parse_uci(uci)
        except Exception as exc:
            raise ValueError("invalid uci '{}' in history: {}".format(uci, exc))
        if move not in board.legal_moves:
            raise ValueError("illegal move {} on {}".format(uci, board.fen()))
        board.push(move)
    return board


def _legal_uci(board) -> list:
    return [m.uci() for m in board.legal_moves]


def _detect_auto_terminal(board):
    """Return (terminal, reason) for the post-move position, or ('', '')."""
    if board.is_checkmate():
        return "win", R_CHECKMATE
    if board.is_stalemate():
        return "draw", R_STALEMATE
    if board.is_insufficient_material():
        return "draw", R_INSUFFICIENT
    return "", ""


def _initial_metadata(white_hash, black_hash, my_color):
    return {
        "fen": STARTING_FEN,
        "moves": [],
        "turn": white_hash,
        "first_turn": white_hash,
        "white": white_hash,
        "black": black_hash,
        "my_color": my_color,
        "in_check": False,
        "winner": "",
        "terminal": "",
        "reason": "",
        "draw_offered": False,
        "draw_offered_by": "",
        "draw_offer_reason": "",
    }


def _refresh_derived(session, board, moves):
    meta = session.metadata
    meta["fen"] = board.fen()
    meta["moves"] = list(moves)
    meta["in_check"] = board.is_check()


class ChessApp(GameBase):
    app_id = "chess"
    version = 1
    display_name = "Chess"
    icon = "chess"
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
    ttl = {"pending": 86400, "active": 604800}  # 1 day pending, 7 days active

    def __init__(self):
        super().__init__()

    # --- GameBase required methods ---

    def handle_incoming(self, session_id, command, payload, sender_hash, identity_id):
        payload_error = self._incoming_payload_error(command, payload)
        if payload_error:
            return incoming_error(
                ERR_PROTOCOL_ERROR, payload_error, command,
                self._get_session(session_id, identity_id),
            )
        try:
            if command == CMD_CHALLENGE:
                result = self._handle_challenge_in(
                    session_id, sender_hash, identity_id
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
                        session_id, payload, sender_hash, identity_id
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
            session = self.require_live_session(session_id, identity_id)
            SessionStateMachine.apply_command(session, CMD_DECLINE)
            self._save_session(session)
            return {}, "[LRGP Chess] Challenge declined"
        if command == CMD_MOVE:
            return self._handle_move_out(session_id, payload, identity_id)
        if command == CMD_RESIGN:
            return self._handle_resign_out(session_id, identity_id)
        if command == CMD_DRAW_OFFER:
            return self._handle_draw_offer_out(session_id, payload, identity_id)
        if command == CMD_DRAW_ACCEPT:
            return self._handle_draw_accept_out(session_id, identity_id)
        if command == CMD_DRAW_DECLINE:
            session = self.require_live_session(session_id, identity_id)
            session.metadata["draw_offered"] = False
            session.metadata["draw_offered_by"] = ""
            session.metadata["draw_offer_reason"] = ""
            SessionStateMachine.apply_command(session, CMD_DRAW_DECLINE)
            self._save_session(session)
            return {}, "[LRGP Chess] Declined draw offer"
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
        return session.to_dict() if session else {}

    def render_fallback(self, command, payload):
        if command == CMD_CHALLENGE:
            return "[LRGP Chess] Sent a challenge!"
        if command == CMD_ACCEPT:
            return "[LRGP Chess] Challenge accepted"
        if command == CMD_DECLINE:
            return "[LRGP Chess] Challenge declined"
        if command == CMD_MOVE:
            terminal = payload.get(KEY_TERMINAL, "")
            uci = payload.get(KEY_MOVE, "?")
            if terminal == "win":
                return "[LRGP Chess] {}#".format(uci)
            if terminal == "draw":
                return "[LRGP Chess] {} (½-½)".format(uci)
            return "[LRGP Chess] {}".format(uci)
        if command == CMD_RESIGN:
            return "[LRGP Chess] Resigned."
        if command == CMD_DRAW_OFFER:
            reason = payload.get(KEY_REASON, "")
            if reason == R_THREEFOLD:
                return "[LRGP Chess] Claim: threefold"
            if reason == R_FIFTY_MOVE:
                return "[LRGP Chess] Claim: 50-move rule"
            return "[LRGP Chess] Offered a draw"
        if command == CMD_DRAW_ACCEPT:
            return "[LRGP Chess] Draw accepted"
        if command == CMD_DRAW_DECLINE:
            return "[LRGP Chess] Draw declined"
        if command == CMD_ERROR:
            return "[LRGP Chess] Error: {}".format(payload.get("msg", "Unknown"))
        return "[LRGP Chess] {}".format(command)

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
            metadata=_initial_metadata("", sender_hash, ""),  # color set on accept
            unread=1,
        )
        self._save_session(session)
        return {"session": session.to_dict(), "emit": {
            "type": "challenge", "session_id": session_id,
            "app_id": self.app_id, "from": sender_hash,
        }, "error": None}

    def _handle_accept_in(self, session_id, payload, sender_hash, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return _err(ERR_PROTOCOL_ERROR, "Unknown session")
        white = payload.get(KEY_WHITE)
        if white not in (identity_id, session.contact_hash):
            return incoming_error(
                ERR_PROTOCOL_ERROR,
                "Accept white player must be one of the bound participants",
                CMD_ACCEPT,
                session,
            )
        SessionStateMachine.apply_command(session, CMD_ACCEPT)
        meta = session.metadata
        black = identity_id if white == sender_hash else sender_hash
        meta["white"] = white
        meta["black"] = black
        meta["turn"] = white
        meta["first_turn"] = white
        meta["my_color"] = "w" if identity_id == white else "b"
        session.unread = 1
        self._save_session(session)
        return {"session": session.to_dict(), "emit": {
            "type": "accept", "session_id": session_id,
            "app_id": self.app_id, "from": sender_hash,
        }, "error": None}

    def _handle_decline_in(self, session_id, sender_hash, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return _err(ERR_PROTOCOL_ERROR, "Unknown session")
        SessionStateMachine.apply_command(session, CMD_DECLINE)
        session.unread = 1
        self._save_session(session)
        return {"session": session.to_dict(), "emit": {
            "type": "decline", "session_id": session_id,
            "app_id": self.app_id, "from": sender_hash,
        }, "error": None}

    def _handle_move_in(self, session_id, payload, sender_hash, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return _err(ERR_PROTOCOL_ERROR, "Unknown session")
        meta = session.metadata
        if meta.get("turn") != sender_hash:
            return _err("not_your_turn", "Not sender's turn")

        uci = payload.get(KEY_MOVE)
        if not isinstance(uci, str):
            return _err(ERR_INVALID_MOVE, "Move missing")

        moves = list(meta.get("moves", []))
        claimed_ply = payload[KEY_PLY]
        expected_ply = len(moves)
        if claimed_ply != expected_ply:
            return _err(
                ERR_INVALID_MOVE,
                "Ply mismatch: expected {}, got {}".format(
                    expected_ply, claimed_ply
                ),
            )
        try:
            board = _replay_board(moves)
            move = board.parse_uci(uci)
        except (ValueError, _chess.InvalidMoveError):
            return _err(ERR_INVALID_MOVE, "Could not parse '{}'".format(uci))

        if move not in board.legal_moves:
            return _err(ERR_INVALID_MOVE, "Illegal move: {}".format(uci))

        board.push(move)
        moves.append(uci)

        # Never trust claimed terminal state: recompute from the replayed
        # board and reject mismatches (mirrors lrgp-rs validate_move).
        terminal, reason = _detect_auto_terminal(board)
        winner = sender_hash if terminal == "win" else ""
        claim_error = _check_terminal_claims(payload, terminal, reason, sender_hash)
        if claim_error is not None:
            return _err(ERR_PROTOCOL_ERROR, claim_error)

        meta["winner"] = winner
        meta["terminal"] = terminal
        meta["reason"] = reason
        meta["draw_offered"] = False
        meta["draw_offered_by"] = ""
        meta["draw_offer_reason"] = ""
        meta["turn"] = (
            "" if terminal
            else meta["black"] if sender_hash == meta["white"] else meta["white"]
        )
        _refresh_derived(session, board, moves)

        if terminal:
            SessionStateMachine.apply_command(session, CMD_MOVE, terminal=True)
        else:
            SessionStateMachine.apply_command(session, CMD_MOVE)
        session.unread = 1
        self._save_session(session)

        emit = {"type": "move", "session_id": session_id, "app_id": self.app_id,
                "from": sender_hash, "uci": uci}
        if terminal:
            emit["terminal"] = terminal
            emit["reason"] = reason
            if winner:
                emit["winner"] = winner
        return {"session": session.to_dict(), "emit": emit, "error": None}

    def _handle_resign_in(self, session_id, sender_hash, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return _err(ERR_PROTOCOL_ERROR, "Unknown session")
        meta = session.metadata
        winner = meta.get("black") if sender_hash == meta.get("white") else meta.get("white")
        meta["winner"] = winner or ""
        meta["terminal"] = "win"
        meta["reason"] = R_RESIGN
        meta["draw_offered"] = False
        meta["draw_offered_by"] = ""
        meta["draw_offer_reason"] = ""
        meta["turn"] = ""
        SessionStateMachine.apply_command(session, CMD_RESIGN, terminal=True)
        session.unread = 1
        self._save_session(session)
        return {"session": session.to_dict(), "emit": {
            "type": "resign", "session_id": session_id,
            "app_id": self.app_id, "from": sender_hash, "winner": meta["winner"],
        }, "error": None}

    def _handle_draw_offer_in(self, session_id, payload, sender_hash, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return _err(ERR_PROTOCOL_ERROR, "Unknown session")
        meta = session.metadata
        reason = _claim_str(payload or {}, KEY_REASON)

        # draw_offer with a valid `r` is a FIDE claim (threefold / 50-move):
        # verified locally, a valid claim ends the game without acceptance
        # (canonical per SPEC; mirrors lrgp-rs). Invalid claims degrade to a
        # plain draw offer.
        if reason in (R_THREEFOLD, R_FIFTY_MOVE) and self._claim_is_valid(meta, reason):
            meta["terminal"] = "draw"
            meta["reason"] = reason
            meta["draw_offered"] = False
            meta["draw_offered_by"] = ""
            meta["draw_offer_reason"] = ""
            meta["turn"] = ""
            SessionStateMachine.apply_command(session, CMD_DRAW_ACCEPT, terminal=True)
            session.unread = 1
            self._save_session(session)
            return {"session": session.to_dict(), "emit": {
                "type": "draw_claim", "session_id": session_id,
                "app_id": self.app_id, "from": sender_hash,
                "reason": reason,
            }, "error": None}

        if meta.get("draw_offered"):
            return incoming_error(
                ERR_PROTOCOL_ERROR, "A draw offer is already outstanding",
                CMD_DRAW_OFFER, session,
            )
        meta["draw_offered"] = True
        meta["draw_offered_by"] = sender_hash
        meta["draw_offer_reason"] = reason
        SessionStateMachine.apply_command(session, CMD_DRAW_OFFER)
        session.unread = 1
        self._save_session(session)
        return {"session": session.to_dict(), "emit": {
            "type": "draw_offer", "session_id": session_id,
            "app_id": self.app_id, "from": sender_hash,
            "reason": meta["draw_offer_reason"],
        }, "error": None}

    @staticmethod
    def _claim_is_valid(meta, reason):
        try:
            board = _replay_board(meta.get("moves", []))
        except (ValueError, _chess.InvalidMoveError):
            return False
        return _claim_reason(board) == reason

    def _handle_draw_accept_in(self, session_id, sender_hash, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return _err(ERR_PROTOCOL_ERROR, "Unknown session")
        meta = session.metadata
        offerer = meta.get("draw_offered_by", "")
        if not meta.get("draw_offered") or not offerer:
            return incoming_error(
                ERR_PROTOCOL_ERROR, "No draw offer is outstanding",
                CMD_DRAW_ACCEPT, session,
            )
        if offerer == sender_hash:
            return incoming_error(
                ERR_PROTOCOL_ERROR, "Cannot answer your own draw offer",
                CMD_DRAW_ACCEPT, session,
            )
        meta["terminal"] = "draw"
        # An invalid FIDE claim degrades to a normal offer. Agreement, not
        # the rejected claim code, is therefore the terminal draw reason.
        meta["reason"] = R_AGREEMENT
        meta["draw_offered"] = False
        meta["draw_offered_by"] = ""
        meta["draw_offer_reason"] = ""
        meta["turn"] = ""
        SessionStateMachine.apply_command(session, CMD_DRAW_ACCEPT, terminal=True)
        session.unread = 1
        self._save_session(session)
        return {"session": session.to_dict(), "emit": {
            "type": "draw_accept", "session_id": session_id,
            "app_id": self.app_id, "from": sender_hash,
        }, "error": None}

    def _handle_draw_decline_in(self, session_id, sender_hash, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return _err(ERR_PROTOCOL_ERROR, "Unknown session")
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
        session.metadata["draw_offer_reason"] = ""
        SessionStateMachine.apply_command(session, CMD_DRAW_DECLINE)
        session.unread = 1
        self._save_session(session)
        return {"session": session.to_dict(), "emit": {
            "type": "draw_decline", "session_id": session_id,
            "app_id": self.app_id, "from": sender_hash,
        }, "error": None}

    # --- Outgoing handlers ---

    def _handle_challenge_out(self, session_id, identity_id):
        sid = session_id or _gen_session_id()
        existing = self._get_session(sid, identity_id)
        if existing is not None:
            return {}, "[LRGP Chess] Sent a challenge!"
        session = Session(
            session_id=sid, identity_id=identity_id,
            app_id=self.app_id, app_version=self.version,
            contact_hash="", initiator=identity_id,
            status=STATUS_PENDING,
            metadata=_initial_metadata("", "", ""),
        )
        self._save_session(session)
        return {}, "[LRGP Chess] Sent a challenge!"

    def _handle_accept_out(self, session_id, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return {}, "[LRGP Chess] Challenge accepted"
        challenger_white = _flip_responder_coin()
        white = session.contact_hash if challenger_white else identity_id
        black = identity_id if challenger_white else session.contact_hash
        meta = session.metadata
        meta["white"] = white
        meta["black"] = black
        meta["turn"] = white
        meta["first_turn"] = white
        meta["my_color"] = "w" if identity_id == white else "b"
        SessionStateMachine.apply_command(session, CMD_ACCEPT)
        self._save_session(session)
        return {KEY_WHITE: white}, "[LRGP Chess] Challenge accepted"

    def _handle_move_out(self, session_id, payload, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return payload, "[LRGP Chess] Move (no session)"
        meta = session.metadata
        if meta.get("turn") != identity_id:
            return payload, "[LRGP Chess] Not your turn"

        uci = payload.get(KEY_MOVE)
        if not isinstance(uci, str):
            return payload, "[LRGP Chess] No move"

        moves = list(meta.get("moves", []))
        try:
            board = _replay_board(moves)
            move = board.parse_uci(uci)
        except (ValueError, _chess.InvalidMoveError):
            return payload, "[LRGP Chess] Invalid move"

        if move not in board.legal_moves:
            return payload, "[LRGP Chess] Illegal move"

        ply = len(moves)
        board.push(move)
        moves.append(uci)
        terminal, reason = _detect_auto_terminal(board)
        winner = identity_id if terminal == "win" else ""

        meta["winner"] = winner
        meta["terminal"] = terminal
        meta["reason"] = reason
        meta["draw_offered"] = False
        meta["draw_offered_by"] = ""
        meta["draw_offer_reason"] = ""
        meta["turn"] = (
            "" if terminal
            else meta["black"] if identity_id == meta["white"] else meta["white"]
        )
        _refresh_derived(session, board, moves)
        SessionStateMachine.apply_command(session, CMD_MOVE, terminal=bool(terminal))
        self._save_session(session)

        wire = {KEY_MOVE: uci, KEY_PLY: ply, KEY_TERMINAL: terminal}
        if terminal:
            wire[KEY_REASON] = reason
        if terminal == "win":
            wire[KEY_WINNER] = winner
        return wire, "[LRGP Chess] {}".format(uci)

    def _handle_resign_out(self, session_id, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return {}, "[LRGP Chess] Resigned."
        meta = session.metadata
        winner = meta.get("black") if identity_id == meta.get("white") else meta.get("white")
        meta["winner"] = winner or ""
        meta["terminal"] = "win"
        meta["reason"] = R_RESIGN
        meta["draw_offered"] = False
        meta["draw_offered_by"] = ""
        meta["draw_offer_reason"] = ""
        meta["turn"] = ""
        SessionStateMachine.apply_command(session, CMD_RESIGN, terminal=True)
        self._save_session(session)
        return {}, "[LRGP Chess] Resigned."

    def _handle_draw_offer_out(self, session_id, payload, identity_id):
        reason = _claim_str(payload or {}, KEY_REASON)
        session = self._get_session(session_id, identity_id)
        if session is not None:
            meta = session.metadata
            # A valid claim pre-terminates locally so the claimant's state
            # reflects the draw immediately (mirrors lrgp-rs).
            if reason in (R_THREEFOLD, R_FIFTY_MOVE) and self._claim_is_valid(meta, reason):
                meta["terminal"] = "draw"
                meta["reason"] = reason
                meta["turn"] = ""
                meta["draw_offered"] = False
                meta["draw_offered_by"] = ""
                meta["draw_offer_reason"] = ""
                SessionStateMachine.apply_command(session, CMD_DRAW_ACCEPT, terminal=True)
            else:
                meta["draw_offered"] = True
                meta["draw_offered_by"] = identity_id
                meta["draw_offer_reason"] = reason
                SessionStateMachine.apply_command(session, CMD_DRAW_OFFER)
            self._save_session(session)
        wire = {KEY_REASON: reason} if reason else {}
        if reason == R_THREEFOLD:
            return wire, "[LRGP Chess] Claimed threefold repetition"
        if reason == R_FIFTY_MOVE:
            return wire, "[LRGP Chess] Claimed fifty-move rule"
        return wire, "[LRGP Chess] Offered a draw"

    def _handle_draw_accept_out(self, session_id, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return {}, "[LRGP Chess] Draw accepted"
        meta = session.metadata
        meta["terminal"] = "draw"
        meta["reason"] = R_AGREEMENT
        meta["draw_offered"] = False
        meta["draw_offered_by"] = ""
        meta["draw_offer_reason"] = ""
        meta["turn"] = ""
        SessionStateMachine.apply_command(session, CMD_DRAW_ACCEPT, terminal=True)
        self._save_session(session)
        return {}, "[LRGP Chess] Draw accepted"

    # --- Validation helper ---

    @staticmethod
    def _incoming_payload_error(command, payload):
        empty_commands = {
            CMD_CHALLENGE, CMD_DECLINE, CMD_RESIGN,
            CMD_DRAW_ACCEPT, CMD_DRAW_DECLINE,
        }
        if command in empty_commands:
            return None if payload == {} else "{} payload must be empty".format(command)
        if command == CMD_ACCEPT:
            if set(payload) != {KEY_WHITE} or not isinstance(payload.get(KEY_WHITE), str):
                return "accept payload must contain exactly string w"
        elif command == CMD_DRAW_OFFER:
            if payload == {}:
                return None
            if (set(payload) != {KEY_REASON}
                    or payload.get(KEY_REASON) not in (R_THREEFOLD, R_FIFTY_MOVE)):
                return "draw_offer payload must be empty or contain a valid claim r"
        elif command == CMD_MOVE:
            terminal = payload.get(KEY_TERMINAL)
            expected = {KEY_MOVE, KEY_PLY, KEY_TERMINAL}
            if terminal in ("win", "draw"):
                expected.add(KEY_REASON)
            if terminal == "win":
                expected.add(KEY_WINNER)
            if set(payload) != expected:
                return "move payload has non-canonical keys"
            if (not isinstance(payload.get(KEY_MOVE), str)
                    or isinstance(payload.get(KEY_PLY), bool)
                    or not isinstance(payload.get(KEY_PLY), int)
                    or terminal not in ("", "win", "draw")):
                return "move payload has invalid value types"
            if terminal and not isinstance(payload.get(KEY_REASON), str):
                return "terminal move reason must be a string"
            if terminal == "win" and not isinstance(payload.get(KEY_WINNER), str):
                return "winning move winner must be a string"
        return None

    @staticmethod
    def _outgoing_payload_error(command, payload):
        if command == CMD_MOVE:
            if set(payload) != {KEY_MOVE} or not isinstance(payload.get(KEY_MOVE), str):
                return "local move intent must contain exactly string m"
            return None
        if command == CMD_DRAW_OFFER:
            if payload == {}:
                return None
            if (set(payload) == {KEY_REASON}
                    and payload.get(KEY_REASON) in (R_THREEFOLD, R_FIFTY_MOVE)):
                return None
            return "local draw_offer must be empty or contain a valid claim r"
        if command != CMD_ERROR and payload != {}:
            return "{} local payload must be empty".format(command)
        return None

    def _validate_local_move(self, session, payload, sender_hash):
        if session.status != STATUS_ACTIVE:
            return False, "Session is not active (status={})".format(session.status)
        meta = session.metadata
        turn = meta.get("turn", "")
        if not turn:
            return False, "Turn is required before moves"
        if turn != sender_hash:
            return False, "Not your turn"
        uci = payload.get(KEY_MOVE)
        if not isinstance(uci, str):
            return False, "Missing move"
        try:
            board = _replay_board(meta.get("moves", []))
            move = board.parse_uci(uci)
        except (ValueError, _chess.InvalidMoveError):
            return False, "Invalid UCI"
        if move not in board.legal_moves:
            return False, "Illegal move"
        return True, None

    def _validate_move(self, session, payload, sender_hash):
        meta = session.metadata
        if meta.get("turn") != sender_hash:
            return False, "Not your turn"
        uci = payload.get(KEY_MOVE)
        if not isinstance(uci, str):
            return False, "Missing move"

        ply = payload.get(KEY_PLY, 0)
        if isinstance(ply, bool) or not isinstance(ply, int):
            ply = 0
        expected_ply = len(meta.get("moves", []))
        if ply != expected_ply:
            return False, "Ply mismatch: expected {}, got {}".format(expected_ply, ply)

        try:
            board = _replay_board(meta.get("moves", []))
            move = board.parse_uci(uci)
        except (ValueError, _chess.InvalidMoveError):
            return False, "Invalid UCI"
        if move not in board.legal_moves:
            return False, "Illegal move"

        # Recompute terminal state and reject forged claims (mirrors
        # lrgp-rs validate_move).
        board.push(move)
        terminal, reason = _detect_auto_terminal(board)
        claim_error = _check_terminal_claims(payload, terminal, reason, sender_hash)
        if claim_error is not None:
            return False, claim_error
        return True, None


def _claim_str(payload, key):
    value = payload.get(key, "")
    return value if isinstance(value, str) else ""


def _claim_reason(board):
    """Valid FIDE draw-claim reason for the current position, or None.

    Mirrors lrgp-rs claim_reason: raw halfmove clock for the fifty-move
    rule, current position seen three times for repetition.
    """
    if board.halfmove_clock >= 100:
        return R_FIFTY_MOVE
    if board.is_repetition(3):
        return R_THREEFOLD
    return None


def _check_terminal_claims(payload, terminal, reason, sender_hash):
    """Compare claimed x/r/w against the recomputed terminal state.

    Returns an error message on mismatch, else None. Matches lrgp-rs:
    the claimed terminal must equal the computed one, a terminal move's
    claimed reason must match, and a win's claimed winner must be the
    sender.
    """
    claimed_terminal = _claim_str(payload, KEY_TERMINAL)
    claimed_reason = _claim_str(payload, KEY_REASON)
    claimed_winner = _claim_str(payload, KEY_WINNER)

    if claimed_terminal != terminal:
        return "Terminal mismatch: computed='{}' claimed='{}'".format(
            terminal, claimed_terminal)
    if terminal and claimed_reason != reason:
        return "Reason mismatch: computed='{}' claimed='{}'".format(
            reason, claimed_reason)
    if terminal == "win" and claimed_winner != sender_hash:
        return "Winner mismatch: computed='{}' claimed='{}'".format(
            sender_hash, claimed_winner)
    return None


def _err(code, msg):
    return {"session": None, "emit": None, "error": {"code": code, "msg": msg}}
