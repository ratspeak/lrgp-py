"""Shared engine for the single-token turn games: the stone-placement games
(Hex, Gomoku, Reversi), Dots and Boxes, and Mancala.

Mirrors lrgp-rs's ``apps/placement.rs``. Every move is one token, such as a
square (``h8``), a line (``c3h``), or a pit (``d``). The wire carries
``{m, n, x}`` plus ``w`` on a win and never a board; both peers replay the
move history from the starting position. A game supplies only a rules class,
including who moves next, so extra turns need no message; this module owns
the LRGP lifecycle. SPEC.md Appendices E and F are normative.

Wire payload keys::

    m   move token, e.g. "h8", "swap", "c3h", or "d"
    n   ply counter, 0-based (0 = Black's first move)
    x   terminal status: "" | "win" | "draw"
    w   winner identity hash (move with x="win")
    b   Black-player (first mover) identity hash (accept)

A rules class provides ``LONGEST_MOVE`` (for tests), ``CAN_DRAW``, ``WIN_REASON``,
``DRAW_REASON`` and the static methods ``start``, ``side_to_move``,
``is_canonical``, ``legal_moves``, ``play`` (raising ``ValueError``),
``outcome`` (``("", None)``, ``("win", side)`` or ``("draw", None)``),
``render``, and ``extra_metadata``. A game with more than one win or draw
reason also provides ``reason``, the reason for a finished position.
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

R_RESIGN = "rsn"
R_AGREEMENT = "agr"

KEY_MOVE = "m"
KEY_PLY = "n"
KEY_TERMINAL = "x"
KEY_WINNER = "w"
KEY_BLACK = "b"

EMPTY = "_"
# Side codes double as the stone characters on the rendered board.
BLACK = "b"
WHITE = "w"

ONGOING = ("", None)
DRAW = ("draw", None)

_FILES = "abcdefghijklmnopqrstuvwxyz"
_DIGITS = "0123456789"


def other_side(side):
    return WHITE if side == BLACK else BLACK


def square_name(index, size):
    """Squares are ``rank * size + file`` with a1 = 0."""
    return "{}{}".format(_FILES[index % size], index // size + 1)


def parse_square(text, size):
    """Canonical square: lowercase file, then a decimal rank with no leading zero."""
    if not isinstance(text, str) or not 2 <= len(text) <= 3:
        return None
    file = _FILES.find(text[0])
    rank = text[1:]
    if not 0 <= file < size or rank[0] == "0" or any(c not in _DIGITS for c in rank):
        return None
    rank = int(rank)
    if not 1 <= rank <= size:
        return None
    return (rank - 1) * size + file


def render_grid(cells, size):
    """Row-major from the highest rank down, so rank 1 is the last row."""
    return "".join(
        cells[rank * size + file] for rank in range(size - 1, -1, -1) for file in range(size)
    )


def terminal_reason(rules, position):
    """The reason code for a finished *position*, or ``""`` while it is on."""
    if hasattr(rules, "reason"):
        return rules.reason(position)
    terminal = rules.outcome(position)[0]
    if terminal == "win":
        return rules.WIN_REASON
    return rules.DRAW_REASON if terminal == "draw" else ""


def play_checked(rules, position, notation):
    """Apply one move, rejecting moves after the game ended and bad spellings."""
    if rules.outcome(position)[0]:
        raise ValueError("The game is already over")
    if not rules.is_canonical(notation):
        raise ValueError("Invalid move notation: {}".format(notation))
    rules.play(position, notation)


def replay(rules, moves):
    """Replay move history from the starting position (raises ValueError)."""
    if not isinstance(moves, (list, tuple)):
        raise ValueError("move history must be a list")
    position = rules.start()
    for notation in moves:
        play_checked(rules, position, notation)
    return position


def _gen_session_id():
    return os.urandom(8).hex()


def _other_player(session, player):
    if player == session.identity_id and session.contact_hash:
        return session.contact_hash
    if player == session.contact_hash and session.identity_id:
        return session.identity_id
    return ""


def _player(session, side):
    """Identity playing *side*, derived from the stored Black player."""
    black = session.metadata.get("first_turn", "")
    if not black:
        return ""
    return black if side == BLACK else _other_player(session, black)


def _clear_draw_offer(metadata):
    metadata["draw_offered"] = False
    metadata["draw_offered_by"] = ""


class PlacementApp(GameBase):
    """LRGP lifecycle for one stone-placement game; subclasses set ``rules``."""

    app_id = ""  # Abstract: router discovery skips classes without an app id.
    rules = None
    version = 1
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

    # Set by each game module's ``force_coin`` for deterministic tests.
    _forced_coin = None

    def _prefix(self):
        return "[LRGP {}]".format(self.display_name)

    def _flip_responder_coin(self):
        """``True`` if the challenger gets Black."""
        if self._forced_coin is not None:
            return self._forced_coin
        return os.urandom(1)[0] & 1 == 0

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

    def _initial_metadata(self):
        start = self.rules.start()
        metadata = {
            "board": self.rules.render(start),
            "moves": [],
            "my_color": "",
            "first_turn": "",
            "turn": "",
            "move_count": 0,
            "last_move": "",
            "legal_moves": [],
            "winner": "",
            "terminal": "",
            "terminal_reason": "",
            "draw_offered": False,
            "draw_offered_by": "",
        }
        self.rules.extra_metadata(start, metadata)
        return metadata

    def _refresh_derived(self, metadata, position):
        """Refresh board, legal_moves, and the game's extra metadata."""
        metadata["board"] = self.rules.render(position)
        metadata["legal_moves"] = self.rules.legal_moves(position)
        self.rules.extra_metadata(position, metadata)

    def _refresh_restored(self, session):
        """Re-derive an active restored session's metadata from its history."""
        self._refresh_derived(session.metadata, replay(self.rules, session.metadata["moves"]))

    def _start_game(self, session, black):
        """Assign colors and reset the board when a challenge is accepted."""
        metadata = session.metadata
        metadata["moves"] = []
        metadata["move_count"] = 0
        metadata["first_turn"] = black
        metadata["turn"] = black
        metadata["my_color"] = BLACK if black == session.identity_id else WHITE
        self._refresh_derived(metadata, self.rules.start())

    @staticmethod
    def _finish(metadata, terminal, reason, winner):
        """Record a non-move game end (resignation or agreement)."""
        metadata["terminal"] = terminal
        metadata["terminal_reason"] = reason
        metadata["winner"] = winner
        metadata["turn"] = ""
        metadata["legal_moves"] = []
        _clear_draw_offer(metadata)

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
                result = self._handle_challenge_in(
                    session_id, payload, sender_hash, identity_id
                )
            else:
                session = self.require_live_session(session_id, identity_id)
                if command == CMD_ACCEPT:
                    result = self._handle_accept_in(session, payload, sender_hash)
                elif command == CMD_MOVE:
                    result = self._handle_move_in(session, payload, sender_hash)
                elif command in (
                    CMD_DECLINE,
                    CMD_RESIGN,
                    CMD_DRAW_OFFER,
                    CMD_DRAW_ACCEPT,
                    CMD_DRAW_DECLINE,
                ):
                    result = self._handle_simple_in(session, command, sender_hash)
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
            return self._handle_challenge_out(session_id, payload, identity_id)
        if command == CMD_ACCEPT:
            return self._handle_accept_out(session_id, payload, identity_id)
        if command == CMD_MOVE:
            return self._handle_move_out(session_id, payload, identity_id)
        if command in (
            CMD_DECLINE,
            CMD_RESIGN,
            CMD_DRAW_OFFER,
            CMD_DRAW_ACCEPT,
            CMD_DRAW_DECLINE,
        ):
            return self._handle_simple_out(session_id, command, identity_id)
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
            valid, message, _position = self._validate_move_details(
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
            raise OutgoingActionError(
                ERR_PROTOCOL_ERROR, "A draw offer is already outstanding", command
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
        prefix = self._prefix()
        if command == CMD_CHALLENGE:
            return "{} Sent a challenge!".format(prefix)
        if command == CMD_ACCEPT:
            return "{} Challenge accepted".format(prefix)
        if command == CMD_DECLINE:
            return "{} Challenge declined".format(prefix)
        if command == CMD_MOVE:
            notation = payload.get(KEY_MOVE, "")
            terminal = payload.get(KEY_TERMINAL)
            if terminal == "win":
                return "{} {} (game won)".format(prefix, notation)
            if terminal == "draw":
                return "{} {} (game drawn)".format(prefix, notation)
            return "{} {}".format(prefix, notation)
        if command == CMD_RESIGN:
            return "{} Resigned.".format(prefix)
        if command == CMD_DRAW_OFFER:
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
            self._refresh_restored(session)
        restored = super().upsert_session(session, now=now)
        if restored.status == STATUS_EXPIRED:
            _clear_draw_offer(restored.metadata)
            self._save_session(restored)
        return restored

    hydrate_session = upsert_session

    def _validate_restored_session(self, session):
        """Mirror the Rust built-in's semantic persistence boundary."""
        rules = self.rules
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
            position = replay(rules, moves)
        except ValueError as exc:
            raise InvalidEnvelope(
                "Restored move history is invalid: {}".format(exc)
            )
        if text("board") != rules.render(position):
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
            if not _other_player(session, first_turn):
                raise InvalidEnvelope("Restored session has no bound opponent")
            board_terminal, board_side = rules.outcome(position)
            to_move = _player(session, rules.side_to_move(position))

            if session.status == STATUS_ACTIVE:
                if turn != to_move or terminal or reason or winner or board_terminal:
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
                board_reason = terminal_reason(rules, position)
                if terminal == "win" and reason == board_reason and board_terminal == "win":
                    consistent = winner == _player(session, board_side)
                elif terminal == "draw" and reason == board_reason and board_terminal == "draw":
                    consistent = not winner
                elif terminal == "win" and reason == R_RESIGN and not board_terminal:
                    consistent = winner in (session.identity_id, session.contact_hash)
                elif terminal == "draw" and reason == R_AGREEMENT and not board_terminal:
                    consistent = not winner
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

    def _handle_challenge_in(self, session_id, payload, sender_hash, identity_id):
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
            metadata=self._initial_metadata(),
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
        self._start_game(session, black)
        session.unread = 1
        self._save_session(session)
        return self._incoming_event(session, "accept", sender_hash)

    def _handle_move_in(self, session, payload, sender_hash):
        valid, message, position = self._validate_move_details(
            session, payload, sender_hash
        )
        if not valid:
            return incoming_error(ERR_INVALID_MOVE, message, CMD_MOVE, session)
        self._record_move(session, position, payload[KEY_MOVE])
        session.unread = 1
        self._save_session(session)
        result = self._incoming_event(session, "move", sender_hash)
        result["emit"]["payload"] = payload
        return result

    def _handle_simple_in(self, session, command, sender_hash):
        """Decline, resign, draw offer, draw accept, and draw decline."""
        metadata = session.metadata
        if command == CMD_DRAW_OFFER and metadata.get("draw_offered"):
            return incoming_error(
                ERR_PROTOCOL_ERROR,
                "A draw offer is already outstanding",
                command,
                session,
            )
        if command in (CMD_DRAW_ACCEPT, CMD_DRAW_DECLINE):
            error = self._draw_response_error(session, sender_hash)
            if error:
                return incoming_error(ERR_PROTOCOL_ERROR, error, command, session)
        SessionStateMachine.apply_command(session, command)
        if command == CMD_DECLINE and session.initiator == sender_hash:
            metadata["cancelled_by_initiator"] = True
        elif command == CMD_RESIGN:
            # Sender resigned; the local identity wins.
            self._finish(metadata, "win", R_RESIGN, session.identity_id)
        elif command == CMD_DRAW_OFFER:
            metadata["draw_offered"] = True
            metadata["draw_offered_by"] = sender_hash
        elif command == CMD_DRAW_ACCEPT:
            self._finish(metadata, "draw", R_AGREEMENT, "")
        elif command == CMD_DRAW_DECLINE:
            _clear_draw_offer(metadata)
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

    def _handle_challenge_out(self, session_id, payload, identity_id):
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
                metadata=self._initial_metadata(),
            )
            self._save_session(session)
        return {}, self.render_fallback(CMD_CHALLENGE, {})

    def _handle_accept_out(self, session_id, payload, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return {}, "{} Session not found".format(self._prefix())
        SessionStateMachine.apply_command(session, CMD_ACCEPT)
        # Responder coin-flips Black; ships the Black-player hash as `b`.
        black = session.initiator if self._flip_responder_coin() else identity_id
        self._start_game(session, black)
        self._save_session(session)
        return {KEY_BLACK: black}, self.render_fallback(CMD_ACCEPT, {})

    def _handle_move_out(self, session_id, payload, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return {}, "{} Session not found".format(self._prefix())
        valid, message = self._validate_local_move(session, payload, identity_id)
        if not valid:
            return {}, "{} {}".format(self._prefix(), message)

        notation = payload[KEY_MOVE]
        # SPEC: `n` is 0-based (0 = Black's first move) — count before play.
        ply = len(session.metadata["moves"])
        position = replay(self.rules, session.metadata["moves"])
        play_checked(self.rules, position, notation)
        self._record_move(session, position, notation)
        self._save_session(session)

        terminal = session.metadata["terminal"]
        wire = {KEY_MOVE: notation, KEY_PLY: ply, KEY_TERMINAL: terminal}
        if terminal == "win":
            wire[KEY_WINNER] = session.metadata["winner"]
        return wire, self.render_fallback(CMD_MOVE, wire)

    def _handle_simple_out(self, session_id, command, identity_id):
        """Decline, resign, draw offer, draw accept, and draw decline."""
        session = self._get_session(session_id, identity_id)
        cancelled = False
        if session is not None:
            SessionStateMachine.apply_command(session, command)
            metadata = session.metadata
            if command == CMD_DECLINE and session.initiator == identity_id:
                metadata["cancelled_by_initiator"] = True
                cancelled = True
            elif command == CMD_RESIGN:
                self._finish(metadata, "win", R_RESIGN, session.contact_hash)
            elif command == CMD_DRAW_OFFER:
                metadata["draw_offered"] = True
                metadata["draw_offered_by"] = identity_id
            elif command == CMD_DRAW_ACCEPT:
                self._finish(metadata, "draw", R_AGREEMENT, "")
            elif command == CMD_DRAW_DECLINE:
                _clear_draw_offer(metadata)
            self._save_session(session)
        if cancelled:
            return {}, "{} Challenge cancelled".format(self._prefix())
        return {}, self.render_fallback(command, {})

    # --- Validation and move reconstruction ---

    def _incoming_payload_error(self, command, payload):
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
            if set(payload) != {KEY_BLACK} or not isinstance(
                payload.get(KEY_BLACK), str
            ):
                return "accept payload must contain exactly string b"
        elif command == CMD_MOVE:
            terminal = payload.get(KEY_TERMINAL)
            allowed = ("", "win", "draw") if self.rules.CAN_DRAW else ("", "win")
            if not isinstance(terminal, str) or terminal not in allowed:
                return "unsupported terminal marker"
            expected = {KEY_MOVE, KEY_PLY, KEY_TERMINAL}
            if terminal == "win":
                expected.add(KEY_WINNER)
            if set(payload) != expected:
                return "move payload has non-canonical keys"
            ply = payload.get(KEY_PLY)
            if (
                not self.rules.is_canonical(payload.get(KEY_MOVE))
                or isinstance(ply, bool)
                or not isinstance(ply, int)
                or ply < 0
            ):
                return "move payload contains a value with the wrong type"
            if terminal == "win":
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
        try:
            position = replay(self.rules, session.metadata.get("moves", []))
        except ValueError as exc:
            return False, "Local replay failed: {}".format(exc)
        try:
            play_checked(self.rules, position, payload.get(KEY_MOVE))
        except ValueError as exc:
            return False, "Illegal move: {}".format(exc)
        return True, None

    def _validate_move_details(self, session, payload, sender_hash):
        """Validate an inbound wire move; return the position with it applied."""
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
            position = replay(self.rules, moves)
        except ValueError as exc:
            return False, "Local replay failed: {}".format(exc), None
        try:
            play_checked(self.rules, position, payload.get(KEY_MOVE))
        except ValueError as exc:
            return False, str(exc), None

        terminal, side = self.rules.outcome(position)
        claimed_terminal = payload.get(KEY_TERMINAL, "")
        claimed_winner = payload.get(KEY_WINNER, "")
        if claimed_terminal != terminal:
            return (
                False,
                "Terminal mismatch: computed='{}' claimed='{}'".format(
                    terminal, claimed_terminal
                ),
                None,
            )
        if terminal == "win":
            winner = _player(session, side)
            if not winner:
                return False, "Opponent unknown", None
            if claimed_winner != winner:
                return (
                    False,
                    "Winner mismatch: computed='{}' claimed='{}'".format(
                        winner, claimed_winner
                    ),
                    None,
                )
        elif claimed_winner:
            return False, "Winner must be empty unless the move is a win", None
        if not terminal and not _player(session, self.rules.side_to_move(position)):
            return False, "Opponent unknown", None
        return True, None, position

    def _record_move(self, session, position, notation):
        """Record a move already applied to *position*."""
        terminal, side = self.rules.outcome(position)
        metadata = session.metadata
        metadata["moves"] = list(metadata.get("moves", [])) + [notation]
        metadata["move_count"] = len(metadata["moves"])
        metadata["turn"] = (
            "" if terminal else _player(session, self.rules.side_to_move(position))
        )
        metadata["last_move"] = notation
        metadata["terminal"] = terminal
        metadata["terminal_reason"] = terminal_reason(self.rules, position)
        metadata["winner"] = _player(session, side) if terminal == "win" else ""
        _clear_draw_offer(metadata)
        self._refresh_derived(metadata, position)
        SessionStateMachine.apply_command(session, CMD_MOVE, terminal=bool(terminal))
