"""LRGP error hierarchy."""


class LrgpError(Exception):
    """Base error for all LRGP operations."""


class EnvelopeTooLarge(LrgpError):
    """Packed envelope exceeds ENVELOPE_MAX_PACKED bytes."""


class InvalidEnvelope(LrgpError):
    """Envelope is malformed or missing required fields."""


class IllegalTransition(LrgpError):
    """Session state transition is not allowed."""


class UnknownApp(LrgpError):
    """No registered handler for the given game."""


class ValidationError(LrgpError):
    """Action failed validation (invalid move, not your turn, etc.)."""

    def __init__(self, code, message=""):
        self.code = code
        super().__init__(message or code)
