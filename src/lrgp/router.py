"""LRGP game registry, replay-safe dispatch, and session hydration."""

from collections.abc import Mapping
from dataclasses import dataclass
import importlib
import pkgutil
import threading

from .app_base import GameBase
from .constants import (
    CMD_CHALLENGE, CMD_ERROR,
    KEY_APP, KEY_COMMAND, KEY_NONCE, KEY_PAYLOAD, KEY_SESSION,
    NONCE_BYTES,
    PENDING_SESSIONS_PER_IDENTITY_MAX,
    PENDING_SESSIONS_PER_PARTICIPANT_MAX, STATUS_PENDING,
    STATUS_EXPIRED,
)
from .dedup import ReplayDedup
from .envelope import (
    generate_session_id, is_canonical_session_id, pack_envelope,
    parse_app_version, validate_envelope,
)
from .errors import (
    AdmissionLimit, AuthenticatedSenderRequired, InvalidEnvelope,
    OutgoingIdentityRequired, ParticipantRequired, ReceivingIdentityRequired,
    SessionExpired, SessionExists, SessionNotFound, UnauthorizedPeer, UnknownApp,
    UnsupportedAction, UnsupportedVersion,
)


class IncomingDispatch(Mapping):
    """Typed incoming verdict matching Rust's ``Applied``/``Replay`` enum.

    Applied values also behave as a read-only mapping over the legacy result,
    so existing callers can continue to use ``dispatch["session"]``.
    """

    __slots__ = ("kind", "result")

    def __init__(self, kind, result=None):
        self.kind = kind
        self.result = result

    @classmethod
    def applied(cls, result):
        return cls("applied", result)

    @classmethod
    def replay(cls):
        return cls("replay", None)

    @property
    def is_replay(self):
        return self.kind == "replay"

    @property
    def is_remote_error(self):
        return self.kind == "remote_error"

    def __getitem__(self, key):
        if self.kind != "applied":
            raise KeyError(key)
        return self.result[key]

    def __iter__(self):
        return iter(self.result if self.kind == "applied" else {})

    def __len__(self):
        return len(self.result) if self.kind == "applied" else 0

    def __bool__(self):
        return self.result is not None


@dataclass(frozen=True)
class PreparedOutgoing:
    """Validated outbound envelope and its transport presentation."""

    envelope: dict
    session_id: str
    fallback_text: str
    delivery_method: str

    def __iter__(self):
        """Preserve legacy three-value tuple unpacking."""
        yield self.envelope
        yield self.fallback_text
        yield self.delivery_method


@dataclass(frozen=True)
class RemoteProtocolError:
    """Authenticated remote ``error`` action surfaced for reconciliation."""

    app_id: str
    session_id: str
    code: str
    message: str
    reference: str


class LrgpRouter:
    """Thread-safe app registry with router-owned replay protection."""

    def __init__(self, replay=None):
        self._registry = {}
        self._registry_lock = threading.RLock()
        self._replay = replay if replay is not None else ReplayDedup()
        self._replay_lock = threading.Lock()
        self._admission_lock = threading.RLock()

    def register(self, app):
        if not isinstance(app, GameBase):
            raise TypeError("app must implement GameBase")
        with self._registry_lock:
            self._registry[app.app_id] = app

    def unregister(self, app_id):
        with self._registry_lock:
            self._registry.pop(app_id, None)

    def get_app(self, app_id):
        with self._registry_lock:
            return self._registry.get(app_id)

    def list_apps(self):
        with self._registry_lock:
            return [app.get_manifest() for app in self._registry.values()]

    def discover(self, package):
        """Scan a package for ``GameBase`` subclasses and register them."""
        for _importer, name, _ispkg in pkgutil.iter_modules(package.__path__):
            try:
                module = importlib.import_module("{}.{}".format(package.__name__, name))
            except (ImportError, RuntimeError):
                continue
            for attr_name in dir(module):
                cls = getattr(module, attr_name)
                if (isinstance(cls, type) and issubclass(cls, GameBase)
                        and cls is not GameBase and cls.app_id):
                    self.register(cls())

    def _resolve(self, app_id, version=None, command=None):
        app = self.get_app(app_id)
        if app is None:
            raise UnknownApp("No handler for game '{}'".format(app_id))
        if version is not None and version != app.version:
            raise UnsupportedVersion(app_id, version, app.version)
        if command is not None:
            supported = set(app.actions)
            supported.add(CMD_ERROR)
            if command not in supported:
                raise UnsupportedAction(app_id, command)
        return app

    @staticmethod
    def _validate_error_payload(payload):
        if set(payload) != {"code", "msg", "ref"}:
            raise InvalidEnvelope("error payload must contain exactly code, msg, ref")
        if any(not isinstance(payload[key], str) or not payload[key]
               for key in ("code", "msg", "ref")):
            raise InvalidEnvelope(
                "error payload code, msg, and ref must be non-empty strings"
            )

    def _enforce_challenge_admission(self, identity_id, participant_hash):
        """Fail closed when a new inbound challenge exceeds fixed quotas."""
        with self._registry_lock:
            apps = list(self._registry.values())
        pending = []
        for registered in apps:
            pending.extend(
                session for session in registered.list_session_records(
                    identity_id=identity_id
                )
                if session.status == STATUS_PENDING
            )
        participant_count = sum(
            session.contact_hash == participant_hash for session in pending
        )
        if participant_count >= PENDING_SESSIONS_PER_PARTICIPANT_MAX:
            raise AdmissionLimit(
                "participant", PENDING_SESSIONS_PER_PARTICIPANT_MAX
            )
        if len(pending) >= PENDING_SESSIONS_PER_IDENTITY_MAX:
            raise AdmissionLimit(
                "identity", PENDING_SESSIONS_PER_IDENTITY_MAX
            )

    def _find_session_owner(self, session_id, identity_id):
        """Return the TTL-checked app/session owning a global session key."""
        with self._registry_lock:
            apps = list(self._registry.values())
        for registered in apps:
            session = registered.get_session_record(session_id, identity_id)
            if session is not None:
                return registered, session
        return None

    def _dispatch_authorized(self, app, app_id, command, session_id, payload,
                             envelope, sender_hash, identity_id):
        # Game sessions are mutable Python objects. Keep authorization,
        # replay insertion, handler mutation, and rollback in one app-scoped
        # critical section so concurrent deliveries cannot validate against
        # the same pre-action state and then both mutate it.
        with app._session_lock:
            return self._dispatch_authorized_locked(
                app, app_id, command, session_id, payload, envelope,
                sender_hash, identity_id,
            )

    def _dispatch_authorized_locked(self, app, app_id, command, session_id,
                                    payload, envelope, sender_hash,
                                    identity_id):
        app.authorize_incoming(
            session_id, command, sender_hash, identity_id
        )

        # Record only after participant authorization. The second atomic
        # check resolves concurrent copies that both passed the non-recording
        # probe, while unauthenticated fresh nonces cannot consume or evict
        # replay state.
        with self._replay_lock:
            if self._replay.check(envelope, scope=identity_id):
                return IncomingDispatch.replay()

        if command == CMD_CHALLENGE:
            owner = self._find_session_owner(session_id, identity_id)
            if owner is not None and owner[0] is not app:
                # The durable storage key excludes app_id. This is a global
                # collision, not an authorization failure, so its nonce remains.
                raise SessionExists(session_id)

        if (command == CMD_CHALLENGE
                and app.get_session_record(session_id, identity_id) is None):
            self._enforce_challenge_admission(identity_id, sender_hash)

        if command == CMD_ERROR:
            # A remote error reports the peer's view of an earlier action. Its
            # ``ref`` is a command name, not a nonce/action correlation key, so
            # it must never be mistaken for a local rejection, sent through a
            # game handler, or used to roll state backward.
            return IncomingDispatch(
                "remote_error",
                RemoteProtocolError(
                    app_id=app_id,
                    session_id=session_id,
                    code=payload["code"],
                    message=payload["msg"],
                    reference=payload["ref"],
                ),
            )

        snapshot = app.snapshot_session(session_id, identity_id)
        try:
            result = app.handle_incoming(
                session_id, command, payload, sender_hash, identity_id
            )
        except Exception:
            app.rollback_session(session_id, identity_id, snapshot)
            raise

        if isinstance(result, dict) and result.get("error") is not None:
            app.rollback_session(session_id, identity_id, snapshot)
            self._validate_error_payload(result["error"])
        return IncomingDispatch.applied(result)

    def dispatch_incoming(self, envelope, sender_hash, identity_id=""):
        """Validate, deduplicate, authorize, and dispatch one envelope."""
        # These values are supplied by the authenticated transport boundary,
        # not by the LRGP envelope. Reject missing scope before parsing or
        # touching replay/application state so an unauthenticated delivery can
        # neither allocate a session nor consume a nonce.
        if not isinstance(sender_hash, str) or not sender_hash.strip():
            raise AuthenticatedSenderRequired()
        if not isinstance(identity_id, str) or not identity_id.strip():
            raise ReceivingIdentityRequired()
        validate_envelope(envelope)
        app_id, version = parse_app_version(envelope[KEY_APP])
        command = envelope[KEY_COMMAND]
        session_id = envelope[KEY_SESSION]
        payload = envelope[KEY_PAYLOAD]
        app = self._resolve(app_id, version, command)

        if command == CMD_ERROR:
            self._validate_error_payload(payload)

        with self._replay_lock:
            if self._replay.probe(envelope, scope=identity_id):
                return IncomingDispatch.replay()

        if command == CMD_CHALLENGE:
            # Admission count + creation must be atomic across apps. Re-run
            # authorization inside the lock so a concurrent first challenge
            # cannot turn a second sender into an apparent fresh session.
            with self._admission_lock:
                return self._dispatch_authorized(
                    app, app_id, command, session_id, payload, envelope,
                    sender_hash, identity_id,
                )
        return self._dispatch_authorized(
            app, app_id, command, session_id, payload, envelope,
            sender_hash, identity_id,
        )

    def snapshot_session(self, app_id, session_id, identity_id=""):
        """Snapshot live state before an external durable transaction."""
        app = self._resolve(app_id)
        return app.snapshot_session(session_id, identity_id)

    snapshot_before_outgoing = snapshot_session

    def rollback_outgoing(self, app_id, session_id, identity_id="", snapshot=None):
        app = self._resolve(app_id)
        app.rollback_session(session_id, identity_id, snapshot)

    def rollback_incoming(self, app_id, session_id, identity_id, nonce,
                          snapshot=None):
        """Undo an applied inbound action after durable persistence fails.

        Restoring the session alone is insufficient: the exact envelope must
        be allowed through replay protection when its transport retransmits.
        Only that receiving-identity/session/nonce entry is forgotten.
        """
        if not isinstance(nonce, bytes) or len(nonce) != NONCE_BYTES:
            raise InvalidEnvelope(
                "Incoming rollback nonce must be exactly {} bytes".format(
                    NONCE_BYTES
                )
            )
        app = self._resolve(app_id)
        with self._admission_lock:
            app.rollback_session(session_id, identity_id, snapshot)
            with self._replay_lock:
                self._replay.forget(session_id, nonce, scope=identity_id)

    def forget_incoming_nonce(self, identity_id, session_id, nonce):
        """Release one accepted replay key without changing game state.

        This is the narrow durable-recovery path for an authenticated
        ``remote_error`` result. Remote errors consume a nonce but do not
        mutate the session, so restoring a snapshot would be incorrect.
        """
        if not isinstance(nonce, bytes) or len(nonce) != NONCE_BYTES:
            raise InvalidEnvelope(
                "Incoming nonce must be exactly {} bytes".format(NONCE_BYTES)
            )
        with self._replay_lock:
            self._replay.forget(session_id, nonce, scope=identity_id)

    def dispatch_outgoing_to(self, app_id, command, payload, session_id,
                             identity_id, participant_hash):
        """Prepare an outbound action bound to its remote participant."""
        # This entry point always promises a concrete transport recipient,
        # including for non-challenge actions. Enforce that promise before
        # doing any application lookup or mutation.
        if (not isinstance(participant_hash, str)
                or not participant_hash.strip()):
            raise ParticipantRequired()
        if not isinstance(identity_id, str) or not identity_id.strip():
            raise OutgoingIdentityRequired()
        app = self._resolve(app_id, command=command)
        if not isinstance(payload, dict):
            raise InvalidEnvelope("Outgoing payload must be a map")
        if command == CMD_ERROR:
            self._validate_error_payload(payload)

        if not session_id and command == CMD_CHALLENGE:
            session_id = generate_session_id()
        if not is_canonical_session_id(session_id):
            raise InvalidEnvelope("Outgoing session id is not canonical")

        def prepare():
            if command == CMD_CHALLENGE:
                owner = self._find_session_owner(session_id, identity_id)
                if owner is not None:
                    raise SessionExists(session_id)
            snapshot = app.snapshot_session(session_id, identity_id)
            try:
                if command == CMD_ERROR:
                    # ``error`` is a router-owned standard action, not a
                    # game-specific action. Apply only the shared live-session
                    # and participant checks; a custom game's action validator
                    # must not make protocol errors unsendable.
                    GameBase.validate_outgoing(
                        app, session_id, command, payload, identity_id,
                        participant_hash,
                    )
                    enriched = payload
                    fallback = "[LRGP] Protocol error"
                else:
                    app.validate_outgoing(
                        session_id, command, payload, identity_id,
                        participant_hash,
                    )
                    enriched, fallback = app.handle_outgoing(
                        session_id, command, payload, identity_id
                    )
                    if command == CMD_CHALLENGE:
                        app.bind_peer(session_id, identity_id, participant_hash)
                envelope = pack_envelope(
                    app.app_id, app.version, command, session_id, enriched
                )
            except Exception:
                app.rollback_session(session_id, identity_id, snapshot)
                raise
            return envelope, fallback

        if command == CMD_CHALLENGE:
            with self._admission_lock:
                with app._session_lock:
                    envelope, fallback = prepare()
        else:
            with app._session_lock:
                envelope, fallback = prepare()

        return PreparedOutgoing(
            envelope=envelope,
            session_id=session_id,
            fallback_text=fallback,
            delivery_method=app.get_delivery_method(command),
        )

    def dispatch_outgoing(self, app_id, command, payload, session_id,
                          identity_id=""):
        """Compatibility path; challenges fail closed without a participant."""
        if not isinstance(identity_id, str) or not identity_id.strip():
            raise OutgoingIdentityRequired()
        app = self._resolve(app_id, command=command)
        if command == CMD_CHALLENGE:
            raise ParticipantRequired()
        if not is_canonical_session_id(session_id):
            raise InvalidEnvelope("Outgoing session id is not canonical")
        session = app.require_live_session(session_id, identity_id)
        return self.dispatch_outgoing_to(
            app_id, command, payload, session_id, identity_id,
            session.contact_hash,
        )

    def hydrate_session(self, record, now=None):
        """Restore a persisted session into the matching application."""
        app_id = record.app_id if hasattr(record, "app_id") else record.get("app_id", "")
        version = (record.app_version if hasattr(record, "app_version")
                   else record.get("app_version"))
        app = self._resolve(app_id, version)
        session_id = (record.session_id if hasattr(record, "session_id")
                      else record.get("session_id", ""))
        identity_id = (record.identity_id if hasattr(record, "identity_id")
                       else record.get("identity_id", ""))
        with self._admission_lock:
            owner = self._find_session_owner(session_id, identity_id)
            if owner is not None and owner[0] is not app:
                raise SessionExists(session_id)
            return app.upsert_session(record, now=now)

    restore_session = hydrate_session

    def list_sessions(self, app_id, identity_id=None, now=None):
        app = self._resolve(app_id)
        return app.list_session_records(identity_id=identity_id, now=now)

    def remove_session(self, app_id, session_id, identity_id=""):
        app = self._resolve(app_id)
        with self._admission_lock:
            removed = app.remove_session(session_id, identity_id)
            if removed:
                with self._replay_lock:
                    self._replay.drop_session(session_id, scope=identity_id)
        return removed

    def sweep_expired(self, now=None):
        """Apply session TTLs and prune replay entries by their nonce TTL."""
        expired = []
        with self._registry_lock:
            apps = list(self._registry.values())
        for app in apps:
            for session in app.list_session_records(now=now):
                if session.status == STATUS_EXPIRED:
                    expired.append(session)
        with self._replay_lock:
            self._replay.sweep()
        return expired


_default_router = LrgpRouter()
# Kept as aliases for callers/tests that historically inspected the registry.
_registry = _default_router._registry
_registry_lock = _default_router._registry_lock


def register(app):
    return _default_router.register(app)


def unregister(app_id):
    return _default_router.unregister(app_id)


def get_app(app_id):
    return _default_router.get_app(app_id)


def list_apps():
    return _default_router.list_apps()


def discover(package):
    return _default_router.discover(package)


def dispatch_incoming(envelope, sender_hash, identity_id=""):
    return _default_router.dispatch_incoming(envelope, sender_hash, identity_id)


def dispatch_outgoing_to(app_id, command, payload, session_id,
                         identity_id, participant_hash):
    return _default_router.dispatch_outgoing_to(
        app_id, command, payload, session_id, identity_id, participant_hash
    )


def dispatch_outgoing(app_id, command, payload, session_id, identity_id=""):
    return _default_router.dispatch_outgoing(
        app_id, command, payload, session_id, identity_id
    )


def hydrate_session(record, now=None):
    return _default_router.hydrate_session(record, now=now)


restore_session = hydrate_session


def snapshot_session(app_id, session_id, identity_id=""):
    return _default_router.snapshot_session(app_id, session_id, identity_id)


snapshot_before_outgoing = snapshot_session


def rollback_outgoing(app_id, session_id, identity_id="", snapshot=None):
    return _default_router.rollback_outgoing(
        app_id, session_id, identity_id, snapshot
    )


def rollback_incoming(app_id, session_id, identity_id, nonce, snapshot=None):
    return _default_router.rollback_incoming(
        app_id, session_id, identity_id, nonce, snapshot
    )


def forget_incoming_nonce(identity_id, session_id, nonce):
    return _default_router.forget_incoming_nonce(
        identity_id, session_id, nonce
    )


def list_sessions(app_id, identity_id=None, now=None):
    return _default_router.list_sessions(app_id, identity_id, now=now)


def remove_session(app_id, session_id, identity_id=""):
    return _default_router.remove_session(app_id, session_id, identity_id)
