"""LRGP Rock Paper Scissors; mirrors lrgp-rs's ``apps/rps.rs``.

First to three rounds. Choices are simultaneous over a slow link by
commitment: the leader sends a SHA-256 hash of its choice and a random salt
(``h``), the follower answers in the open (``c``), and the leader then reveals
the choice and salt (``c``, ``k``) together with the next round's commitment.
SPEC.md Appendix I defines the protocol.

The LRGP lifecycle (challenge, coin flip, draw offers, resignation, expiry)
comes from the single-token engine; this module replaces its moves and
hydration checks.
"""

import copy
import hashlib
import os
from typing import Optional

from ..constants import (
    CMD_MOVE,
    ERR_INVALID_MOVE,
    STATUS_ACTIVE,
    STATUS_COMPLETED,
    STATUS_DECLINED,
    STATUS_EXPIRED,
    STATUS_PENDING,
)
from ..errors import InvalidEnvelope, incoming_error
from ..session import Session, SessionStateMachine
from ._placement import (
    BLACK,
    R_AGREEMENT,
    R_RESIGN,
    WHITE,
    PlacementApp,
    _clear_draw_offer,
    _other_player,
)

# Round wins that take the match.
WINS_NEEDED = 3
CHOICES = ("p", "r", "s")
R_ROUNDS = "rnd"

# Wire payload keys.
KEY_CHOICE = "c"  # "r" | "p" | "s"
KEY_SALT = "k"  # revealed salt
KEY_COMMIT = "h"  # commitment to the leader's next choice
KEY_PLY = "n"  # ply counter, 0-based
KEY_TERMINAL = "x"  # "" | "win"
KEY_WINNER = "w"  # winner identity hash

PHASE_COMMIT = "commit"
PHASE_CHOOSE = "choose"
PHASE_REVEAL = "reveal"

_NAMES = {"r": "rock", "p": "paper", "s": "scissors"}

_STRING_KEYS = (
    "my_color",
    "first_turn",
    "turn",
    "phase",
    "winner",
    "terminal",
    "terminal_reason",
    "my_choice",
    "my_salt",
)

# The move shapes a peer may send: commit, choose, reveal, and final reveal.
_SHAPES = (
    {KEY_COMMIT, KEY_PLY, KEY_TERMINAL},
    {KEY_CHOICE, KEY_PLY, KEY_TERMINAL},
    {KEY_CHOICE, KEY_SALT, KEY_COMMIT, KEY_PLY, KEY_TERMINAL},
)
_WIN_SHAPE = {KEY_CHOICE, KEY_SALT, KEY_PLY, KEY_TERMINAL, KEY_WINNER}


def commitment(choice, salt):
    """Lowercase hex SHA-256 of ``"<choice>:<salt>"``."""
    return hashlib.sha256("{}:{}".format(choice, salt).encode("utf-8")).hexdigest()


def _is_lower_hex(text, length):
    return (
        isinstance(text, str)
        and len(text) == length
        and all(c in "0123456789abcdef" for c in text)
    )


def is_salt(text):
    return _is_lower_hex(text, 32)


def is_commit(text):
    return _is_lower_hex(text, 64)


def is_choice(text):
    return isinstance(text, str) and text in CHOICES


def random_salt():
    return os.urandom(16).hex()


def beats(a, b):
    """``True`` if *a* beats *b*, ``None`` for a tie."""
    if a == b:
        return None
    return (a, b) in (("r", "s"), ("s", "p"), ("p", "r"))


def _score(score, mine, theirs):
    """*score* with one round added: the leader's, then the follower's."""
    result = beats(mine, theirs)
    if result is True:
        return [score[0] + 1, score[1]]
    if result is False:
        return [score[0], score[1] + 1]
    return list(score)


def _text(metadata, key):
    value = metadata.get(key, "")
    return value if isinstance(value, str) else ""


def _stored_plies(metadata):
    """The stored plies as dicts of strings (raises ``ValueError``)."""
    plies = metadata.get("plies")
    if not isinstance(plies, list):
        raise ValueError("plies must be an array")
    for ply in plies:
        if not isinstance(ply, dict):
            raise ValueError("a ply must be an object")
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in ply.items()):
            raise ValueError("ply values must be strings")
    return plies


class _Replayed:
    """Everything the stored plies imply."""

    __slots__ = ("turn", "phase", "rounds", "score", "winner", "pending")


def replay(session):
    """Replay the stored plies, checking every reveal against its commitment.

    Raises ``ValueError`` when the plies are inconsistent.
    """
    metadata = session.metadata
    leader = _text(metadata, "first_turn")
    follower = _other_player(session, leader)
    if not leader or not follower:
        raise ValueError("first_turn is not a bound participant")
    plies = _stored_plies(metadata)
    commit = None
    pending = None
    rounds = []
    score = [0, 0]
    winner = None

    for index, ply in enumerate(plies):
        if winner is not None:
            raise ValueError("a move follows the end of the match")
        keys = set(ply)
        if index == 0:
            if keys != {KEY_COMMIT} or not is_commit(ply[KEY_COMMIT]):
                raise ValueError("the first ply must be the leader's commitment")
            commit = ply[KEY_COMMIT]
        elif index % 2 == 1:
            if keys != {KEY_CHOICE} or not is_choice(ply[KEY_CHOICE]):
                raise ValueError("the follower must send exactly one choice")
            pending = ply[KEY_CHOICE]
        else:
            ends = keys == {KEY_CHOICE, KEY_SALT}
            if not ends and keys != {KEY_CHOICE, KEY_SALT, KEY_COMMIT}:
                raise ValueError("a reveal must carry c and k, and h unless the match ends")
            choice, salt = ply[KEY_CHOICE], ply[KEY_SALT]
            if not is_choice(choice) or not is_salt(salt):
                raise ValueError("a reveal carries an invalid choice or salt")
            if commit != commitment(choice, salt):
                raise ValueError("the revealed choice does not match its commitment")
            if pending is None:
                raise ValueError("a reveal must follow the follower's choice")
            score = _score(score, choice, pending)
            rounds.append([choice, pending])
            pending = None
            over = max(score) >= WINS_NEEDED
            if over != ends:
                if over:
                    raise ValueError("the match is over, so a reveal must not commit again")
                raise ValueError("the match goes on, so a reveal must commit to the next round")
            if over:
                winner = leader if score[0] >= WINS_NEEDED else follower
            else:
                if not is_commit(ply[KEY_COMMIT]):
                    raise ValueError("a commitment must be 64 lowercase hexadecimal characters")
                commit = ply[KEY_COMMIT]

    result = _Replayed()
    if winner is not None:
        result.turn, result.phase = "", ""
    elif not plies:
        result.turn, result.phase = leader, PHASE_COMMIT
    elif len(plies) % 2 == 1:
        result.turn, result.phase = follower, PHASE_CHOOSE
    else:
        result.turn, result.phase = leader, PHASE_REVEAL
    result.rounds = rounds
    result.score = score
    result.winner = winner
    result.pending = pending
    return result


def _is_ply(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class RpsApp(PlacementApp):
    """The built-in Rock Paper Scissors LRGP application."""

    app_id = "rps"
    display_name = "Rock Paper Scissors"
    icon = "rps"
    session_type = "round_based"

    def _initial_metadata(self):
        metadata = {key: "" for key in _STRING_KEYS}
        metadata.update(
            {
                "plies": [],
                "rounds": [],
                "legal_moves": [],
                "move_count": 0,
                "black_score": 0,
                "white_score": 0,
                "draw_offered": False,
                "draw_offered_by": "",
            }
        )
        return metadata

    @staticmethod
    def _refresh(session, replayed):
        """Store derived metadata from a replay."""
        metadata = session.metadata
        leading = metadata.get("first_turn") == session.identity_id
        metadata["turn"] = replayed.turn
        metadata["phase"] = replayed.phase
        metadata["move_count"] = len(metadata["plies"])
        metadata["rounds"] = [list(r) for r in replayed.rounds]
        metadata["black_score"], metadata["white_score"] = replayed.score
        # A reveal that ends the match takes no new choice.
        ends = (
            leading
            and replayed.phase == PHASE_REVEAL
            and replayed.pending is not None
            and max(_score(replayed.score, _text(metadata, "my_choice"), replayed.pending))
            >= WINS_NEEDED
        )
        metadata["legal_moves"] = [] if not replayed.phase or ends else list(CHOICES)
        if replayed.winner is not None:
            metadata["winner"] = replayed.winner
            metadata["terminal"] = "win"
            metadata["terminal_reason"] = R_ROUNDS

    def _refresh_restored(self, session):
        self._refresh(session, replay(session))

    def _start_game(self, session, black):
        session.metadata["first_turn"] = black
        session.metadata["my_color"] = BLACK if black == session.identity_id else WHITE
        self._refresh(session, replay(session))

    @staticmethod
    def _finish(metadata, terminal, reason, winner):
        PlacementApp._finish(metadata, terminal, reason, winner)
        metadata["phase"] = ""

    @staticmethod
    def _with_ply(session, ply):
        """A copy of *session* with one more ply, and its replay."""
        candidate = Session.from_dict(copy.deepcopy(session.to_dict()))
        metadata = candidate.metadata
        metadata["plies"] = list(_stored_plies(metadata)) + [ply]
        return candidate, replay(candidate)

    # --- Payload shapes ---

    def _incoming_payload_error(self, command, payload):
        if command != CMD_MOVE or not isinstance(payload, dict):
            return super()._incoming_payload_error(command, payload)
        if not _is_ply(payload.get(KEY_PLY)):
            return "move n must be an unsigned integer"
        if not all(isinstance(v, str) for k, v in payload.items() if k != KEY_PLY):
            return "move payload contains a value with the wrong type"
        terminal = payload.get(KEY_TERMINAL)
        keys = set(payload)
        if terminal == "win":
            shape_ok = keys == _WIN_SHAPE
        elif terminal == "":
            shape_ok = keys in _SHAPES
        else:
            shape_ok = False
        return None if shape_ok else "move payload has invalid keys"

    @staticmethod
    def _outgoing_payload_error(command, payload):
        if command == CMD_MOVE and isinstance(payload, dict):
            return None
        return PlacementApp._outgoing_payload_error(command, payload)

    # --- Moves ---

    def _prepare_move(self, session, payload, identity_id):
        """Check a local intent; return the session with it applied, its
        replay, and the wire payload (raises ``ValueError``).

        The intent is ``{c}``, or ``{}`` for a reveal that ends the match;
        ``k`` may be given with a choice that will be committed.
        """
        if session.status != STATUS_ACTIVE:
            raise ValueError("Session is not active ({})".format(session.status))
        metadata = session.metadata
        if metadata.get("turn", "") != identity_id:
            raise ValueError("Not your turn")
        try:
            replayed = replay(session)
        except ValueError as exc:
            raise ValueError("Local replay failed: {}".format(exc))
        if not isinstance(payload, dict):
            payload = {}
        choice = payload.get(KEY_CHOICE)
        salt = payload.get(KEY_SALT)
        if salt is None:
            salt = random_salt()
        elif not is_salt(salt):
            raise ValueError("k must be 32 lowercase hexadecimal characters")
        wants_choice = bool(metadata.get("legal_moves"))
        if wants_choice:
            shape_ok = set(payload) in ({KEY_CHOICE}, {KEY_CHOICE, KEY_SALT})
        else:
            shape_ok = payload == {}
        if not shape_ok or (choice is not None and not is_choice(choice)):
            if wants_choice:
                raise ValueError("Choose r, p, or s")
            raise ValueError("The match ends with this reveal: send an empty intent")
        ply = {}
        secret = None
        if replayed.phase == PHASE_COMMIT:
            ply[KEY_COMMIT] = commitment(choice, salt)
            secret = (choice, salt)
        elif replayed.phase == PHASE_CHOOSE:
            ply[KEY_CHOICE] = choice
        elif replayed.phase == PHASE_REVEAL:
            ply[KEY_CHOICE] = _text(metadata, "my_choice")
            ply[KEY_SALT] = _text(metadata, "my_salt")
            if wants_choice:
                ply[KEY_COMMIT] = commitment(choice, salt)
                secret = (choice, salt)
        else:
            raise ValueError("The match is over")
        candidate, after = self._with_ply(session, ply)
        if secret is not None:
            candidate.metadata["my_choice"], candidate.metadata["my_salt"] = secret
        wire = dict(ply)
        wire[KEY_PLY] = len(metadata["plies"])
        if after.winner is not None:
            wire[KEY_TERMINAL] = "win"
            wire[KEY_WINNER] = after.winner
        else:
            wire[KEY_TERMINAL] = ""
        return candidate, after, wire

    def _validate_local_move(self, session, payload, identity_id):
        if not session.contact_hash:
            return False, "Opponent unknown"
        try:
            self._prepare_move(session, payload, identity_id)
        except ValueError as exc:
            return False, str(exc)
        return True, None

    def _validate_move_details(self, session, payload, sender_hash):
        """Validate an inbound wire move; the third value is the session with
        it applied and the replay."""
        if session.status != STATUS_ACTIVE:
            return False, "Session is not active ({})".format(session.status), None
        turn = session.metadata.get("turn", "")
        if not turn:
            return False, "Turn is required before moves", None
        if turn != sender_hash:
            return False, "Not your turn", None
        try:
            expected = len(_stored_plies(session.metadata))
        except ValueError as exc:
            return False, str(exc), None
        ply = payload.get(KEY_PLY)
        if not _is_ply(ply) or ply != expected:
            return False, "Ply mismatch: expected {}, got {}".format(expected, ply), None
        stored = {
            k: v for k, v in payload.items() if k not in (KEY_PLY, KEY_TERMINAL, KEY_WINNER)
        }
        try:
            candidate, replayed = self._with_ply(session, stored)
        except ValueError as exc:
            return False, str(exc), None
        terminal = payload.get(KEY_TERMINAL, "")
        if replayed.winner is not None:
            consistent = terminal == "win" and payload.get(KEY_WINNER) == replayed.winner
        else:
            consistent = terminal == ""
        if not consistent:
            return False, "Terminal claim does not match the replayed match", None
        return True, None, (candidate, replayed)

    def _apply_move(self, candidate, replayed):
        self._refresh(candidate, replayed)
        _clear_draw_offer(candidate.metadata)
        SessionStateMachine.apply_command(
            candidate, CMD_MOVE, terminal=replayed.winner is not None
        )

    def _handle_move_in(self, session, payload, sender_hash):
        valid, message, applied = self._validate_move_details(session, payload, sender_hash)
        if not valid:
            return incoming_error(ERR_INVALID_MOVE, message, CMD_MOVE, session)
        candidate, replayed = applied
        self._apply_move(candidate, replayed)
        candidate.unread = 1
        self._save_session(candidate)
        result = self._incoming_event(candidate, "move", sender_hash)
        result["emit"]["payload"] = payload
        return result

    def _handle_move_out(self, session_id, payload, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return {}, "{} Session not found".format(self._prefix())
        try:
            candidate, replayed, wire = self._prepare_move(session, payload, identity_id)
        except ValueError as exc:
            return {}, "{} {}".format(self._prefix(), exc)
        self._apply_move(candidate, replayed)
        self._save_session(candidate)
        return wire, self.render_fallback(CMD_MOVE, wire)

    def render_fallback(self, command, payload):
        if command != CMD_MOVE:
            return super().render_fallback(command, payload)
        prefix = self._prefix()
        choice = payload.get(KEY_CHOICE)
        if not isinstance(choice, str):
            return "{} Chose in secret".format(prefix)
        name = _NAMES.get(choice, "scissors")
        if payload.get(KEY_TERMINAL) == "win":
            return "{} Showed {} (match over)".format(prefix, name)
        if KEY_SALT in payload:
            return "{} Showed {} and chose again".format(prefix, name)
        return "{} Chose {}".format(prefix, name)

    # --- Hydration ---

    def _validate_restored_session(self, session):
        metadata = session.metadata
        if not isinstance(metadata, dict):
            raise InvalidEnvelope("Restored metadata must be a map")
        for key in _STRING_KEYS:
            if not isinstance(metadata.get(key), str):
                raise InvalidEnvelope("Restored {} must be a string".format(key))
        try:
            plies = _stored_plies(metadata)
        except ValueError as exc:
            raise InvalidEnvelope("Restored {}".format(exc))

        if session.status in (STATUS_PENDING, STATUS_DECLINED):
            started = plies or any(
                metadata[key]
                for key in ("first_turn", "my_color", "turn", "terminal", "winner", "my_choice")
            )
            if started:
                raise InvalidEnvelope("Pending or declined session contains game progress")
        elif session.status in (STATUS_ACTIVE, STATUS_COMPLETED, STATUS_EXPIRED):
            leader = metadata["first_turn"]
            if not leader or leader not in (session.identity_id, session.contact_hash):
                raise InvalidEnvelope("Restored first_turn is not a bound participant")
            if metadata["my_color"] != (BLACK if leader == session.identity_id else WHITE):
                raise InvalidEnvelope("Restored my_color does not match first_turn")
            try:
                replayed = replay(session)
            except ValueError as exc:
                raise InvalidEnvelope("Restored match is invalid: {}".format(exc))
            move_count = metadata.get("move_count")
            if not _is_ply(move_count) or move_count != len(plies):
                raise InvalidEnvelope("Restored move_count does not match the plies")
            # The leader's own secret must match the commitment awaiting a reveal.
            if leader == session.identity_id and replayed.winner is None and plies:
                commit = next((p[KEY_COMMIT] for p in reversed(plies) if KEY_COMMIT in p), "")
                if commitment(metadata["my_choice"], metadata["my_salt"]) != commit:
                    raise InvalidEnvelope("Restored choice does not match the commitment")
            terminal = metadata["terminal"]
            reason = metadata["terminal_reason"]
            winner = metadata["winner"]
            turn = metadata["turn"]
            if session.status == STATUS_ACTIVE:
                consistent = (
                    replayed.winner is None
                    and turn == replayed.turn
                    and not terminal
                    and not reason
                    and not winner
                )
            elif session.status == STATUS_EXPIRED:
                consistent = (
                    replayed.winner is None
                    and not terminal
                    and not winner
                    and (not turn or turn == replayed.turn)
                )
            elif replayed.winner is not None:
                consistent = (
                    not turn and terminal == "win" and reason == R_ROUNDS and winner == replayed.winner
                )
            elif terminal == "win" and reason == R_RESIGN:
                consistent = not turn and winner in (session.identity_id, session.contact_hash)
            elif terminal == "draw" and reason == R_AGREEMENT:
                consistent = not turn and not winner
            else:
                consistent = False
            if not consistent:
                raise InvalidEnvelope("Restored terminal metadata does not match the match")
        else:
            raise InvalidEnvelope("Restored session has an unknown status")

        if metadata.get("draw_offered") is True:
            owner = metadata.get("draw_offered_by")
            if session.status != STATUS_ACTIVE or owner not in (
                session.identity_id,
                session.contact_hash,
            ):
                raise InvalidEnvelope("Restored draw offer has an invalid owner or status")


def force_coin(challenger_leads: Optional[bool]) -> None:
    """Pin the responder coin flip for tests (``True`` = challenger leads).

    ``None`` clears the pin. Process-global; callers must serialize.
    """
    RpsApp._forced_coin = challenger_leads


__all__ = [
    "CHOICES",
    "WINS_NEEDED",
    "RpsApp",
    "beats",
    "commitment",
    "force_coin",
    "random_salt",
    "replay",
]
