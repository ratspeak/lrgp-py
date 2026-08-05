"""Tests for LRGP envelope packing, unpacking, and validation."""

import pytest
from lrgp.envelope import (
    pack_envelope, validate_envelope_size, pack_lxmf_fields,
    unpack_envelope, parse_app_version, measure_content_size,
    generate_nonce,
)
from lrgp.constants import (
    FIELD_CUSTOM_TYPE, FIELD_CUSTOM_META, PROTOCOL_TYPE,
    ENVELOPE_MAX_PACKED, OPPORTUNISTIC_MAX_CONTENT,
    KEY_NONCE, NONCE_BYTES,
)
from lrgp.errors import EnvelopeTooLarge, InvalidEnvelope
from lrgp._msgpack import packb, unpackb

SESSION_ID = "0123456789abcdef"


class TestPackEnvelope:
    def test_basic(self):
        env = pack_envelope("ttt", 1, "challenge", SESSION_ID, {})
        assert env["a"] == "ttt.1"
        assert env["c"] == "challenge"
        assert env["s"] == SESSION_ID
        assert env["p"] == {}

    def test_with_payload(self):
        env = pack_envelope("ttt", 1, "move", SESSION_ID, {"i": 4, "b": "____X____"})
        assert env["p"]["i"] == 4
        assert env["p"]["b"] == "____X____"

    def test_none_payload_becomes_empty_dict(self):
        env = pack_envelope("ttt", 1, "challenge", SESSION_ID)
        assert env["p"] == {}

    def test_auto_nonce_is_bytes_of_correct_length(self):
        env = pack_envelope("ttt", 1, "challenge", SESSION_ID)
        assert isinstance(env[KEY_NONCE], bytes)
        assert len(env[KEY_NONCE]) == NONCE_BYTES

    def test_auto_nonces_differ_between_envelopes(self):
        a = pack_envelope("ttt", 1, "move", SESSION_ID, {})
        b = pack_envelope("ttt", 1, "move", SESSION_ID, {})
        assert a[KEY_NONCE] != b[KEY_NONCE]

    def test_explicit_nonce_round_trips(self):
        fixed = b"\x00\x01\x02\x03\x04\x05\x06\x07"
        env = pack_envelope("ttt", 1, "move", SESSION_ID, {}, nonce=fixed)
        assert env[KEY_NONCE] == fixed

    def test_wrong_length_nonce_raises(self):
        with pytest.raises(InvalidEnvelope):
            pack_envelope("ttt", 1, "move", SESSION_ID, {}, nonce=b"short")

    def test_wrong_type_nonce_raises(self):
        with pytest.raises(InvalidEnvelope):
            pack_envelope("ttt", 1, "move", SESSION_ID, {}, nonce="not-bytes")


class TestGenerateNonce:
    def test_length(self):
        assert len(generate_nonce()) == NONCE_BYTES

    def test_entropy_across_many_calls(self):
        # 1000 nonces at 64 bits should never collide in practice; the
        # birthday bound lands below 2^-40. A collision here means the
        # CSPRNG is broken, not flaky tests.
        seen = {generate_nonce() for _ in range(1000)}
        assert len(seen) == 1000


class TestValidateEnvelopeSize:
    def test_small_envelope_passes(self):
        env = pack_envelope("ttt", 1, "challenge", SESSION_ID, {})
        size = validate_envelope_size(env)
        assert size <= ENVELOPE_MAX_PACKED

    def test_oversized_envelope_raises(self):
        with pytest.raises(EnvelopeTooLarge):
            pack_envelope("ttt", 1, "move", SESSION_ID,
                          {"data": "x" * 300})


class TestPackLxmfFields:
    def test_fields_structure(self):
        env = pack_envelope("ttt", 1, "challenge", SESSION_ID, {})
        fields = pack_lxmf_fields(env)
        assert fields[FIELD_CUSTOM_TYPE] == PROTOCOL_TYPE
        assert fields[FIELD_CUSTOM_META] == env


class TestUnpackEnvelope:
    def test_valid_rlap(self):
        env = pack_envelope("ttt", 1, "move", SESSION_ID, {"i": 4})
        fields = pack_lxmf_fields(env)
        result = unpack_envelope(fields)
        assert result == env

    def test_non_rlap_returns_none(self):
        assert unpack_envelope({}) is None
        assert unpack_envelope({FIELD_CUSTOM_TYPE: "other"}) is None

    def test_missing_keys_raises(self):
        fields = {
            FIELD_CUSTOM_TYPE: PROTOCOL_TYPE,
            FIELD_CUSTOM_META: {"a": "ttt.1"},  # missing c, s, p
        }
        with pytest.raises(InvalidEnvelope):
            unpack_envelope(fields)

    def test_bad_app_version_raises(self):
        fields = {
            FIELD_CUSTOM_TYPE: PROTOCOL_TYPE,
            FIELD_CUSTOM_META: {"a": "ttt", "c": "x", "s": "y", "p": {}},
        }
        with pytest.raises(InvalidEnvelope):
            unpack_envelope(fields)

    def test_non_dict_meta_raises(self):
        fields = {
            FIELD_CUSTOM_TYPE: PROTOCOL_TYPE,
            FIELD_CUSTOM_META: "not a dict",
        }
        with pytest.raises(InvalidEnvelope):
            unpack_envelope(fields)

    def test_non_map_fields_raise_typed_error(self):
        with pytest.raises(InvalidEnvelope):
            unpack_envelope([])

    def test_unencodable_payload_raises_typed_error(self):
        with pytest.raises(InvalidEnvelope):
            pack_envelope(
                "ttt", 1, "move", SESSION_ID, {"value": object()}
            )

    def test_envelope_without_nonce_rejected(self):
        # KEY_NONCE is a required envelope key; missing it is a protocol
        # violation that unpack_envelope must reject.
        fields = {
            FIELD_CUSTOM_TYPE: PROTOCOL_TYPE,
            FIELD_CUSTOM_META: {"a": "ttt.1", "c": "move", "s": "abc", "p": {}},
        }
        with pytest.raises(InvalidEnvelope):
            unpack_envelope(fields)

    def test_malformed_nonce_wrong_length_raises(self):
        fields = {
            FIELD_CUSTOM_TYPE: PROTOCOL_TYPE,
            FIELD_CUSTOM_META: {
                "a": "ttt.1", "c": "move", "s": SESSION_ID, "p": {},
                KEY_NONCE: b"short",
            },
        }
        with pytest.raises(InvalidEnvelope):
            unpack_envelope(fields)

    def test_malformed_nonce_wrong_type_raises(self):
        fields = {
            FIELD_CUSTOM_TYPE: PROTOCOL_TYPE,
            FIELD_CUSTOM_META: {
                "a": "ttt.1", "c": "move", "s": SESSION_ID, "p": {},
                KEY_NONCE: "not-bytes",
            },
        }
        with pytest.raises(InvalidEnvelope):
            unpack_envelope(fields)


class TestParseAppVersion:
    def test_basic(self):
        assert parse_app_version("ttt.1") == ("ttt", 1)

    def test_multi_part(self):
        assert parse_app_version("my.app.2") == ("my.app", 2)


class TestRoundtrip:
    def test_msgpack_roundtrip(self):
        env = pack_envelope("ttt", 1, "move", SESSION_ID,
                            {"i": 4, "b": "____X____", "n": 1})
        packed = packb(env)
        unpacked = unpackb(packed)
        assert unpacked == env

    def test_full_lxmf_roundtrip(self):
        env = pack_envelope("ttt", 1, "challenge", SESSION_ID, {})
        fields = pack_lxmf_fields(env)
        # Simulate msgpack roundtrip of fields
        packed = packb(fields)
        unpacked = unpackb(packed)
        result = unpack_envelope(unpacked)
        assert result["a"] == "ttt.1"
        assert result["c"] == "challenge"
