"""LRGP GameBase abstract class — the interface all LRGP games must implement."""

from abc import ABC, abstractmethod
import threading

from .constants import CMD_CHALLENGE, CMD_ERROR
from .envelope import is_canonical_session_id
from .errors import (
    InvalidEnvelope, ParticipantRequired, SessionExists, SessionExpired,
    SessionNotFound, UnauthorizedPeer,
)
from .session import Session, SessionStateMachine


class GameBase(ABC):
    """Abstract base for LRGP games.

    Subclasses must set class attributes and implement abstract methods.
    """

    # --- Class attributes (override in subclass) ---
    app_id = ""
    version = 1
    display_name = ""
    icon = ""
    session_type = "turn_based"
    max_players = 2
    min_players = 2
    validation = "sender"
    actions = []
    preferred_delivery = {}
    ttl = {"pending": 86400, "active": 604800}
    genre = None
    turn_timeout = None

    def __init__(self):
        self._sessions = {}
        self._session_lock = threading.RLock()

    # --- Required methods ---

    @abstractmethod
    def handle_incoming(self, session_id, command, payload, sender_hash,
                        identity_id):
        """Process an incoming LRGP game action.

        Returns:
            dict with keys: "session", "emit", "error"
        """

    @abstractmethod
    def handle_outgoing(self, session_id, command, payload, identity_id):
        """Prepare an outgoing LRGP game action.

        Participant authorization and binding are router responsibilities;
        application hooks retain the original four-argument API.

        Returns:
            tuple: (enriched_payload: dict, fallback_text: str)
        """

    @abstractmethod
    def validate_action(self, session_id, command, payload, sender_hash,
                        identity_id=""):
        """Validate an action.

        Returns:
            tuple: (valid: bool, error_message: str or None)
        """

    @abstractmethod
    def get_session_state(self, session_id, identity_id):
        """Return current session state for rendering."""

    @abstractmethod
    def render_fallback(self, command, payload):
        """Generate human-readable fallback text for LXMF content field."""

    # --- Session lifecycle shared by every game ---

    @staticmethod
    def _session_key(session_id, identity_id=""):
        return session_id, identity_id

    def _get_session(self, session_id, identity_id="", now=None):
        """Load a session and enforce its TTL before returning it."""
        key = self._session_key(session_id, identity_id)
        with self._session_lock:
            session = self._sessions.get(key)
            if session is not None and SessionStateMachine.check_expiry(
                    session, self.ttl, now=now):
                self._sessions[key] = session
            return session

    def _save_session(self, session):
        with self._session_lock:
            self._sessions[self._session_key(
                session.session_id, session.identity_id
            )] = session

    def get_session_record(self, session_id, identity_id="", now=None):
        """Return a TTL-checked session record, or ``None``."""
        return self._get_session(session_id, identity_id, now=now)

    def upsert_session(self, record, now=None):
        """Hydrate one persisted session into the application's live store."""
        session = record if isinstance(record, Session) else Session.from_dict(record)
        if not is_canonical_session_id(session.session_id):
            raise InvalidEnvelope("Hydrated session id is not canonical")
        if session.app_id != self.app_id or session.app_version != self.version:
            raise InvalidEnvelope("Hydrated session belongs to another app/version")
        if not session.identity_id:
            raise InvalidEnvelope("Hydrated session must include identity_id")
        SessionStateMachine.check_expiry(session, self.ttl, now=now)
        metadata = session.metadata
        if isinstance(metadata, dict) and (
                "draw_offered" in metadata or "draw_offered_by" in metadata):
            offered = metadata.get("draw_offered") is True
            owner = metadata.get("draw_offered_by")
            if not offered or not isinstance(owner, str) or not owner:
                # Records written before draw ownership was persisted cannot
                # safely authorize a response.  A stale owner with a cleared
                # flag is likewise normalized away.
                metadata["draw_offered"] = False
                metadata["draw_offered_by"] = ""
            elif owner not in (session.identity_id, session.contact_hash):
                raise InvalidEnvelope(
                    "Hydrated draw offer owner is not a bound participant"
                )
        self._save_session(session)
        return session

    hydrate_session = upsert_session

    def remove_session(self, session_id, identity_id=""):
        """Remove a live session and return whether one existed."""
        with self._session_lock:
            return self._sessions.pop(
                self._session_key(session_id, identity_id), None
            ) is not None

    def list_session_records(self, identity_id=None, now=None):
        """Return TTL-checked snapshots, optionally for one local identity."""
        with self._session_lock:
            keys = list(self._sessions)
        records = []
        for session_id, local_identity in keys:
            if identity_id is not None and local_identity != identity_id:
                continue
            session = self._get_session(session_id, local_identity, now=now)
            if session is not None:
                records.append(Session.from_dict(session.to_dict()))
        return records

    def bind_peer(self, session_id, identity_id, participant_hash):
        """Bind a session to its one remote participant, never silently rebind."""
        if not participant_hash:
            raise ParticipantRequired()
        session = self._get_session(session_id, identity_id)
        if session is None:
            raise SessionNotFound(session_id)
        if session.contact_hash and session.contact_hash != participant_hash:
            raise UnauthorizedPeer(session_id)
        session.contact_hash = participant_hash
        self._save_session(session)
        return session

    def authorize_session(self, session, sender_hash):
        """Require an action to come from the session's bound peer."""
        if not session.contact_hash or session.contact_hash != sender_hash:
            raise UnauthorizedPeer(session.session_id)
        return session

    def authorize_incoming(self, session_id, command, sender_hash,
                           identity_id=""):
        """Authorize the transport sender before application dispatch."""
        session = self._get_session(session_id, identity_id)
        if session is None:
            if command == CMD_CHALLENGE:
                return None
            raise SessionNotFound(session_id)
        if session.status == "expired":
            raise SessionExpired(session_id)
        return self.authorize_session(session, sender_hash)

    def require_live_session(self, session_id, identity_id="", now=None):
        """Load a session or raise a typed missing/expired failure."""
        session = self._get_session(session_id, identity_id, now=now)
        if session is None:
            raise SessionNotFound(session_id)
        if session.status == "expired":
            raise SessionExpired(session_id)
        return session

    def validate_outgoing(self, session_id, command, payload, identity_id,
                          participant_hash=""):
        """Shared pre-mutation checks; games add command-specific validation."""
        if command == CMD_CHALLENGE:
            if not participant_hash:
                raise ParticipantRequired()
            existing = self._get_session(session_id, identity_id)
            if existing is not None:
                raise SessionExists(session_id)
            return None
        session = self.require_live_session(session_id, identity_id)
        if participant_hash and session.contact_hash != participant_hash:
            raise UnauthorizedPeer(session_id)
        if not session.contact_hash:
            raise ParticipantRequired()
        if command != CMD_ERROR:
            transition_probe = Session.from_dict(session.to_dict())
            SessionStateMachine.apply_command(transition_probe, command)
        return session

    def snapshot_session(self, session_id, identity_id=""):
        session = self._get_session(session_id, identity_id)
        return Session.from_dict(session.to_dict()) if session is not None else None

    def rollback_session(self, session_id, identity_id="", snapshot=None):
        if snapshot is None:
            self.remove_session(session_id, identity_id)
        else:
            self._save_session(Session.from_dict(snapshot.to_dict()))

    # --- Optional methods ---

    def get_delivery_method(self, command):
        """Return preferred delivery method for this command."""
        return self.preferred_delivery.get(command, "opportunistic")

    def get_manifest(self):
        """Build manifest dict from class attributes."""
        manifest = {
            "app_id": self.app_id,
            "version": self.version,
            "display_name": self.display_name,
            "icon": self.icon,
            "session_type": self.session_type,
            "max_players": self.max_players,
            "min_players": self.min_players,
            "validation": self.validation,
            "actions": list(self.actions),
            "preferred_delivery": dict(self.preferred_delivery),
            "ttl": dict(self.ttl),
        }
        if self.genre is not None:
            manifest["genre"] = self.genre
        if self.turn_timeout is not None:
            manifest["turn_timeout"] = self.turn_timeout
        return manifest
