"""LRGP game session state machine and lifecycle."""

import time
from .constants import (
    STATUS_PENDING, STATUS_ACTIVE, STATUS_COMPLETED,
    STATUS_EXPIRED, STATUS_DECLINED,
    CMD_CHALLENGE, CMD_ACCEPT, CMD_DECLINE, CMD_RESIGN,
    CMD_DRAW_ACCEPT, CMD_ERROR,
    TTL_PENDING, TTL_ACTIVE, TTL_GRACE_PERIOD,
)
from .errors import IllegalTransition


class Session:
    """Represents an LRGP game session record."""

    __slots__ = (
        "session_id", "identity_id", "app_id", "app_version",
        "contact_hash", "initiator", "status", "metadata",
        "unread", "created_at", "updated_at", "last_action_at",
    )

    def __init__(self, session_id, identity_id="", app_id="", app_version=1,
                 contact_hash="", initiator="", status=STATUS_PENDING,
                 metadata=None, unread=0, created_at=None, updated_at=None,
                 last_action_at=None):
        now = time.time()
        self.session_id = session_id
        self.identity_id = identity_id
        self.app_id = app_id
        self.app_version = app_version
        self.contact_hash = contact_hash
        self.initiator = initiator
        self.status = status
        self.metadata = metadata if metadata is not None else {}
        self.unread = unread
        self.created_at = created_at if created_at is not None else now
        self.updated_at = updated_at if updated_at is not None else now
        self.last_action_at = last_action_at if last_action_at is not None else now

    def to_dict(self):
        return {attr: getattr(self, attr) for attr in self.__slots__}

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: v for k, v in d.items() if k in cls.__slots__})


# Legal state transitions: {current_status: {command: new_status}}
_TRANSITIONS = {
    STATUS_PENDING: {
        CMD_ACCEPT: STATUS_ACTIVE,
        CMD_DECLINE: STATUS_DECLINED,
    },
    STATUS_ACTIVE: {
        CMD_RESIGN: STATUS_COMPLETED,
        CMD_DRAW_ACCEPT: STATUS_COMPLETED,
        # "move" with terminal flag also completes, handled by apply_command
    },
}

# Commands that keep the session in the same status (no transition)
_SAME_STATUS_COMMANDS = {
    STATUS_ACTIVE: {"move", "draw_offer", "draw_decline", "error"},
}


class SessionStateMachine:
    """Enforces legal game session state transitions."""

    @staticmethod
    def apply_command(session, command, terminal=False):
        """Apply a command to a session, updating its status if appropriate.

        Args:
            session: Session instance.
            command: the LRGP command string.
            terminal: if True, the action ends the session (e.g., winning move).

        Returns:
            The new status string.

        Raises:
            IllegalTransition: if the command is not valid for the current status.
        """
        current = session.status

        # Check for explicit transition
        transitions = _TRANSITIONS.get(current, {})
        if command in transitions:
            session.status = transitions[command]
            session.updated_at = time.time()
            session.last_action_at = time.time()
            return session.status

        # Check for same-status commands
        same = _SAME_STATUS_COMMANDS.get(current, set())
        if command in same:
            if terminal:
                session.status = STATUS_COMPLETED
            session.updated_at = time.time()
            session.last_action_at = time.time()
            return session.status

        # Challenge creates a new session (pending) — handled at session creation
        if command == CMD_CHALLENGE and current == STATUS_PENDING:
            session.updated_at = time.time()
            session.last_action_at = time.time()
            return session.status

        raise IllegalTransition(
            "Cannot apply '{}' to session in '{}' state".format(command, current)
        )

    @staticmethod
    def check_expiry(session, ttl=None, now=None):
        """Check if a session has expired based on its TTL.

        Args:
            session: Session instance.
            ttl: dict mapping status to TTL seconds, or None for defaults.
            now: current timestamp, or None for time.time().

        Returns:
            True if the session has expired (and updates session.status).
        """
        if session.status in (STATUS_COMPLETED, STATUS_EXPIRED, STATUS_DECLINED):
            return False

        if now is None:
            now = time.time()

        if ttl is None:
            ttl = {}

        if session.status == STATUS_PENDING:
            limit = ttl.get(STATUS_PENDING, TTL_PENDING)
        elif session.status == STATUS_ACTIVE:
            limit = ttl.get(STATUS_ACTIVE, TTL_ACTIVE)
        else:
            return False

        deadline = session.last_action_at + limit + TTL_GRACE_PERIOD
        if now > deadline:
            session.status = STATUS_EXPIRED
            session.updated_at = now
            return True

        return False
