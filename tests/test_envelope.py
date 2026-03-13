"""Tests for RLAP envelope packing, unpacking, and validation."""

import pytest
from lrgp.envelope import (
    pack_envelope, validate_envelope_size, pack_lxmf_fields,
    unpack_envelope, parse_app_version, measure_content_size,
)
from lrgp.constants import (
    FIELD_CUSTOM_TYPE, FIELD_CUSTOM_META, PROTOCOL_TYPE,
    ENVELOPE_MAX_PACKED, OPPORTUNISTIC_MAX_CONTENT,
)
from lrgp.errors import EnvelopeTooLarge, InvalidEnvelope
from lrgp._msgpack import packb, unpackb


class TestPackEnvelope:
    def test_basic(self):
        env = pack_envelope("ttt", 1, "challenge", "abc123", {})
        assert env["a"] == "ttt.1"
        assert env["c"] == "challenge"
        assert env["s"] == "abc123"
        assert env["p"] == {}

    def test_with_payload(self):
        env = pack_envelope("ttt", 1, "move", "abc123", {"i": 4, "b": "____X____"})
        assert env["p"]["i"] == 4
        assert env["p"]["b"] == "____X____"

    def test_none_payload_becomes_empty_dict(self):
        env = pack_envelope("ttt", 1, "challenge", "abc123")
        assert env["p"] == {}


class TestValidateEnvelopeSize:
    def test_small_envelope_passes(self):
        env = pack_envelope("ttt", 1, "challenge", "a1b2c3d4e5f6g7h8", {})
        size = validate_envelope_size(env)
        assert size <= ENVELOPE_MAX_PACKED

    def test_oversized_envelope_raises(self):
        env = pack_envelope("ttt", 1, "move", "a1b2c3d4e5f6g7h8",
                            {"data": "x" * 300})
        with pytest.raises(EnvelopeTooLarge):
            validate_envelope_size(env)


class TestPackLxmfFields:
    def test_fields_structure(self):
        env = pack_envelope("ttt", 1, "challenge", "abc", {})
        fields = pack_lxmf_fields(env)
        assert fields[FIELD_CUSTOM_TYPE] == PROTOCOL_TYPE
        assert fields[FIELD_CUSTOM_META] == env


class TestUnpackEnvelope:
    def test_valid_rlap(self):
        env = pack_envelope("ttt", 1, "move", "abc", {"i": 4})
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


class TestParseAppVersion:
    def test_basic(self):
        assert parse_app_version("ttt.1") == ("ttt", 1)

    def test_multi_part(self):
        assert parse_app_version("my.app.2") == ("my.app", 2)


class TestRoundtrip:
    def test_msgpack_roundtrip(self):
        env = pack_envelope("ttt", 1, "move", "a1b2c3d4e5f6g7h8",
                            {"i": 4, "b": "____X____", "n": 1})
        packed = packb(env)
        unpacked = unpackb(packed)
        assert unpacked == env

    def test_full_lxmf_roundtrip(self):
        env = pack_envelope("ttt", 1, "challenge", "a1b2c3d4e5f6g7h8", {})
        fields = pack_lxmf_fields(env)
        # Simulate msgpack roundtrip of fields
        packed = packb(fields)
        unpacked = unpackb(packed)
        result = unpack_envelope(unpacked)
        assert result["a"] == "ttt.1"
        assert result["c"] == "challenge"
