"""LRGP error hierarchy."""


class LrgpError(Exception):
    """Base error for all LRGP operations."""


class EnvelopeTooLarge(LrgpError):
    """Packed envelope exceeds ENVELOPE_MAX_PACKED bytes."""


class InvalidEnvelope(LrgpError):
    """Envelope is malformed or missing required fields."""


class AuthenticatedSenderRequired(LrgpError):
    """Incoming dispatch lacks a transport-authenticated remote sender."""

    def __init__(self):
        super().__init__(
            "incoming dispatch requires a non-empty transport-authenticated sender"
        )


class ReceivingIdentityRequired(LrgpError):
    """Incoming dispatch lacks the receiving local identity scope."""

    def __init__(self):
        super().__init__(
            "incoming dispatch requires a non-empty receiving local identity"
        )


class OutgoingIdentityRequired(LrgpError):
    """Outgoing dispatch lacks its sending local identity."""

    def __init__(self):
        super().__init__(
            "outgoing dispatch requires a non-empty local identity"
        )


class IllegalTransition(LrgpError):
    """Session state transition is not allowed."""


class UnknownApp(LrgpError):
    """No registered handler for the given game."""


class UnsupportedVersion(LrgpError):
    """Envelope version does not match the registered application."""

    def __init__(self, app_id, received, supported):
        self.app_id = app_id
        self.received = received
        self.supported = supported
        super().__init__(
            "Unsupported {} version {} (supported: {})".format(
                app_id, received, supported
            )
        )


class UnsupportedAction(LrgpError):
    """Command is not declared by the registered application."""

    def __init__(self, app_id, command):
        self.app_id = app_id
        self.command = command
        super().__init__("Unsupported {} action: {}".format(app_id, command))


class UnauthorizedPeer(LrgpError):
    """A session action came from a peer other than its participant."""

    def __init__(self, session_id):
        self.session_id = session_id
        super().__init__("Peer is not authorized for session {}".format(session_id))


class SessionExpired(LrgpError):
    """A session exceeded its status-specific TTL."""

    def __init__(self, session_id):
        self.session_id = session_id
        super().__init__("Session expired: {}".format(session_id))


class SessionNotFound(LrgpError):
    """A requested game session does not exist."""

    def __init__(self, session_id):
        self.session_id = session_id
        super().__init__("Session not found: {}".format(session_id))


class SessionExists(LrgpError):
    """A challenge or restore collides with an existing global session ID."""

    def __init__(self, session_id):
        self.session_id = session_id
        super().__init__("session already exists: {}".format(session_id))


class ParticipantRequired(LrgpError):
    """An outgoing challenge did not identify its remote participant."""

    def __init__(self):
        super().__init__("Outgoing challenges require a participant hash")


class AdmissionLimit(LrgpError):
    """A new inbound challenge exceeded a fixed pending-session quota."""

    def __init__(self, scope, limit):
        self.scope = scope
        self.limit = limit
        super().__init__(
            "Pending challenge {} limit reached ({})".format(scope, limit)
        )


class ValidationError(LrgpError):
    """Action failed validation (invalid move, not your turn, etc.)."""

    def __init__(self, code, message="", ref=""):
        self.code = code
        self.message = message or code
        self.ref = ref
        super().__init__(message or code)

    def to_payload(self):
        """Return the canonical LRGP ``{code,msg,ref}`` error payload."""
        return {"code": self.code, "msg": self.message, "ref": self.ref}


class OutgoingActionError(ValidationError):
    """Typed failure raised before an invalid outgoing action can mutate state."""


def error_payload(code, message, ref):
    """Build the exact three-string error map required on the wire."""
    return {"code": str(code), "msg": str(message), "ref": str(ref)}


def incoming_error(code, message, ref, session=None):
    """Build the canonical incoming handler failure shape."""
    return {
        "session": session.to_dict() if hasattr(session, "to_dict") else session,
        "emit": None,
        "error": error_payload(code, message, ref),
    }
