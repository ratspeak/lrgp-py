"""LRGP Battleship; mirrors lrgp-rs's ``apps/battleship.rs``.

Battleship on a 10x10 board, the first LRGP game with hidden information.
Each player commits to a fleet when the game starts by sending only a SHA-256
hash of the fleet and a random salt (``h``). A shot (``s``) travels with the
answer to the opponent's previous shot (``a``): miss, hit, or sunk. The player
whose last ship sinks concedes by revealing the fleet and salt, and the winner
then reveals theirs, so each side checks the other's answers against the
commitment before the game counts. SPEC.md Appendix H defines the protocol.

The LRGP lifecycle (challenge, coin flip, draw offers, resignation, expiry)
comes from the single-token engine; this module replaces its payloads, moves,
and hydration checks.
"""

import copy
import hashlib
import os
import random
from typing import Optional

from ..constants import (
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
    _gen_session_id,
    _other_player,
    parse_square,
    render_grid,
    square_name,
)

SIZE = 10
CELLS = SIZE * SIZE
# Ship lengths in fleet order: carrier, battleship, cruiser, submarine, destroyer.
FLEET = (5, 4, 3, 3, 2)
FLEET_SQUARES = sum(FLEET)
R_SUNK = "snk"

# Wire payload keys.
KEY_SHOT = "s"  # square fired at
KEY_ANSWER = "a"  # "" | "m" | "h" | "s", for the previous shot
KEY_FLEET = "f"  # revealed fleet
KEY_SALT = "k"  # revealed salt
KEY_COMMIT = "h"  # fleet commitment
KEY_PLY = "n"  # ply counter, 0-based
KEY_TERMINAL = "x"  # "" | "win"
KEY_WINNER = "w"  # winner identity hash
KEY_BLACK = "b"  # first shooter (ACCEPT payload)

MISS = "m"
HIT = "h"
SUNK = "s"

PHASE_SHOOT = "shoot"
PHASE_CONCEDE = "concede"
PHASE_REVEAL = "reveal"

_SHOT = "shot"
_CONCEDE = "concede"
_FINAL = "final"

_STRING_KEYS = (
    "my_color",
    "first_turn",
    "turn",
    "last_move",
    "winner",
    "terminal",
    "terminal_reason",
    "phase",
    "my_fleet",
    "my_salt",
    "my_commit",
    "their_commit",
    "their_fleet",
    "their_salt",
    "my_board",
    "their_board",
)


def parse_fleet(text):
    """The squares of each ship in fleet order, or ``None``.

    A canonical fleet is five ``<square><h|v>`` tokens joined by single
    spaces, each ship running from its square toward higher files (``h``) or
    ranks (``v``), all on the board and none overlapping.
    """
    if not isinstance(text, str):
        return None
    tokens = text.split(" ")
    if len(tokens) != len(FLEET):
        return None
    taken = set()
    ships = []
    for token, length in zip(tokens, FLEET):
        start = parse_square(token[:-1], SIZE)
        if start is None:
            return None
        file, rank = start % SIZE, start // SIZE
        if token[-1] == "h" and file + length <= SIZE:
            step = 1
        elif token[-1] == "v" and rank + length <= SIZE:
            step = SIZE
        else:
            return None
        squares = [start + i * step for i in range(length)]
        if taken.intersection(squares):
            return None
        taken.update(squares)
        ships.append(squares)
    return ships


def commitment(fleet, salt):
    """Lowercase hex SHA-256 of ``"<fleet>:<salt>"``."""
    return hashlib.sha256("{}:{}".format(fleet, salt).encode("utf-8")).hexdigest()


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


def random_fleet():
    """A random canonical fleet."""
    rng = random.SystemRandom()
    while True:
        tokens = []
        for length in FLEET:
            horizontal = rng.random() < 0.5
            files, ranks = (SIZE - length + 1, SIZE) if horizontal else (SIZE, SIZE - length + 1)
            square = rng.randrange(ranks) * SIZE + rng.randrange(files)
            tokens.append(square_name(square, SIZE) + ("h" if horizontal else "v"))
        fleet = " ".join(tokens)
        if parse_fleet(fleet) is not None:
            return fleet


def random_salt():
    return os.urandom(16).hex()


class _Target:
    """A fleet being fired at: its ships and the squares hit so far."""

    def __init__(self, ships):
        self.ships = ships
        self.hit = set()

    def fire(self, square):
        """Record a shot and return the true answer to it."""
        self.hit.add(square)
        for ship in self.ships:
            if square in ship:
                return SUNK if all(s in self.hit for s in ship) else HIT
        return MISS

    def all_sunk(self):
        return all(s in self.hit for ship in self.ships for s in ship)

    def sunk(self):
        return sum(1 for ship in self.ships if all(s in self.hit for s in ship))

    def render(self, shots):
        """100 cells from the top rank down: ``s`` ship, ``x`` hit ship,
        ``o`` miss, ``_`` water."""
        cells = ["_"] * CELLS
        for ship in self.ships:
            for square in ship:
                cells[square] = "s"
        for square in shots:
            cells[square] = "x" if cells[square] == "s" else "o"
        return render_grid(cells, SIZE)


class _Replayed:
    """Everything the stored plies imply, from one side's point of view."""

    __slots__ = (
        "turn",
        "phase",
        "answer_due",
        "winner",
        "legal_moves",
        "last_move",
        "my_board",
        "their_board",
        "my_sunk",
        "their_sunk",
    )


def _text(metadata, key):
    value = metadata.get(key, "")
    return value if isinstance(value, str) else ""


def _fleets(session):
    metadata = session.metadata
    first = _text(metadata, "first_turn")
    second = _other_player(session, first)
    if not first or not second:
        raise ValueError("first_turn is not a bound participant")
    my_fleet = _text(metadata, "my_fleet")
    mine = parse_fleet(my_fleet)
    if mine is None:
        raise ValueError("my fleet is not a canonical fleet")
    if commitment(my_fleet, _text(metadata, "my_salt")) != _text(metadata, "my_commit"):
        raise ValueError("my fleet does not match my commitment")
    their_fleet = _text(metadata, "their_fleet")
    theirs = None
    if their_fleet:
        theirs = parse_fleet(their_fleet)
        if theirs is None:
            raise ValueError("the revealed fleet is not canonical")
        if commitment(their_fleet, _text(metadata, "their_salt")) != _text(
            metadata, "their_commit"
        ):
            raise ValueError("the revealed fleet does not match its commitment")
    return first, second, mine, theirs


def replay(session):
    """Replay the stored shots and answers, checking every answer that can
    be checked: all of the local player's, and the opponent's once revealed.

    Raises ``ValueError`` when the plies are inconsistent.
    """
    first, second, my_ships, their_ships = _fleets(session)
    me = session.identity_id
    shots = session.metadata.get("shots", [])
    answers = session.metadata.get("answers", [])
    if len(shots) != len(answers):
        raise ValueError("shots and answers differ in length")

    def mover(ply):
        return first if ply % 2 == 0 else second

    mine = _Target(my_ships)
    theirs = _Target(their_ships) if their_ships is not None else None
    fired = ([], [])  # by me, by them
    reported = []  # their answers to my shots, in order
    previous = None  # (kind, square)

    for ply, (shot, answer) in enumerate(zip(shots, answers)):
        by_me = mover(ply) == me
        if shot:
            square = parse_square(shot, SIZE)
            if square is None:
                raise ValueError("a stored shot is not a square")
            kind = (_SHOT, square)
        elif answer:
            kind = (_CONCEDE, None)
        else:
            kind = (_FINAL, None)
        if previous is not None and previous[0] == _FINAL:
            raise ValueError("a move follows the end of the game")

        # Every ply but the first and the final reveal answers the shot before it.
        if kind[0] == _FINAL:
            if previous is None or previous[0] != _CONCEDE:
                raise ValueError("only a concession can be answered by a reveal")
            if not by_me and theirs is None:
                raise ValueError("the winner's fleet is not revealed")
        elif kind[0] == _SHOT and previous is None and not answer:
            pass
        elif previous is not None and previous[0] == _SHOT:
            if answer not in (MISS, HIT, SUNK):
                raise ValueError("an answer must be m, h, or s")
            target = mine if by_me else theirs
            if target is not None and target.fire(previous[1]) != answer:
                raise ValueError(
                    "the answer to {} is false".format(square_name(previous[1], SIZE))
                )
            if not by_me:
                reported.append(answer)
        else:
            raise ValueError("a move is out of sequence")

        hits = sum(1 for a in reported if a != MISS)
        sunk = reported.count(SUNK)
        if kind[0] == _SHOT:
            if by_me and mine.all_sunk():
                raise ValueError("a sunk fleet must concede, not shoot")
            if not by_me and (
                sunk >= len(FLEET) or (theirs is not None and theirs.all_sunk())
            ):
                raise ValueError("a sunk fleet must concede, not shoot")
            done = fired[0 if by_me else 1]
            if kind[1] in done:
                raise ValueError(
                    "{} was already fired at".format(square_name(kind[1], SIZE))
                )
            done.append(kind[1])
        elif kind[0] == _CONCEDE:
            if answer != SUNK:
                raise ValueError("a concession must answer s")
            gone = mine.all_sunk() if by_me else theirs is not None and theirs.all_sunk()
            if not gone:
                raise ValueError("a concession must sink the last ship")
        if hits > FLEET_SQUARES or sunk > len(FLEET):
            raise ValueError("more hits or sinkings were reported than the fleet holds")
        previous = kind

    plies = len(shots)
    following = mover(plies)
    result = _Replayed()
    # The answer owed to the opponent's last shot, if it is our turn.
    result.answer_due = (
        mine.fire(previous[1])
        if previous is not None and previous[0] == _SHOT and following == me
        else ""
    )
    result.winner = None
    if previous is not None and previous[0] == _FINAL:
        result.turn, result.phase, result.winner = "", "", mover(plies - 1)
    elif previous is not None and previous[0] == _CONCEDE:
        result.turn, result.phase = following, PHASE_REVEAL
    elif following == me and mine.all_sunk():
        result.turn, result.phase = following, PHASE_CONCEDE
    else:
        result.turn, result.phase = following, PHASE_SHOOT
    if result.phase == PHASE_SHOOT:
        done = fired[0 if result.turn == me else 1]
        result.legal_moves = [
            square_name(square, SIZE) for square in range(CELLS) if square not in done
        ]
    else:
        result.legal_moves = []

    # Their board as far as the local player knows it.
    if theirs is not None:
        result.their_board = theirs.render(fired[0])
        result.their_sunk = theirs.sunk()
    else:
        cells = ["_"] * CELLS
        for index, square in enumerate(fired[0]):
            if index < len(reported):
                cells[square] = "o" if reported[index] == MISS else "x"
            else:
                cells[square] = "?"
        result.their_board = render_grid(cells, SIZE)
        result.their_sunk = reported.count(SUNK)
    result.my_board = _Target(my_ships).render(fired[1])
    result.my_sunk = mine.sunk()
    result.last_move = next((s for s in reversed(shots) if s), "")
    return result


def _chosen_fleet(payload):
    """Fleet and salt from a local intent: ``{}``, ``{f}``, or ``{f, k}``.

    Raises ``ValueError`` for anything else.
    """
    if not isinstance(payload, dict) or set(payload) not in (
        set(),
        {KEY_FLEET},
        {KEY_FLEET, KEY_SALT},
    ):
        raise ValueError("a fleet intent must be {}, {f}, or {f, k}")
    fleet = payload.get(KEY_FLEET)
    if fleet is None:
        fleet = random_fleet()
    elif parse_fleet(fleet) is None:
        raise ValueError("f must be a canonical fleet")
    salt = payload.get(KEY_SALT)
    if salt is None:
        salt = random_salt()
    elif not is_salt(salt):
        raise ValueError("k must be 32 lowercase hexadecimal characters")
    return fleet, salt


def _is_ply(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class BattleshipApp(PlacementApp):
    """The built-in Battleship LRGP application."""

    app_id = "battleship"
    display_name = "Battleship"
    icon = "battleship"

    def _initial_metadata(self):
        metadata = {key: "" for key in _STRING_KEYS}
        metadata.update(
            {
                "shots": [],
                "answers": [],
                "legal_moves": [],
                "move_count": 0,
                "my_sunk": 0,
                "their_sunk": 0,
                "draw_offered": False,
                "draw_offered_by": "",
                "my_board": "_" * CELLS,
                "their_board": "_" * CELLS,
            }
        )
        return metadata

    @staticmethod
    def _set_my_fleet(session, fleet, salt):
        metadata = session.metadata
        metadata["my_fleet"] = fleet
        metadata["my_salt"] = salt
        metadata["my_commit"] = commitment(fleet, salt)
        metadata["my_board"] = _Target(parse_fleet(fleet)).render([])

    @staticmethod
    def _refresh(session, replayed):
        """Store derived metadata from a replay."""
        metadata = session.metadata
        metadata["turn"] = replayed.turn
        metadata["phase"] = replayed.phase
        metadata["legal_moves"] = list(replayed.legal_moves)
        metadata["move_count"] = len(metadata["shots"])
        metadata["last_move"] = replayed.last_move
        metadata["my_board"] = replayed.my_board
        metadata["their_board"] = replayed.their_board
        metadata["my_sunk"] = replayed.my_sunk
        metadata["their_sunk"] = replayed.their_sunk
        if replayed.winner is not None:
            metadata["winner"] = replayed.winner
            metadata["terminal"] = "win"
            metadata["terminal_reason"] = R_SUNK

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
    def _with_ply(session, shot, answer, reveal=None):
        """A copy of *session* with one more ply, and its replay."""
        candidate = Session.from_dict(copy.deepcopy(session.to_dict()))
        metadata = candidate.metadata
        metadata["shots"] = list(metadata["shots"]) + [shot]
        metadata["answers"] = list(metadata["answers"]) + [answer]
        if reveal is not None:
            metadata["their_fleet"], metadata["their_salt"] = reveal
        return candidate, replay(candidate)

    # --- Payload shapes ---

    def _incoming_payload_error(self, command, payload):
        if not isinstance(payload, dict):
            return "{} payload must be a map".format(command)
        if command in (CMD_DECLINE, CMD_RESIGN, CMD_DRAW_OFFER, CMD_DRAW_ACCEPT, CMD_DRAW_DECLINE):
            return None if payload == {} else "{} payload must be empty".format(command)
        if command == CMD_CHALLENGE:
            if set(payload) != {KEY_COMMIT} or not is_commit(payload[KEY_COMMIT]):
                return "challenge payload must contain exactly a 64-character h"
        elif command == CMD_ACCEPT:
            if (
                set(payload) != {KEY_BLACK, KEY_COMMIT}
                or not is_commit(payload[KEY_COMMIT])
                or not isinstance(payload[KEY_BLACK], str)
            ):
                return "accept payload must contain exactly b and a 64-character h"
        elif command == CMD_MOVE:
            terminal = payload.get(KEY_TERMINAL)
            if terminal == "win":
                shape = {KEY_FLEET, KEY_SALT, KEY_PLY, KEY_TERMINAL, KEY_WINNER}
            elif terminal == "" and KEY_SHOT in payload:
                shape = {KEY_SHOT, KEY_ANSWER, KEY_PLY, KEY_TERMINAL}
            elif terminal == "":
                shape = {KEY_ANSWER, KEY_FLEET, KEY_SALT, KEY_PLY, KEY_TERMINAL}
            else:
                return "unsupported terminal marker"
            if set(payload) != shape:
                return "move payload has invalid keys"
            if not _is_ply(payload[KEY_PLY]) or not all(
                isinstance(payload[key], str) for key in shape if key != KEY_PLY
            ):
                return "move payload contains a value with the wrong type"
            if (
                (KEY_SHOT in payload and parse_square(payload[KEY_SHOT], SIZE) is None)
                or (KEY_FLEET in payload and parse_fleet(payload[KEY_FLEET]) is None)
                or (KEY_SALT in payload and not is_salt(payload[KEY_SALT]))
                or payload.get(KEY_WINNER) == ""
            ):
                return "move payload contains an invalid value"
        return None

    @staticmethod
    def _outgoing_payload_error(command, payload):
        if not isinstance(payload, dict):
            return "{} local payload must be a map".format(command)
        if command in (CMD_CHALLENGE, CMD_ACCEPT):
            try:
                _chosen_fleet(payload)
            except ValueError as exc:
                return str(exc)
            return None
        if command == CMD_MOVE:
            return None
        if payload != {}:
            return "{} local payload must be empty".format(command)
        return None

    # --- Moves ---

    def _prepare_move(self, session, payload, identity_id):
        """Check a local intent; return the session with it applied, its
        replay, and the wire payload (raises ``ValueError``)."""
        if session.status != STATUS_ACTIVE:
            raise ValueError("Session is not active ({})".format(session.status))
        if session.metadata.get("turn", "") != identity_id:
            raise ValueError("Not your turn")
        try:
            replayed = replay(session)
        except ValueError as exc:
            raise ValueError("Local replay failed: {}".format(exc))
        ply = len(session.metadata["shots"])
        metadata = session.metadata
        if replayed.phase == PHASE_SHOOT:
            shot = payload.get(KEY_SHOT) if isinstance(payload, dict) else None
            if set(payload) != {KEY_SHOT} or not isinstance(shot, str):
                raise ValueError("a shot must be exactly {s}")
            if parse_square(shot, SIZE) is None:
                raise ValueError("Invalid square: {}".format(shot))
            candidate, after = self._with_ply(session, shot, replayed.answer_due)
            wire = {KEY_SHOT: shot, KEY_ANSWER: replayed.answer_due, KEY_PLY: ply, KEY_TERMINAL: ""}
            return candidate, after, wire
        if replayed.phase in (PHASE_CONCEDE, PHASE_REVEAL):
            if payload != {}:
                raise ValueError("{} takes an empty intent".format(replayed.phase))
            wire = {KEY_FLEET: metadata["my_fleet"], KEY_SALT: metadata["my_salt"], KEY_PLY: ply}
            if replayed.phase == PHASE_CONCEDE:
                wire[KEY_ANSWER] = replayed.answer_due
                wire[KEY_TERMINAL] = ""
                candidate, after = self._with_ply(session, "", replayed.answer_due)
            else:
                wire[KEY_TERMINAL] = "win"
                wire[KEY_WINNER] = identity_id
                candidate, after = self._with_ply(session, "", "")
            return candidate, after, wire
        raise ValueError("The game is over")

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
        ply = payload.get(KEY_PLY)
        expected = len(session.metadata["shots"])
        if not _is_ply(ply) or ply != expected:
            return False, "Ply mismatch: expected {}, got {}".format(expected, ply), None
        phase = session.metadata.get("phase", "")
        terminal = payload.get(KEY_TERMINAL, "")
        if terminal == "win" and phase != PHASE_REVEAL:
            return False, "Nothing to reveal for", None
        if terminal == "" and phase == PHASE_REVEAL:
            return False, "The winner must reveal", None
        reveal = None
        if KEY_FLEET in payload:
            reveal = (payload[KEY_FLEET], payload.get(KEY_SALT, ""))
        try:
            candidate, replayed = self._with_ply(
                session, payload.get(KEY_SHOT, ""), payload.get(KEY_ANSWER, ""), reveal
            )
        except ValueError as exc:
            return False, str(exc), None
        if replayed.winner is not None:
            consistent = (
                terminal == "win"
                and replayed.winner == sender_hash
                and payload.get(KEY_WINNER) == sender_hash
            )
        else:
            consistent = terminal == ""
        if not consistent:
            return False, "Terminal claim does not match the replayed game", None
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

    # --- Challenge and accept ---

    def _handle_challenge_in(self, session_id, payload, sender_hash, identity_id):
        existing = self._get_session(session_id, identity_id)
        result = super()._handle_challenge_in(session_id, payload, sender_hash, identity_id)
        if existing is not None:
            return result
        session = self._get_session(session_id, identity_id)
        session.metadata["their_commit"] = payload[KEY_COMMIT]
        self._save_session(session)
        result["session"] = session.to_dict()
        return result

    def _handle_accept_in(self, session, payload, sender_hash):
        black = payload.get(KEY_BLACK)
        if black not in (session.identity_id, sender_hash):
            return incoming_error(
                ERR_PROTOCOL_ERROR,
                "Accept black player must be one of the bound participants",
                CMD_ACCEPT,
                session,
            )
        SessionStateMachine.apply_command(session, CMD_ACCEPT)
        session.metadata["their_commit"] = payload[KEY_COMMIT]
        self._start_game(session, black)
        session.unread = 1
        self._save_session(session)
        return self._incoming_event(session, "accept", sender_hash)

    def _handle_challenge_out(self, session_id, payload, identity_id):
        try:
            fleet, salt = _chosen_fleet(payload)
        except ValueError as exc:
            return {}, "{} {}".format(self._prefix(), exc)
        session_id = session_id or _gen_session_id()
        session = self._get_session(session_id, identity_id)
        if session is None:
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
            self._set_my_fleet(session, fleet, salt)
            self._save_session(session)
        commit = {KEY_COMMIT: session.metadata["my_commit"]}
        return commit, self.render_fallback(CMD_CHALLENGE, {})

    def _handle_accept_out(self, session_id, payload, identity_id):
        session = self._get_session(session_id, identity_id)
        if session is None:
            return {}, "{} Session not found".format(self._prefix())
        try:
            fleet, salt = _chosen_fleet(payload)
        except ValueError as exc:
            return {}, "{} {}".format(self._prefix(), exc)
        SessionStateMachine.apply_command(session, CMD_ACCEPT)
        black = session.initiator if self._flip_responder_coin() else identity_id
        self._set_my_fleet(session, fleet, salt)
        self._start_game(session, black)
        self._save_session(session)
        wire = {KEY_BLACK: black, KEY_COMMIT: session.metadata["my_commit"]}
        return wire, self.render_fallback(CMD_ACCEPT, {})

    def render_fallback(self, command, payload):
        if command != CMD_MOVE:
            return super().render_fallback(command, payload)
        prefix = self._prefix()
        if payload.get(KEY_SHOT) is not None:
            return "{} Fired at {}".format(prefix, payload[KEY_SHOT])
        if payload.get(KEY_TERMINAL) == "win":
            return "{} Fleet revealed (game won)".format(prefix)
        return "{} Fleet sunk".format(prefix)

    # --- Hydration ---

    def _validate_restored_session(self, session):
        metadata = session.metadata
        if not isinstance(metadata, dict):
            raise InvalidEnvelope("Restored metadata must be a map")
        for key in _STRING_KEYS:
            if not isinstance(metadata.get(key), str):
                raise InvalidEnvelope("Restored {} must be a string".format(key))
        for key in ("shots", "answers"):
            items = metadata.get(key)
            if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
                raise InvalidEnvelope("Restored {} must be an array of strings".format(key))
        my_fleet = metadata["my_fleet"]
        if my_fleet and (
            parse_fleet(my_fleet) is None
            or not is_salt(metadata["my_salt"])
            or commitment(my_fleet, metadata["my_salt"]) != metadata["my_commit"]
        ):
            raise InvalidEnvelope("Restored fleet does not match its commitment")
        their_commit = metadata["their_commit"]
        if their_commit and not is_commit(their_commit):
            raise InvalidEnvelope("Restored opponent commitment is malformed")

        if session.status in (STATUS_PENDING, STATUS_DECLINED):
            started = (
                metadata["shots"]
                or metadata["answers"]
                or any(
                    metadata[key]
                    for key in ("first_turn", "my_color", "turn", "terminal", "winner", "their_fleet")
                )
            )
            if started:
                raise InvalidEnvelope("Pending or declined session contains game progress")
            challenger = session.initiator == session.identity_id
            if session.status == STATUS_PENDING and not (my_fleet if challenger else their_commit):
                raise InvalidEnvelope("Pending session is missing its commitment")
        elif session.status in (STATUS_ACTIVE, STATUS_COMPLETED, STATUS_EXPIRED):
            first = metadata["first_turn"]
            if first not in (session.identity_id, session.contact_hash) or not first:
                raise InvalidEnvelope("Restored first_turn is not a bound participant")
            if metadata["my_color"] != (BLACK if first == session.identity_id else WHITE):
                raise InvalidEnvelope("Restored my_color does not match first_turn")
            if not their_commit:
                raise InvalidEnvelope("Restored session has no opponent commitment")
            try:
                replayed = replay(session)
            except ValueError as exc:
                raise InvalidEnvelope("Restored game is invalid: {}".format(exc))
            move_count = metadata.get("move_count")
            if (
                not _is_ply(move_count)
                or move_count != len(metadata["shots"])
                or metadata["last_move"] != replayed.last_move
                or metadata["my_board"] != replayed.my_board
                or metadata["their_board"] != replayed.their_board
            ):
                raise InvalidEnvelope("Restored metadata does not match the shots")
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
                    not turn and terminal == "win" and reason == R_SUNK and winner == replayed.winner
                )
            elif terminal == "win" and reason == R_RESIGN:
                consistent = not turn and winner in (session.identity_id, session.contact_hash)
            elif terminal == "draw" and reason == R_AGREEMENT:
                consistent = not turn and not winner
            else:
                consistent = False
            if not consistent:
                raise InvalidEnvelope("Restored terminal metadata does not match the game")
        else:
            raise InvalidEnvelope("Restored session has an unknown status")

        if metadata.get("draw_offered") is True:
            owner = metadata.get("draw_offered_by")
            if session.status != STATUS_ACTIVE or owner not in (
                session.identity_id,
                session.contact_hash,
            ):
                raise InvalidEnvelope("Restored draw offer has an invalid owner or status")


def force_coin(challenger_first: Optional[bool]) -> None:
    """Pin the responder coin flip for tests (``True`` = challenger shoots first).

    ``None`` clears the pin. Process-global; callers must serialize.
    """
    BattleshipApp._forced_coin = challenger_first


__all__ = [
    "CELLS",
    "FLEET",
    "BattleshipApp",
    "SIZE",
    "commitment",
    "force_coin",
    "parse_fleet",
    "random_fleet",
    "random_salt",
    "replay",
]
