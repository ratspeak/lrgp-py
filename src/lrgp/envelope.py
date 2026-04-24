"""LRGP envelope packing, unpacking, and validation."""

import os

from .constants import (
    FIELD_CUSTOM_TYPE, FIELD_CUSTOM_META, PROTOCOL_TYPE, LEGACY_TYPES,
    ENVELOPE_MAX_PACKED, OPPORTUNISTIC_MAX_CONTENT,
    KEY_APP, KEY_COMMAND, KEY_SESSION, KEY_PAYLOAD, KEY_NONCE,
    NONCE_BYTES,
)
from .errors import EnvelopeTooLarge, InvalidEnvelope
from ._msgpack import packb, unpackb

_REQUIRED_KEYS = {KEY_APP, KEY_COMMAND, KEY_SESSION, KEY_PAYLOAD}


def generate_nonce():
    """Return an 8-byte random nonce suitable for KEY_NONCE."""
    return os.urandom(NONCE_BYTES)


def pack_envelope(app_id, version, command, session_id, payload=None, nonce=None):
    """Build an LRGP envelope dict.

    Args:
        nonce: optional 8-byte bytes object. If ``None`` (default) a fresh
            CSPRNG nonce is generated. Pass a fixed value to build
            deterministic test vectors.

    Returns:
        dict with keys "a", "c", "s", "p", "n".
    """
    if nonce is None:
        nonce = generate_nonce()
    elif not isinstance(nonce, (bytes, bytearray)) or len(nonce) != NONCE_BYTES:
        raise InvalidEnvelope(
            "nonce must be {}-byte bytes; got {!r}".format(NONCE_BYTES, nonce)
        )
    return {
        KEY_APP: "{}.{}".format(app_id, version),
        KEY_COMMAND: command,
        KEY_SESSION: session_id,
        KEY_PAYLOAD: payload if payload is not None else {},
        KEY_NONCE: bytes(nonce),
    }


def validate_envelope_size(envelope):
    """Check that the packed envelope fits within ENVELOPE_MAX_PACKED.

    Returns:
        int: packed size in bytes.

    Raises:
        EnvelopeTooLarge: if packed size exceeds limit.
    """
    packed = packb(envelope)
    size = len(packed)
    if size > ENVELOPE_MAX_PACKED:
        raise EnvelopeTooLarge(
            "Envelope is {} bytes (max {})".format(size, ENVELOPE_MAX_PACKED)
        )
    return size


def pack_lxmf_fields(envelope):
    """Return LXMF fields dict ready for inclusion in an LXMessage.

    Returns:
        dict: {0xFB: "lrgp.v1", 0xFD: envelope}
    """
    return {
        FIELD_CUSTOM_TYPE: PROTOCOL_TYPE,
        FIELD_CUSTOM_META: envelope,
    }


def unpack_envelope(fields):
    """Extract and validate an LRGP envelope from LXMF fields.

    Recognizes both lrgp.v1 and legacy rlap.v1/ratspeak.game markers.

    Args:
        fields: dict of LXMF fields (keyed by field ID).

    Returns:
        dict: the envelope, or None if not an LRGP message.

    Raises:
        InvalidEnvelope: if fields indicate LRGP but envelope is malformed.
    """
    custom_type = fields.get(FIELD_CUSTOM_TYPE, "")
    if custom_type != PROTOCOL_TYPE and custom_type not in LEGACY_TYPES:
        return None

    envelope = fields.get(FIELD_CUSTOM_META)
    if not isinstance(envelope, dict):
        raise InvalidEnvelope("FIELD_CUSTOM_META is not a dict")

    missing = _REQUIRED_KEYS - set(envelope.keys())
    if missing:
        raise InvalidEnvelope("Missing envelope keys: {}".format(missing))

    app_ver = envelope[KEY_APP]
    if not isinstance(app_ver, str) or "." not in app_ver:
        raise InvalidEnvelope("Invalid app.version format: {!r}".format(app_ver))

    # Validate the nonce if present; absence is accepted for backward
    # compatibility with pre-nonce peers (the caller's dedup layer logs
    # these once per session).
    if KEY_NONCE in envelope:
        n = envelope[KEY_NONCE]
        if not isinstance(n, (bytes, bytearray)) or len(n) != NONCE_BYTES:
            raise InvalidEnvelope(
                "KEY_NONCE must be {}-byte bytes; got {!r}".format(NONCE_BYTES, n)
            )

    return envelope


def parse_app_version(app_ver_string):
    """Split 'app_id.version' into (app_id, version_int).

    Returns:
        tuple: (app_id: str, version: int)
    """
    parts = app_ver_string.rsplit(".", 1)
    return parts[0], int(parts[1])


def measure_content_size(title, content, fields):
    """Measure total packed LXMF content size.

    Simulates the LXMF packing: [timestamp, title, content, fields_dict].

    Returns:
        int: total packed size in bytes.
    """
    import time
    payload = [time.time(), title or "", content or "", fields or {}]
    return len(packb(payload))
