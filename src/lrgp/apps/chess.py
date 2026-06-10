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
    CMD_ERROR, ERR_INVALID_MOVE, ERR_PROTOCOL_ERROR,
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


def _claim_reason(board) -> Optional[str]:
    """Return a 2-3 char reason if the side to move can claim a draw.

    The threefold-repetition and fifty-move rules are claim-based per FIDE,
    so a peer must explicitly send `draw_offer` with the reason. This helper
    surfaces the available claim, never auto-applies it.
    """
    if board.can_claim_threefold_repetition():
        return R_THREEFOLD
    if board.can_claim_fifty_moves():
        return R_FIFTY_MOVE
    return None


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
    ttl = {"pending": 86400, "active": 604800}  # 1 day pending, 7 days active

    def __init__(self):
        self._sessions = {}

    def _get_session(self, session_id, identity_id=""):
        return self._sessions.get((session_id, identity_id))

    def _save_session(self, session):
        self._sessions[(session.session_id, session.identity_id)] = session

    # --- GameBase required methods ---

    def handle_incoming(self, session_id, command, payload, sender_hash, identity_id):
        if command == CMD_CHALLENGE:
            return self._handle_challenge_in(session_id, sender_hash, identity_id)
        if command == CMD_ACCEPT:
            return self._handle_accept_in(session_id, payload, sender_hash, identity_id)
        if command == CMD_DECLINE:
            return self._handle_decline_in(session_id, sender_hash, identity_id)
        if command == CMD_MOVE:
            return self._handle_move_in(session_id, payload, sender_hash, identity_id)
        if command == CMD_RESIGN:
            return self._handle_resign_in(session_id, sender_hash, identity_id)
        if command == CMD_DRAW_OFFER:
            return self._handle_draw_offer_in(session_id, payload, sender_hash, identity_id)
        if command == CMD_DRAW_ACCEPT:
            return self._handle_draw_accept_in(session_id, sender_hash, identity_id)
        if command == CMD_DRAW_DECLINE:
            return self._handle_draw_decline_in(session_id, sender_hash, identity_id)
        if command == CMD_ERROR:
            return {"session": None, "emit": None, "error": payload}
        return {"session": None, "emit": None, "error": {
            "code": ERR_PROTOCOL_ERROR,
            "msg": "Unknown command: {}".format(command),
        }}

    def handle_outgoing(self, session_id, command, payload, identity_id):
        if command == CMD_CHALLENGE:
            return self._handle_challenge_out(session_id, identity_id)
        if command == CMD_ACCEPT:
            return self._handle_accept_out(session_id, identity_id)
        if command == CMD_DECLINE:
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
            return {}, "[LRGP Chess] Declined draw offer"
        return payload, "[LRGP Chess] {}".format(command)

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
        SessionStateMachine.apply_command(session, CMD_ACCEPT)
        meta = session.metadata
        white = payload.get(KEY_WHITE, sender_hash)
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
        meta["turn"] = meta["black"] if sender_hash == meta["white"] else meta["white"]
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
            meta["turn"] = ""
            SessionStateMachine.apply_command(session, CMD_DRAW_ACCEPT, terminal=True)
            session.unread = 1
            self._save_session(session)
            return {"session": session.to_dict(), "emit": {
                "type": "draw_claim", "session_id": session_id,
                "app_id": self.app_id, "from": sender_hash,
                "reason": reason,
            }, "error": None}

        meta["draw_offered"] = True
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
        meta["terminal"] = "draw"
        meta["reason"] = meta.get("draw_offer_reason") or R_AGREEMENT
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
        session.metadata["draw_offered"] = False
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
        meta["turn"] = meta["black"] if identity_id == meta["white"] else meta["white"]
        _refresh_derived(session, board, moves)
        SessionStateMachine.apply_command(session, CMD_MOVE, terminal=bool(terminal))
        self._save_session(session)

        wire = {
            KEY_MOVE: uci, KEY_PLY: ply,
            KEY_TERMINAL: terminal, KEY_REASON: reason, KEY_WINNER: winner,
        }
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
        SessionStateMachine.apply_command(session, CMD_RESIGN, terminal=True)
        self._save_session(session)
        return {KEY_WINNER: meta["winner"]}, "[LRGP Chess] Resigned."

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
                SessionStateMachine.apply_command(session, CMD_DRAW_ACCEPT, terminal=True)
            else:
                meta["draw_offered"] = True
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
        meta["reason"] = meta.get("draw_offer_reason") or R_AGREEMENT
        SessionStateMachine.apply_command(session, CMD_DRAW_ACCEPT, terminal=True)
        self._save_session(session)
        return {}, "[LRGP Chess] Draw accepted"

    # --- Validation helper ---

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
