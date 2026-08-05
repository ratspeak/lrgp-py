"""Tests for the optional LXMF bridge that do not require RNS/LXMF."""

from types import SimpleNamespace

import pytest

from lrgp.constants import FIELD_CUSTOM_META, FIELD_CUSTOM_TYPE, PROTOCOL_TYPE
from lrgp.envelope import pack_envelope, pack_lxmf_fields
from lrgp.errors import InvalidEnvelope
from lrgp.transport import LrgpTransport


SID = "0123456789abcdef"


class FakeLxmfRouter:
    def __init__(self):
        self.callback = None

    def register_delivery_callback(self, callback):
        self.callback = callback


def _transport():
    router = FakeLxmfRouter()
    return LrgpTransport(router, object()), router


def test_delivery_callback_validates_and_dispatches_canonical_fields():
    transport, router = _transport()
    received = []
    transport.register_handler(
        lambda envelope, sender, lxm: received.append((envelope, sender, lxm))
    )
    envelope = pack_envelope(
        "ttt", 1, "challenge", SID, {}, nonce=b"\x01" * 8
    )
    lxm = SimpleNamespace(
        fields=pack_lxmf_fields(envelope), source_hash=b"\xaa\xbb",
        signature_validated=True,
    )

    router.callback(lxm)

    assert received == [(envelope, "aabb", lxm)]


def test_delivery_callback_ignores_non_lrgp_fields():
    transport, router = _transport()
    received = []
    transport.register_handler(lambda *args: received.append(args))

    router.callback(SimpleNamespace(fields={}, source_hash=b"\xaa"))

    assert received == []


@pytest.mark.parametrize("signature_validated,source_hash", [
    (False, b"\xaa"),
    (None, b"\xaa"),
    (True, b""),
    (True, "aa"),
])
def test_delivery_callback_never_exposes_unauthenticated_sender(
        signature_validated, source_hash):
    transport, router = _transport()
    received = []
    transport.register_handler(lambda *args: received.append(args))
    envelope = pack_envelope(
        "ttt", 1, "challenge", SID, {}, nonce=b"\x02" * 8
    )

    router.callback(SimpleNamespace(
        fields=pack_lxmf_fields(envelope),
        source_hash=source_hash,
        signature_validated=signature_validated,
    ))

    assert received == []


def test_delivery_callback_rejects_malformed_lrgp_before_user_handler():
    transport, router = _transport()
    received = []
    transport.register_handler(lambda *args: received.append(args))
    malformed = {
        FIELD_CUSTOM_TYPE: PROTOCOL_TYPE,
        FIELD_CUSTOM_META: {"a": "ttt.1"},
    }

    with pytest.raises(InvalidEnvelope):
        router.callback(SimpleNamespace(fields=malformed, source_hash=b"\xaa"))

    assert received == []


def test_send_rejects_unknown_delivery_without_importing_optional_stack():
    transport, _router = _transport()
    envelope = pack_envelope("ttt", 1, "challenge", SID, {})

    with pytest.raises(ValueError):
        transport.send("00", envelope, "fallback", delivery="store")
