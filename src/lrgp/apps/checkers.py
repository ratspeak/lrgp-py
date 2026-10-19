"""LRGP Checkers — American checkers / English draughts, both-side validation.

Wire format mirrors lrgp-rs's checkers.rs exactly: moves travel as
space-separated square paths and each peer reconstructs the board by
replaying the path history from the starting position. Boards, legal moves,
and next-turn identities are local-only.

Wire payload keys::

    m   square path, e.g. "a3 b4" (simple) or "a5 c3 e1" (double jump)
    n   ply counter, 0-based (0 = Black's first move)
    x   terminal status: "" | "win"
    r   terminal reason on a winning move, or claim reason on draw_offer
    w   winner identity hash (move with x="win")
    b   Black-player (first mover) identity hash (accept)

Terminal reason codes (kept short to fit ``ENVELOPE_MAX_PACKED``)::

    np   opponent has no pieces left
    nm   opponent has pieces but no legal move
    3fr  threefold repetition (claimed)
    40m  forty-move rule (claimed)
    rsn  resignation
    agr  draw by agreement
"""

import os
from typing import Optional

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

# Starting board, 64 cells row-major from a8 (index 0) to h1 (index 63).
# ``_`` is empty, ``b``/``w`` are men and ``B``/``W`` are kings.
STARTING_BOARD = (
    "_b_b_b_bb_b_b_b__b_b_b_b________________w_w_w_w__w_w_w_ww_w_w_w_"
)

R_NO_PIECES = "np"
R_NO_MOVES = "nm"
R_THREEFOLD = "3fr"
R_FORTY_MOVE = "40m"
R_RESIGN = "rsn"
R_AGREEMENT = "agr"

CLAIM_REASONS = (R_THREEFOLD, R_FORTY_MOVE)

# Forty moves per side without a capture or a man move.
FORTY_MOVE_PLIES = 80
# Twelve captures is the most any single move can make.
MAX_PATH_SQUARES = 13

KEY_MOVE = "m"
KEY_PLY = "n"
KEY_TERMINAL = "x"
KEY_REASON = "r"
KEY_WINNER = "w"
KEY_BLACK = "b"

EMPTY = "_"
BLACK_MAN = "b"
BLACK_KING = "B"
WHITE_MAN = "w"
WHITE_KING = "W"

BLACK = "b"
WHITE = "w"

_FILES = "abcdefgh"
_RANKS = "12345678"
# (rank, file) steps. Black men move toward rank 1, White men toward rank 8.
_KING_DIRECTIONS = ((-1, -1), (-1, 1), (1, -1), (1, 1))
_MAN_DIRECTIONS = {
    BLACK_MAN: ((-1, -1), (-1, 1)),
    WHITE_MAN: ((1, -1), (1, 1)),
}

# Process-global coin pin for deterministic test vectors.
_FORCED_COIN: Optional[bool] = None


def force_coin(challenger_is_black: Optional[bool]) -> None:
    """Pin the coin flip for tests. ``None`` clears the pin.

    Process-global; callers must serialize their own access in parallel runs.
    """
    global _FORCED_COIN
    _FORCED_COIN = challenger_is_black


def _flip_responder_coin() -> bool:
    """``True`` if the challenger gets Black."""
    if _FORCED_COIN is not None:
        return _FORCED_COIN
    return os.urandom(1)[0] & 1 == 0


def _gen_session_id():
    return os.urandom(8).hex()


# --- Rules engine ---------------------------------------------------------


def _other_side(side):
    return WHITE if side == BLACK else BLACK


def _owns(side, piece):
    if side == BLACK:
        return piece in (BLACK_MAN, BLACK_KING)
    return piece in (WHITE_MAN, WHITE_KING)


def _is_king(piece):
    return piece in (BLACK_KING, WHITE_KING)


def _king(side):
    return BLACK_KING if side == BLACK else WHITE_KING


def _crown_rank(side):
    """Rank index (0 = rank 1) on which this side's men are crowned."""
    return 0 if side == BLACK else 7


def _directions(piece):
    return _MAN_DIRECTIONS.get(piece, _KING_DIRECTIONS)


def _step(square, direction):
    """Squares are ``rank * 8 + file`` with a1 = 0 and h8 = 63."""
    rank = square // 8 + direction[0]
    file = square % 8 + direction[1]
    if 0 <= rank < 8 and 0 <= file < 8:
        return rank * 8 + file
    return None


def _is_dark(square):
    """a1 is dark; only dark squares are playable."""
    return (square // 8 + square % 8) % 2 == 0


def square_name(square):
    return "{}{}".format(_FILES[square % 8], square // 8 + 1)


def parse_square(text):
    """Return the square index for a dark square name such as ``a3``."""
    if not isinstance(text, str) or len(text) != 2:
        return None
    if text[0] not in _FILES or text[1] not in _RANKS:
        return None
    square = _RANKS.index(text[1]) * 8 + _FILES.index(text[0])
    return square if _is_dark(square) else None


def parse_path(notation):
    """Parse a canonical path: 2..13 lowercase dark squares joined by single spaces."""
    if not isinstance(notation, str):
        return None
    squares = []
    for part in notation.split(" "):
        square = parse_square(part)
        if square is None:
            return None
        squares.append(square)
    if not 2 <= len(squares) <= MAX_PATH_SQUARES:
        return None
    return squares


def _notation(path):
    return " ".join(square_name(square) for square in path)


def _starting_board():
    board = [EMPTY] * 64
    for square in range(64):
        if not _is_dark(square):
            continue
        if square // 8 <= 2:
            board[square] = WHITE_MAN
        elif square // 8 >= 5:
            board[square] = BLACK_MAN
    return board


def _render(board):
    """Row-major from a8 down to h1, matching how the board is drawn."""
    return "".join(
        board[rank * 8 + file] for rank in range(7, -1, -1) for file in range(8)
    )


def _collect_jumps(board, piece, side, path, captured, out):
    """Depth-first jump search.

    Jumped pieces stay on the board until the move completes: they cannot be
    jumped twice and block landing. A man that reaches the crown rank is
    crowned and its move ends there.
    """
    origin = path[-1]
    extended = False
    for direction in _directions(piece):
        over = _step(origin, direction)
        if over is None:
            continue
        to = _step(over, direction)
        if to is None:
            continue
        if (
            not _owns(_other_side(side), board[over])
            or over in captured
            or board[to] != EMPTY
        ):
            continue
        extended = True
        path.append(to)
        captured.append(over)
        if not _is_king(piece) and to // 8 == _crown_rank(side):
            out.append((tuple(path), tuple(captured)))
        else:
            _collect_jumps(board, piece, side, path, captured, out)
        path.pop()
        captured.pop()
    if not extended and captured:
        out.append((tuple(path), tuple(captured)))


def _legal_moves(board, side):
    """Return ``(path, captured)`` moves; captures are mandatory and complete."""
    jumps = []
    scratch = list(board)
    for origin in range(64):
        piece = board[origin]
        if not _owns(side, piece):
            continue
        # Lift the piece so a king's circuit may pass through its origin.
        scratch[origin] = EMPTY
        _collect_jumps(scratch, piece, side, [origin], [], jumps)
        scratch[origin] = piece
    if jumps:
        return jumps

    steps = []
    for origin in range(64):
        piece = board[origin]
        if not _owns(side, piece):
            continue
        for direction in _directions(piece):
            to = _step(origin, direction)
            if to is not None and board[to] == EMPTY:
                steps.append(((origin, to), ()))
    return steps


class _Game:
    """A position plus the history needed for draw-claim verification."""

    def __init__(self, board=None, side=BLACK):
        self.board = list(board) if board is not None else _starting_board()
        self.side = side
        self.history = [(tuple(self.board), self.side)]
        # Plies since the last capture or man move.
        self.quiet_plies = 0

    @classmethod
    def replay(cls, moves):
        """Replay path history from the starting position (raises ValueError)."""
        if not isinstance(moves, (list, tuple)):
            raise ValueError("move history must be a list")
        game = cls()
        for notation in moves:
            game.play(notation)
        return game

    def legal_moves(self):
        return _legal_moves(self.board, self.side)

    def play(self, notation):
        if parse_path(notation) is None:
            raise ValueError("Invalid move notation: {}".format(notation))
        for path, captured in self.legal_moves():
            if _notation(path) == notation:
                break
        else:
            raise ValueError(
                "Move {} is not legal from current position".format(notation)
            )
        origin, destination = path[0], path[-1]
        piece = self.board[origin]
        man_moved = not _is_king(piece)
        self.board[origin] = EMPTY
        for square in captured:
            self.board[square] = EMPTY
        if man_moved and destination // 8 == _crown_rank(self.side):
            piece = _king(self.side)
        self.board[destination] = piece
        self.side = _other_side(self.side)
        self.quiet_plies = 0 if man_moved or captured else self.quiet_plies + 1
        self.history.append((tuple(self.board), self.side))
        return path, captured

    def claim_is_valid(self, reason):
        if reason == R_FORTY_MOVE:
            return self.quiet_plies >= FORTY_MOVE_PLIES
        if reason == R_THREEFOLD:
            current = (tuple(self.board), self.side)
            return sum(1 for seen in self.history if seen == current) >= 3
        return False

    def claim_reason(self):
        """Draw-claim eligibility for display. 40-move wins ties."""
        for reason in (R_FORTY_MOVE, R_THREEFOLD):
            if self.claim_is_valid(reason):
                return reason
        return None

    def terminal(self):
        """Return ``(terminal, reason)``; on ``"win"`` the last mover won."""
        if not any(_owns(self.side, piece) for piece in self.board):
            return "win", R_NO_PIECES
        if not self.legal_moves():
            return "win", R_NO_MOVES
        return "", ""


# --- Session helpers ------------------------------------------------------


def _other_player(session, player):
    if player == session.identity_id and session.contact_hash:
        return session.contact_hash
    if player == session.contact_hash and session.identity_id:
        return session.identity_id
    return ""


def _initial_metadata():
    return {
        "board": STARTING_BOARD,
        "moves": [],
        "my_color": "",
        "first_turn": "",
        "turn": "",
        "move_count": 0,
        "winner": "",
        "terminal": "",
        "terminal_reason": "",
        "draw_offered": False,
        "draw_offered_by": "",
        "draw_offer_reason": "",
        "last_move": "",
        "must_capture": False,
        "legal_moves": [],
    }


def _clear_draw_offer(metadata):
    metadata["draw_offered"] = False
    metadata["draw_offered_by"] = ""


def _refresh_derived(metadata, game):
    """Refresh board, legal_moves, must_capture, and draw_offer_reason."""
    metadata["board"] = _render(game.board)
    legal = game.legal_moves()
    metadata["must_capture"] = bool(legal and legal[0][1])
    metadata["legal_moves"] = [_notation(path) for path, _captured in legal]
    claim = game.claim_reason() if not game.terminal()[0] else None
    metadata["draw_offer_reason"] = claim or ""


def _start_game(session, black):
    """Assign colors and reset the board when a challenge is accepted."""
    metadata = session.metadata
    metadata["moves"] = []
    metadata["move_count"] = 0
    metadata["first_turn"] = black
    metadata["turn"] = black
    metadata["my_color"] = BLACK if black == session.identity_id else WHITE
    _refresh_derived(metadata, _Game())


def _finish(metadata, terminal, reason, winner):
    """Record a non-move game end (resignation, agreement, verified claim)."""
    metadata["terminal"] = terminal
    metadata["terminal_reason"] = reason
    metadata["winner"] = winner
    metadata["turn"] = ""
    metadata["legal_moves"] = []
    metadata["must_capture"] = False
    metadata["draw_offer_reason"] = ""
    _clear_draw_offer(metadata)


def _replayed(metadata):
    try:
        return _Game.replay(metadata.get("moves", []))
    except ValueError:
        return None


def _claim_is_valid(metadata, reason):
    game = _replayed(metadata)
    return game is not None and game.claim_is_valid(reason)


class CheckersApp(GameBase):
    """Built-in American checkers / English draughts."""

    app_id = "checkers"
    version = 1
    display_name = "Checkers"
    icon = "checkers"
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
        """Load a session; an expired game can no longer answer a draw offer."""
        session = super()._get_session(session_id, identity_id, now=now)
        if (
            session is not None
            and session.status == STATUS_EXPIRED
            and isinstance(session.metadata, dict)
            and (
                session.metadata.get("draw_offered")
                or session.metadata.get("draw_offered_by")
            )
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
                    result = self._handle_accept_in(session, payload, sender_hash)
                elif command == CMD_DECLINE:
                    result = self._handle_decline_in(session, sender_hash)
                elif command == CMD_MOVE:
                    result = self._handle_move_in(session, payload, sender_hash)
                elif command == CMD_RESIGN:
                    result = self._handle_resign_in(session, sender_hash)
                elif command == CMD_DRAW_OFFER:
                    result = self._handle_draw_offer_in(session, payload, sender_hash)
                elif command in (CMD_DRAW_ACCEPT, CMD_DRAW_DECLINE):
                    result = self._handle_draw_response_in(
                        session, command, sender_hash
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
            return self._handle_draw_offer_out(session_id, payload, identity_id)
        if command in (CMD_DRAW_ACCEPT, CMD_DRAW_DECLINE):
            return self._handle_draw_response_out(session_id, command, identity_id)
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
        if session.status == STATUS_EXPIRED:
            raise SessionExpired(session_id)
        self.authorize_session(session, sender_hash)
        if command == CMD_ACCEPT and payload.get(KEY_BLACK) not in (
            session.identity_id,
            session.contact_hash,
        ):
            return False, "Accept black player is not a bound participant"
        if command == CMD_MOVE:
            valid, message, _game = self._validate_move_details(
                session, payload, sender_hash
            )
            return valid, message
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
            # A verified claim ends the game even over an outstanding offer.
            reason = payload.get(KEY_REASON, "")
            if not (reason and _claim_is_valid(session.metadata, reason)):
                raise OutgoingActionError(
                    ERR_PROTOCOL_ERROR,
                    "A draw offer is already outstanding",
                    command,
                )
        if command in (CMD_DRAW_ACCEPT, CMD_DRAW_DECLINE):
            error = self._draw_response_error(session, identity_id)
            if error:
                raise OutgoingActionError(ERR_PROTOCOL_ERROR, error, command)
        return session

    def get_session_state(self, session_id, identity_id):
        session = self._get_session(session_id, identity_id)
        return session.to_dict() if session is not None else {}

    def render_fallback(self, command, payload):
        prefix = "[LRGP Checkers]"
        if command == CMD_CHALLENGE:
            return "{} Sent a challenge!".format(prefix)
        if command == CMD_ACCEPT:
            return "{} Challenge accepted".format(prefix)
        if command == CMD_DECLINE:
            return "{} Challenge declined".format(prefix)
        if command == CMD_MOVE:
            path = payload.get(KEY_MOVE, "")
            if payload.get(KEY_TERMINAL) == "win":
                detail = {
                    R_NO_PIECES: "no pieces left",
                    R_NO_MOVES: "no moves left",
                }.get(payload.get(KEY_REASON), "win")
                return "{} {} wins ({})".format(prefix, path, detail)
            return "{} {}".format(prefix, path)
        if command == CMD_RESIGN:
            return "{} Resigned.".format(prefix)
        if command == CMD_DRAW_OFFER:
            reason = payload.get(KEY_REASON, "")
            if reason == R_THREEFOLD:
                return "{} Claimed threefold repetition".format(prefix)
            if reason == R_FORTY_MOVE:
                return "{} Claimed forty-move rule".format(prefix)
            return "{} Offered a draw".format(prefix)
        if command == CMD_DRAW_ACCEPT:
            return "{} Draw accepted".format(prefix)
        if command == CMD_DRAW_DECLINE:
            return "{} Draw declined".format(prefix)
        if command == CMD_ERROR:
            return "{} Error: {}".format(prefix, payload.get("msg", "Unknown"))
        return "{} {}".format(prefix, command)

    def upsert_session(self, record, now=None):
        """Hydrate only a record whose stored history replays consistently."""
        session = record if isinstance(record, Session) else Session.from_dict(record)
        metadata = session.metadata
        if isinstance(metadata, dict):
            owner = metadata.get("draw_offered_by")
            # A legacy offer flag without an owner, or an offer on a game that
            # already expired, is not answerable.
            if (
                metadata.get("draw_offered") is not True
                or not isinstance(owner, str)
                or not owner
                or session.status == STATUS_EXPIRED
            ):
                _clear_draw_offer(metadata)
        self._validate_restored_session(session)
        if session.status == STATUS_ACTIVE:
            _refresh_derived(metadata, _Game.replay(metadata["moves"]))
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

        def text(key):
            value = metadata.get(key)
            if not isinstance(value, str):
                raise InvalidEnvelope("Restored {} must be a string".format(key))
            return value

        moves = metadata.get("moves")
        if not isinstance(moves, list):
            raise InvalidEnvelope("Restored moves must be an array")
        if not all(isinstance(move, str) for move in moves):
            raise InvalidEnvelope("Restored moves must be strings")
        try:
            game = _Game.replay(moves)
        except ValueError as exc:
            raise InvalidEnvelope(
                "Restored move history is invalid: {}".format(exc)
            )
        if text("board") != _render(game.board):
            raise InvalidEnvelope("Restored board does not match the move history")
        move_count = metadata.get("move_count")
        if (
            isinstance(move_count, bool)
            or not isinstance(move_count, int)
            or move_count != len(moves)
        ):
            raise InvalidEnvelope(
                "Restored move_count does not match the move history"
            )
        if text("last_move") != (moves[-1] if moves else ""):
            raise InvalidEnvelope(
                "Restored last_move does not match the move history"
            )

        first_turn = text("first_turn")
        my_color = text("my_color")
        turn = text("turn")
        terminal = text("terminal")
        reason = text("terminal_reason")
        winner = text("winner")

        if session.status in (STATUS_PENDING, STATUS_DECLINED):
            if moves or first_turn or my_color or turn or terminal or winner:
                raise InvalidEnvelope(
                    "Pending or declined session contains game progress"
                )
        elif session.status in (STATUS_ACTIVE, STATUS_COMPLETED, STATUS_EXPIRED):
            if first_turn not in (session.identity_id, session.contact_hash):
                raise InvalidEnvelope(
                    "Restored first_turn is not a bound participant"
                )
            expected_color = BLACK if first_turn == session.identity_id else WHITE
            if my_color != expected_color:
                raise InvalidEnvelope("Restored my_color does not match first_turn")
            second_turn = _other_player(session, first_turn)
            if not second_turn:
                raise InvalidEnvelope("Restored session has no bound opponent")
            players = {BLACK: first_turn, WHITE: second_turn}
            to_move = players[game.side]
            board_terminal, board_reason = game.terminal()

            if session.status == STATUS_ACTIVE:
                if (
                    turn != to_move
                    or terminal
                    or reason
                    or winner
                    or board_terminal
                ):
                    raise InvalidEnvelope(
                        "Active session metadata is terminal or out of turn"
                    )
            elif session.status == STATUS_EXPIRED:
                if (
                    terminal
                    or reason
                    or winner
                    or board_terminal
                    or (turn and turn != to_move)
                ):
                    raise InvalidEnvelope(
                        "Expired session contains terminal metadata"
                    )
            else:
                if turn:
                    raise InvalidEnvelope("Completed session must not have a turn")
                if terminal == "win" and reason in (R_NO_PIECES, R_NO_MOVES):
                    consistent = (
                        board_terminal == "win"
                        and board_reason == reason
                        and winner == players[_other_side(game.side)]
                    )
                elif terminal == "win" and reason == R_RESIGN:
                    consistent = not board_terminal and winner in (
                        session.identity_id,
                        session.contact_hash,
                    )
                elif terminal == "draw" and reason == R_AGREEMENT:
                    consistent = not board_terminal and not winner
                elif terminal == "draw" and reason in CLAIM_REASONS:
                    consistent = (
                        not board_terminal
                        and not winner
                        and game.claim_is_valid(reason)
                    )
                else:
                    consistent = False
                if not consistent:
                    raise InvalidEnvelope(
                        "Completed session terminal metadata does not match the board"
                    )
        else:
            raise InvalidEnvelope("Restored session has an unknown status")

        if metadata.get("draw_offered") is True:
            owner = metadata.get("draw_offered_by")
            if session.status != STATUS_ACTIVE or owner not in (
                session.identity_id,
                session.contact_hash,
            ):
                raise InvalidEnvelope(
                    "Restored draw offer has an invalid owner or status"
                )

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
            metadata=_initial_metadata(),
            unread=1,
        )
        self._save_session(session)
        return self._incoming_event(session, "challenge", sender_hash)

    def _handle_accept_in(self, session, payload, sender_hash):
        # Responder is authoritative on color assignment via the `b` field.
        black = payload.get(KEY_BLACK)
        if black not in (session.identity_id, sender_hash):
            return incoming_error(
                ERR_PROTOCOL_ERROR,
                "Accept black player must be one of the bound participants",
                CMD_ACCEPT,
                session,
            )
        SessionStateMachine.apply_command(session, CMD_ACCEPT)
        _start_game(session, black)
        session.unread = 1
        self._save_session(session)
        return self._incoming_event(session, "accept", sender_hash)

    def _handle_decline_in(self, session, sender_hash):
        SessionStateMachine.apply_command(session, CMD_DECLINE)
        if session.initiator == sender_hash:
            session.metadata["cancelled_by_initiator"] = True
        session.unread = 1
        self._save_session(session)
        return self._incoming_event(session, "decline", sender_hash)

    def _handle_move_in(self, session, payload, sender_hash):
        valid, message, game = self._validate_move_details(
            session, payload, sender_hash
        )
        if not valid:
            return incoming_error(ERR_INVALID_MOVE, message, CMD_MOVE, session)
        self._record_move(session, game, payload[KEY_MOVE], sender_hash)
        session.unread = 1
        self._save_session(session)
        result = self._incoming_event(session, "move", sender_hash)
        result["emit"]["payload"] = payload
        return result

    def _handle_resign_in(self, session, sender_hash):
        SessionStateMachine.apply_command(session, CMD_RESIGN)
        # Sender resigned; the local identity wins.
        _finish(session.metadata, "win", R_RESIGN, session.identity_id)
        session.unread = 1
        self._save_session(session)
        return self._incoming_event(session, "resign", sender_hash)

    def _handle_draw_offer_in(self, session, payload, sender_hash):
        # draw_offer with a valid `r` is a claim (threefold / 40-move):
        # verified locally, a valid claim ends the game without acceptance.
        # Invalid claims degrade to a plain draw offer.
        reason = payload.get(KEY_REASON, "")
        if reason and _claim_is_valid(session.metadata, reason):
            SessionStateMachine.apply_command(session, CMD_DRAW_ACCEPT)
            _finish(session.metadata, "draw", reason, "")
            session.unread = 1
            self._save_session(session)
            return self._incoming_event(session, "draw_claim", sender_hash)

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

    def _handle_draw_response_in(self, session, command, sender_hash):
        error = self._draw_response_error(session, sender_hash)
        if error:
            return incoming_error(ERR_PROTOCOL_ERROR, error, command, session)
        SessionStateMachine.apply_command(session, command)
        if command == CMD_DRAW_ACCEPT:
            _finish(session.metadata, "draw", R_AGREEMENT, "")
        else:
            _clear_draw_offer(session.metadata)
        session.unread = 1
        self._save_session(session)
        return self._incoming_event(session, command, sender_hash)

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
    def _draw_response_error(session, responder):
        offerer = session.metadata.get("draw_offered_by", "")
        if not session.metadata.get("draw_offered") or not offerer:
            return "No draw offer is outstanding"
        if offerer == responder:
            return "Cannot answer your own draw offer"
        return None

    # --- Outgoing handlers ---

    def _handle_challenge_out(self, session_id, identity_id):
        session_id = session_id or _gen_session_id()
        if self._get_session(session_id, identity_id) is None:
            session = Session(
                session_id=session_id,
                identity_id=identity_id,
                app_id=self.app_id,
                app_version=self.version,
                contact_hash="",
                initiator=identity_id,
                status=STATUS_PENDING,
                metadata=_initial_metadata(),
            )
            self._save_session(session)
        return {}, "[LRGP Checkers] Sent a challenge!"

    def _handle_accept_out(self, session_id, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return {}, "[LRGP Checkers] Session not found"
        SessionStateMachine.apply_command(session, CMD_ACCEPT)
        # Responder coin-flips Black; ships the Black-player hash as `b`.
        black = session.initiator if _flip_responder_coin() else identity_id
        _start_game(session, black)
        self._save_session(session)
        return {KEY_BLACK: black}, "[LRGP Checkers] Challenge accepted"

    def _handle_decline_out(self, session_id, identity_id):
        session = self._get_session(session_id, identity_id)
        cancelled = False
        if session is not None:
            if session.initiator == identity_id:
                session.metadata["cancelled_by_initiator"] = True
                cancelled = True
            SessionStateMachine.apply_command(session, CMD_DECLINE)
            self._save_session(session)
        if cancelled:
            return {}, "[LRGP Checkers] Challenge cancelled"
        return {}, "[LRGP Checkers] Challenge declined"

    def _handle_move_out(self, session_id, payload, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return {}, "[LRGP Checkers] Session not found"
        valid, message = self._validate_local_move(session, payload, identity_id)
        if not valid:
            return {}, "[LRGP Checkers] {}".format(message)

        notation = payload[KEY_MOVE]
        # SPEC: `n` is 0-based (0 = Black's first move) — count before play.
        ply = len(session.metadata["moves"])
        game = _Game.replay(session.metadata["moves"])
        game.play(notation)
        terminal, reason = game.terminal()

        wire = {KEY_MOVE: notation, KEY_PLY: ply, KEY_TERMINAL: terminal}
        if terminal == "win":
            wire[KEY_REASON] = reason
            wire[KEY_WINNER] = identity_id
        self._record_move(session, game, notation, identity_id)
        self._save_session(session)
        return wire, self.render_fallback(CMD_MOVE, wire)

    def _handle_resign_out(self, session_id, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is not None:
            SessionStateMachine.apply_command(session, CMD_RESIGN)
            _finish(session.metadata, "win", R_RESIGN, session.contact_hash)
            self._save_session(session)
        return {}, "[LRGP Checkers] Resigned."

    def _handle_draw_offer_out(self, session_id, payload, identity_id):
        reason = payload.get(KEY_REASON, "")
        session = self._get_session(session_id, identity_id)
        if session is not None:
            # A valid claim pre-terminates locally so the claimant's state
            # reflects the draw immediately.
            if reason and _claim_is_valid(session.metadata, reason):
                SessionStateMachine.apply_command(session, CMD_DRAW_ACCEPT)
                _finish(session.metadata, "draw", reason, "")
            else:
                SessionStateMachine.apply_command(session, CMD_DRAW_OFFER)
                session.metadata["draw_offered"] = True
                session.metadata["draw_offered_by"] = identity_id
            self._save_session(session)
        # A locally ineligible claim degrades to a plain offer but keeps `r`
        # on the wire so the receiver verifies eligibility independently.
        wire = {KEY_REASON: reason} if reason else {}
        return wire, self.render_fallback(CMD_DRAW_OFFER, wire)

    def _handle_draw_response_out(self, session_id, command, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is not None:
            SessionStateMachine.apply_command(session, command)
            if command == CMD_DRAW_ACCEPT:
                _finish(session.metadata, "draw", R_AGREEMENT, "")
            else:
                _clear_draw_offer(session.metadata)
            self._save_session(session)
        return {}, self.render_fallback(command, {})

    # --- Validation and move reconstruction ---

    @staticmethod
    def _incoming_payload_error(command, payload):
        if not isinstance(payload, dict):
            return "{} payload must be a map".format(command)
        empty_commands = {
            CMD_CHALLENGE,
            CMD_DECLINE,
            CMD_RESIGN,
            CMD_DRAW_ACCEPT,
            CMD_DRAW_DECLINE,
        }
        if command in empty_commands:
            return None if payload == {} else "{} payload must be empty".format(command)
        if command == CMD_ACCEPT:
            if set(payload) != {KEY_BLACK} or not isinstance(
                payload.get(KEY_BLACK), str
            ):
                return "accept payload must contain exactly string b"
        elif command == CMD_DRAW_OFFER:
            if payload != {} and (
                set(payload) != {KEY_REASON}
                or payload.get(KEY_REASON) not in CLAIM_REASONS
            ):
                return "draw_offer payload must be empty or contain a valid claim r"
        elif command == CMD_MOVE:
            terminal = payload.get(KEY_TERMINAL)
            if not isinstance(terminal, str) or terminal not in ("", "win"):
                return "move x must be '' or 'win'"
            expected = {KEY_MOVE, KEY_PLY, KEY_TERMINAL}
            if terminal == "win":
                expected |= {KEY_REASON, KEY_WINNER}
            if set(payload) != expected:
                return "move payload has non-canonical keys"
            ply = payload.get(KEY_PLY)
            if (
                parse_path(payload.get(KEY_MOVE)) is None
                or isinstance(ply, bool)
                or not isinstance(ply, int)
                or ply < 0
            ):
                return "move payload contains a value with the wrong type"
            if terminal == "win":
                if payload.get(KEY_REASON) not in (R_NO_PIECES, R_NO_MOVES):
                    return "winning move r must be np or nm"
                winner = payload.get(KEY_WINNER)
                if not isinstance(winner, str) or not winner:
                    return "winning move w must be a non-empty string"
        return None

    @staticmethod
    def _outgoing_payload_error(command, payload):
        if not isinstance(payload, dict):
            return "{} local payload must be a map".format(command)
        if command == CMD_MOVE:
            if set(payload) != {KEY_MOVE} or not isinstance(payload.get(KEY_MOVE), str):
                return "local move intent must contain exactly string m"
            return None
        if command == CMD_DRAW_OFFER:
            if payload == {}:
                return None
            if set(payload) == {KEY_REASON} and payload.get(KEY_REASON) in CLAIM_REASONS:
                return None
            return "local draw_offer must be empty or contain a valid claim r"
        if command != CMD_ERROR and payload != {}:
            return "{} local payload must be empty".format(command)
        return None

    def _validate_local_move(self, session, payload, identity_id):
        if session.status != STATUS_ACTIVE:
            return False, "Session is not active ({})".format(session.status)
        if session.metadata.get("turn", "") != identity_id:
            return False, "Not your turn"
        if not _other_player(session, identity_id):
            return False, "Opponent unknown"
        notation = payload.get(KEY_MOVE)
        try:
            game = _Game.replay(session.metadata.get("moves", []))
        except ValueError as exc:
            return False, "Local replay failed: {}".format(exc)
        try:
            game.play(notation)
        except ValueError as exc:
            return False, "Illegal move: {}".format(exc)
        return True, None

    def _validate_move_details(self, session, payload, sender_hash):
        """Validate an inbound wire move; return the game with it applied."""
        if session.status != STATUS_ACTIVE:
            return False, "Session is not active ({})".format(session.status), None
        # Empty turn on an active session is invalid state — fail closed.
        turn = session.metadata.get("turn", "")
        if not turn:
            return False, "Turn is required before moves", None
        if turn != sender_hash:
            return False, "Not your turn", None

        moves = session.metadata.get("moves", [])
        ply = payload.get(KEY_PLY)
        # SPEC: `n` is 0-based, so the next move's ply equals the count of
        # moves already applied.
        if isinstance(ply, bool) or not isinstance(ply, int) or ply != len(moves):
            return (
                False,
                "Ply mismatch: expected {}, got {}".format(len(moves), ply),
                None,
            )
        try:
            game = _Game.replay(moves)
        except ValueError as exc:
            return False, "Local replay failed: {}".format(exc), None
        try:
            game.play(payload.get(KEY_MOVE))
        except ValueError as exc:
            return False, str(exc), None

        terminal, reason = game.terminal()
        claimed_terminal = payload.get(KEY_TERMINAL, "")
        claimed_reason = payload.get(KEY_REASON, "")
        claimed_winner = payload.get(KEY_WINNER, "")
        if claimed_terminal != terminal:
            return (
                False,
                "Terminal mismatch: computed='{}' claimed='{}'".format(
                    terminal, claimed_terminal
                ),
                None,
            )
        if claimed_reason != reason:
            return (
                False,
                "Reason mismatch: computed='{}' claimed='{}'".format(
                    reason, claimed_reason
                ),
                None,
            )
        if terminal == "win" and claimed_winner != sender_hash:
            return (
                False,
                "Winner mismatch: computed='{}' claimed='{}'".format(
                    sender_hash, claimed_winner
                ),
                None,
            )
        if terminal != "win" and claimed_winner:
            return False, "Winner must be empty unless the move is a win", None
        if not terminal and not _other_player(session, sender_hash):
            return False, "Opponent unknown", None
        return True, None, game

    @staticmethod
    def _record_move(session, game, notation, mover):
        """Record a move already applied to *game* by *mover*."""
        terminal, reason = game.terminal()
        metadata = session.metadata
        metadata["moves"] = list(metadata.get("moves", [])) + [notation]
        metadata["move_count"] = len(metadata["moves"])
        metadata["turn"] = "" if terminal else _other_player(session, mover)
        metadata["last_move"] = notation
        metadata["terminal"] = terminal
        metadata["terminal_reason"] = reason
        metadata["winner"] = mover if terminal == "win" else ""
        _clear_draw_offer(metadata)
        _refresh_derived(metadata, game)
        SessionStateMachine.apply_command(session, CMD_MOVE, terminal=bool(terminal))


__all__ = [
    "CheckersApp",
    "MAX_PATH_SQUARES",
    "STARTING_BOARD",
    "force_coin",
    "parse_path",
    "parse_square",
    "square_name",
]
