"""LRGP envelope packing, unpacking, and validation."""

import io
import os
import re

from .constants import (
    FIELD_CUSTOM_TYPE, FIELD_CUSTOM_META, PROTOCOL_TYPE,
    ENVELOPE_MAX_PACKED, OPPORTUNISTIC_MAX_CONTENT,
    KEY_APP, KEY_COMMAND, KEY_SESSION, KEY_PAYLOAD, KEY_NONCE,
    NONCE_BYTES, SESSION_ID_HEX_CHARS,
)
from .errors import EnvelopeTooLarge, InvalidEnvelope
from ._msgpack import packb, unpack

_REQUIRED_KEYS = {KEY_APP, KEY_COMMAND, KEY_SESSION, KEY_PAYLOAD, KEY_NONCE}
_APP_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]*$")
_COMMAND_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SESSION_ID_RE = re.compile(r"^[0-9a-f]{%d}$" % SESSION_ID_HEX_CHARS)


def generate_nonce():
    """Return an 8-byte random nonce suitable for KEY_NONCE."""
    return os.urandom(NONCE_BYTES)


def generate_session_id():
    """Return the canonical lowercase hex encoding of eight random bytes."""
    return os.urandom(SESSION_ID_HEX_CHARS // 2).hex()


def is_canonical_session_id(session_id):
    """Return whether ``session_id`` is the canonical 8-byte lowercase hex form."""
    return isinstance(session_id, str) and bool(_SESSION_ID_RE.fullmatch(session_id))


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
    envelope = {
        KEY_APP: "{}.{}".format(app_id, version),
        KEY_COMMAND: command,
        KEY_SESSION: session_id,
        KEY_PAYLOAD: payload if payload is not None else {},
        KEY_NONCE: bytes(nonce),
    }
    validate_envelope(envelope)
    return envelope


def validate_envelope(envelope):
    """Validate the canonical five-field LRGP envelope and wire budget.

    This is intentionally structural. Whether an app, version, or action is
    supported belongs to the router, which has the registered manifest.
    """
    if not isinstance(envelope, dict):
        raise InvalidEnvelope("Envelope is not a dict")

    keys = set(envelope.keys())
    missing = _REQUIRED_KEYS - keys
    if missing:
        raise InvalidEnvelope("Missing envelope keys: {}".format(sorted(missing)))
    extra = keys - _REQUIRED_KEYS
    if extra:
        raise InvalidEnvelope(
            "Unexpected envelope keys: {}".format(sorted(map(repr, extra)))
        )

    app_ver = envelope[KEY_APP]
    if not isinstance(app_ver, str):
        raise InvalidEnvelope("'a' must be a string")
    app_id, version = parse_app_version(app_ver)
    if not _APP_ID_RE.fullmatch(app_id):
        raise InvalidEnvelope("Invalid app id: {!r}".format(app_id))
    if isinstance(version, bool) or version < 1:
        raise InvalidEnvelope("Version must be a positive integer")

    command = envelope[KEY_COMMAND]
    if not isinstance(command, str) or not _COMMAND_RE.fullmatch(command):
        raise InvalidEnvelope("Invalid command: {!r}".format(command))

    session_id = envelope[KEY_SESSION]
    if not is_canonical_session_id(session_id):
        raise InvalidEnvelope(
            "Session id must be {} lowercase hexadecimal characters".format(
                SESSION_ID_HEX_CHARS
            )
        )

    if not isinstance(envelope[KEY_PAYLOAD], dict):
        raise InvalidEnvelope("'p' must be a map")

    nonce = envelope[KEY_NONCE]
    # The canonical wire representation is msgpack bin8. ``bytearray`` is
    # accepted by ``pack_envelope`` as an ergonomic input and normalized to
    # immutable bytes before this boundary, but decoded envelopes must already
    # have the one canonical binary type.
    if not isinstance(nonce, bytes) or len(nonce) != NONCE_BYTES:
        raise InvalidEnvelope(
            "'n' must be exactly {} bytes".format(NONCE_BYTES)
        )

    validate_envelope_size(envelope)
    return envelope


def validate_envelope_size(envelope):
    """Check that the packed envelope fits within ENVELOPE_MAX_PACKED.

    Returns:
        int: packed size in bytes.

    Raises:
        EnvelopeTooLarge: if packed size exceeds limit.
    """
    try:
        packed = packb(envelope)
    except Exception as exc:
        raise InvalidEnvelope(
            "Envelope contains a value that cannot be encoded: {}".format(exc)
        ) from exc
    size = len(packed)
    if size > ENVELOPE_MAX_PACKED:
        raise EnvelopeTooLarge(
            "Envelope is {} bytes (max {})".format(size, ENVELOPE_MAX_PACKED)
        )
    return size


def pack_to_bytes(envelope):
    """Validate and encode one canonical LRGP envelope as MessagePack."""
    validate_envelope(envelope)
    try:
        return packb(envelope)
    except Exception as exc:
        raise InvalidEnvelope("Could not encode envelope: {}".format(exc)) from exc


def unpack_from_bytes(data):
    """Decode exactly one canonical LRGP envelope, rejecting trailing bytes."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise InvalidEnvelope("Packed envelope must be bytes-like")
    raw = bytes(data)
    stream = io.BytesIO(raw)
    try:
        envelope = unpack(stream)
    except Exception as exc:
        raise InvalidEnvelope("Could not decode envelope: {}".format(exc)) from exc
    if stream.tell() != len(raw):
        raise InvalidEnvelope("Packed envelope contains trailing bytes")
    return validate_envelope(envelope)


def pack_lxmf_fields(envelope):
    """Return LXMF fields dict ready for inclusion in an LXMessage.

    Returns:
        dict: {0xFB: "lrgp.v1", 0xFD: envelope}
    """
    validate_envelope(envelope)
    return {
        FIELD_CUSTOM_TYPE: PROTOCOL_TYPE,
        FIELD_CUSTOM_META: envelope,
    }


def unpack_envelope(fields):
    """Extract and validate an LRGP envelope from LXMF fields.

    Args:
        fields: dict of LXMF fields (keyed by field ID).

    Returns:
        dict: the envelope, or None if not an LRGP message.

    Raises:
        InvalidEnvelope: if fields indicate LRGP but envelope is malformed.
    """
    if not isinstance(fields, dict):
        raise InvalidEnvelope("LXMF fields must be a map")
    custom_type = fields.get(FIELD_CUSTOM_TYPE, "")
    if (isinstance(custom_type, (bytes, bytearray))
            and bytes(custom_type) == PROTOCOL_TYPE.encode("utf-8")):
        raise InvalidEnvelope(
            "LRGP custom type must be a native MessagePack string, not binary"
        )
    if custom_type != PROTOCOL_TYPE:
        return None

    envelope = fields.get(FIELD_CUSTOM_META)
    if not isinstance(envelope, dict):
        raise InvalidEnvelope("FIELD_CUSTOM_META is not a dict")

    return validate_envelope(envelope)


def parse_app_version(app_ver_string):
    """Split 'app_id.version' into (app_id, version_int).

    Returns:
        tuple: (app_id: str, version: int)
    """
    if not isinstance(app_ver_string, str) or "." not in app_ver_string:
        raise InvalidEnvelope("Invalid app.version format: {!r}".format(app_ver_string))
    app_id, raw_version = app_ver_string.rsplit(".", 1)
    if not app_id or re.fullmatch(r"[0-9]+", raw_version) is None:
        raise InvalidEnvelope("Invalid app.version format: {!r}".format(app_ver_string))
    version = int(raw_version)
    if version < 1 or version > 0xFFFFFFFF or str(version) != raw_version:
        raise InvalidEnvelope("Version is not canonical: {!r}".format(raw_version))
    return app_id, version


def measure_content_size(title, content, fields):
    """Measure total packed LXMF content size.

    Simulates the LXMF packing: [timestamp, title, content, fields_dict].

    Returns:
        int: total packed size in bytes.
    """
    import time
    payload = [time.time(), title or "", content or "", fields or {}]
    return len(packb(payload))
