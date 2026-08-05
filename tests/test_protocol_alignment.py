"""Focused cross-implementation contract tests for the LRGP router boundary."""

import pytest

from lrgp.apps.tictactoe import EMPTY_BOARD, TicTacToeApp
from lrgp.constants import (
    CMD_ACCEPT, CMD_CHALLENGE, CMD_DECLINE, CMD_ERROR, CMD_MOVE,
    DEDUP_TTL_SECONDS, ERR_INVALID_MOVE, ERR_PROTOCOL_ERROR, STATUS_ACTIVE,
    FIELD_CUSTOM_META, FIELD_CUSTOM_TYPE, PROTOCOL_TYPE, STATUS_EXPIRED,
    STATUS_PENDING,
)
from lrgp.dedup import ReplayDedup
from lrgp.envelope import (
    pack_envelope, pack_lxmf_fields, pack_to_bytes, unpack_envelope,
    unpack_from_bytes, validate_envelope,
)
from lrgp.errors import (
    AdmissionLimit, AuthenticatedSenderRequired, IllegalTransition,
    InvalidEnvelope, OutgoingActionError, OutgoingIdentityRequired,
    ParticipantRequired, ReceivingIdentityRequired, SessionExists,
    SessionExpired, UnauthorizedPeer, UnsupportedAction, UnsupportedVersion,
)
from lrgp.router import (
    IncomingDispatch, LrgpRouter, PreparedOutgoing, RemoteProtocolError,
)
from lrgp.session import Session


SID = "0123456789abcdef"
OTHER_SID = "1111111111111111"
LOCAL = "local_identity"
PEER = "remote_peer"
ATTACKER = "not_the_peer"


def _router():
    router = LrgpRouter()
    router.register(TicTacToeApp())
    return router


class OtherTicTacToe(TicTacToeApp):
    app_id = "other_ttt"


class ErrorRejectingTicTacToe(TicTacToeApp):
    def validate_outgoing(self, session_id, command, payload, identity_id,
                          participant_hash=""):
        if command == CMD_ERROR:
            raise AssertionError("game validator must not see standard error")
        return super().validate_outgoing(
            session_id, command, payload, identity_id, participant_hash
        )


def _record(sid, app_id="ttt", peer=PEER, status=STATUS_PENDING):
    return Session(
        session_id=sid,
        identity_id=LOCAL,
        app_id=app_id,
        app_version=1,
        contact_hash=peer,
        initiator=peer,
        status=status,
    )


def _env(command=CMD_CHALLENGE, payload=None, sid=SID, nonce=b"\x01" * 8,
         app_id="ttt", version=1):
    return pack_envelope(
        app_id, version, command, sid, payload or {}, nonce=nonce
    )


class TestCanonicalEnvelope:
    @pytest.mark.parametrize("session_id", [
        "ABCDEF0123456789", "0123456789abcdeg", "short",
        "0123456789abcdef0",
    ])
    def test_session_id_is_exact_lowercase_hex(self, session_id):
        with pytest.raises(InvalidEnvelope):
            pack_envelope("ttt", 1, CMD_CHALLENGE, session_id, {})

    @pytest.mark.parametrize("app_id,version", [
        ("TTT", 1), ("1ttt", 1), ("ttt", 0), ("ttt", -1),
    ])
    def test_app_and_version_are_canonical(self, app_id, version):
        with pytest.raises(InvalidEnvelope):
            pack_envelope(app_id, version, CMD_CHALLENGE, SID, {})

    def test_leading_zero_version_is_rejected(self):
        envelope = _env()
        envelope["a"] = "ttt.01"
        with pytest.raises(InvalidEnvelope):
            validate_envelope(envelope)

    def test_top_level_map_has_exactly_five_keys(self):
        envelope = _env()
        envelope["future"] = True
        with pytest.raises(InvalidEnvelope):
            validate_envelope(envelope)

    def test_mixed_type_extra_key_is_rejected_as_invalid_envelope(self):
        envelope = _env()
        envelope[1] = True
        with pytest.raises(InvalidEnvelope):
            validate_envelope(envelope)

    @pytest.mark.parametrize("key,value", [
        ("a", 1), ("c", 1), ("s", b"0123456789abcdef"),
        ("p", []), ("n", "12345678"),
    ])
    def test_each_required_field_has_one_canonical_type(self, key, value):
        envelope = _env()
        envelope[key] = value
        with pytest.raises(InvalidEnvelope):
            validate_envelope(envelope)

    def test_decoded_nonce_must_be_immutable_msgpack_binary(self):
        envelope = _env()
        envelope["n"] = bytearray(b"12345678")
        with pytest.raises(InvalidEnvelope):
            validate_envelope(envelope)

    def test_version_digits_are_ascii_only(self):
        envelope = _env()
        envelope["a"] = "ttt.\u00b2"
        with pytest.raises(InvalidEnvelope):
            validate_envelope(envelope)

    def test_version_must_fit_unsigned_32_bits(self):
        with pytest.raises(InvalidEnvelope):
            pack_envelope("ttt", 0x100000000, CMD_CHALLENGE, SID, {})

    def test_strict_byte_codec_round_trips_one_envelope(self):
        envelope = _env()
        assert unpack_from_bytes(pack_to_bytes(envelope)) == envelope

    def test_strict_byte_codec_rejects_trailing_bytes(self):
        with pytest.raises(InvalidEnvelope, match="trailing bytes"):
            unpack_from_bytes(pack_to_bytes(_env()) + b"\x00")

    def test_strict_byte_codec_rejects_duplicate_top_level_key(self):
        encoded = pack_to_bytes(_env())
        duplicate = bytes([0x86]) + encoded[1:] + b"\xa1a\xa5ttt.1"
        with pytest.raises(InvalidEnvelope, match="duplicate key"):
            unpack_from_bytes(duplicate)

    def test_strict_byte_codec_rejects_duplicate_nested_payload_key(self):
        duplicate = (
            b"\x85\xa1a\xa5ttt.1\xa1c\xa9challenge"
            b"\xa1s\xb00123456789abcdef"
            b"\xa1p\x82\xa1x\x01\xa1x\x02"
            b"\xa1n\xc4\x08\x01\x01\x01\x01\x01\x01\x01\x01"
        )
        with pytest.raises(InvalidEnvelope, match="duplicate key"):
            unpack_from_bytes(duplicate)

    @pytest.mark.parametrize("value", [None, "not bytes", 42])
    def test_strict_byte_codec_requires_bytes_like_input(self, value):
        with pytest.raises(InvalidEnvelope):
            unpack_from_bytes(value)

    def test_lxmf_fields_use_native_string_and_map_values(self):
        envelope = _env()
        fields = pack_lxmf_fields(envelope)
        assert fields[FIELD_CUSTOM_TYPE] == PROTOCOL_TYPE
        assert isinstance(fields[FIELD_CUSTOM_TYPE], str)
        assert fields[FIELD_CUSTOM_META] == envelope
        assert isinstance(fields[FIELD_CUSTOM_META], dict)
        assert unpack_envelope(fields) == envelope

    def test_binary_wrapped_lxmf_pseudo_fields_are_rejected(self):
        envelope = _env()
        with pytest.raises(InvalidEnvelope, match="native MessagePack string"):
            unpack_envelope({
                FIELD_CUSTOM_TYPE: PROTOCOL_TYPE.encode(),
                FIELD_CUSTOM_META: envelope,
            })
        with pytest.raises(InvalidEnvelope, match="not a dict"):
            unpack_envelope({
                FIELD_CUSTOM_TYPE: PROTOCOL_TYPE,
                FIELD_CUSTOM_META: pack_to_bytes(envelope),
            })


class TestRouterReplayAndSupport:
    def test_duplicate_is_explicit_replay_before_app_dispatch(self):
        router = _router()
        envelope = _env()
        first = router.dispatch_incoming(envelope, PEER, LOCAL)
        second = router.dispatch_incoming(envelope, PEER, LOCAL)
        assert isinstance(first, IncomingDispatch)
        assert first.kind == "applied"
        assert second.kind == "replay"

    def test_replay_namespace_includes_receiving_identity(self):
        router = _router()
        envelope = _env()
        assert router.dispatch_incoming(envelope, PEER, "identity_a").kind == "applied"
        assert router.dispatch_incoming(envelope, PEER, "identity_b").kind == "applied"

    def test_unauthorized_sender_does_not_consume_participant_nonce(self):
        router = _router()
        router.dispatch_outgoing_to(
            "ttt", CMD_CHALLENGE, {}, SID, LOCAL, PEER
        )
        accept = _env(
            CMD_ACCEPT, {"b": EMPTY_BOARD, "t": LOCAL}, nonce=b"\x02" * 8
        )
        with pytest.raises(UnauthorizedPeer):
            router.dispatch_incoming(accept, ATTACKER, LOCAL)
        applied = router.dispatch_incoming(accept, PEER, LOCAL)
        assert applied.kind == "applied"
        assert applied["session"]["status"] == "active"

    def test_terminal_retransmit_stays_replay_until_ttl(self):
        router = _router()
        router.dispatch_outgoing_to(
            "ttt", CMD_CHALLENGE, {}, SID, LOCAL, PEER
        )
        decline = _env(CMD_DECLINE, nonce=b"\x03" * 8)
        assert router.dispatch_incoming(decline, PEER, LOCAL).kind == "applied"
        assert router.dispatch_incoming(decline, PEER, LOCAL).kind == "replay"

    def test_fresh_nonce_duplicate_challenge_is_idempotent_for_bound_peer(self):
        router = _router()
        first = router.dispatch_incoming(
            _env(nonce=b"\x11" * 8), PEER, LOCAL
        )
        duplicate = router.dispatch_incoming(
            _env(nonce=b"\x12" * 8), PEER, LOCAL
        )
        assert first.kind == "applied"
        assert first["emit"]["type"] == "challenge"
        assert duplicate.kind == "applied"
        assert duplicate["emit"] is None
        assert router.list_sessions("ttt", LOCAL)[0].contact_hash == PEER

    def test_duplicate_challenge_from_other_peer_is_unauthorized_and_nonce_safe(self):
        router = _router()
        router.dispatch_incoming(_env(nonce=b"\x13" * 8), PEER, LOCAL)
        attempted = _env(nonce=b"\x14" * 8)
        with pytest.raises(UnauthorizedPeer):
            router.dispatch_incoming(attempted, ATTACKER, LOCAL)
        # Failed authorization did not reserve the nonce against the bound peer.
        result = router.dispatch_incoming(attempted, PEER, LOCAL)
        assert result.kind == "applied"
        assert result["emit"] is None

    def test_version_and_manifest_action_are_enforced(self):
        router = _router()
        with pytest.raises(UnsupportedVersion):
            router.dispatch_incoming(_env(version=2), PEER, LOCAL)
        with pytest.raises(UnsupportedAction):
            router.dispatch_incoming(_env(command="teleport"), PEER, LOCAL)

    def test_error_payload_is_exact_and_nonempty(self):
        router = _router()
        for payload in (
            {"code": "bad", "msg": "bad"},
            {"code": "bad", "msg": "bad", "ref": ""},
            {"code": "bad", "msg": "bad", "ref": "move", "x": 1},
        ):
            with pytest.raises(InvalidEnvelope):
                router.dispatch_incoming(_env(CMD_ERROR, payload), PEER, LOCAL)

    def test_builtin_payload_rejection_happens_before_session_mutation(self):
        router = _router()
        malformed_challenge = _env(
            payload={"unexpected": True}, nonce=b"\x70" * 8
        )
        result = router.dispatch_incoming(malformed_challenge, PEER, LOCAL)
        assert result["error"]["code"] == ERR_PROTOCOL_ERROR
        assert router.list_sessions("ttt", LOCAL) == []

        router.dispatch_outgoing_to(
            "ttt", CMD_CHALLENGE, {}, SID, LOCAL, PEER
        )
        malformed_accept = _env(
            CMD_ACCEPT,
            {"b": "X________", "t": LOCAL},
            nonce=b"\x71" * 8,
        )
        result = router.dispatch_incoming(malformed_accept, PEER, LOCAL)
        assert result["error"]["code"] == ERR_PROTOCOL_ERROR
        assert router.list_sessions("ttt", LOCAL)[0].status == STATUS_PENDING

    def test_builtin_outgoing_intent_shape_is_strict(self):
        router = _router()
        with pytest.raises(OutgoingActionError):
            router.dispatch_outgoing_to(
                "ttt", CMD_CHALLENGE, {"unexpected": True}, SID, LOCAL, PEER
            )
        assert router.list_sessions("ttt", LOCAL) == []

    def test_remote_error_is_typed_and_never_rolls_back_session(self):
        router = _router()
        router.dispatch_outgoing_to(
            "ttt", CMD_CHALLENGE, {}, SID, LOCAL, PEER
        )
        before = router.list_sessions("ttt", LOCAL)[0].to_dict()
        envelope = _env(
            CMD_ERROR,
            {"code": ERR_INVALID_MOVE, "msg": "Bad square", "ref": CMD_MOVE},
            nonce=b"\x15" * 8,
        )
        result = router.dispatch_incoming(envelope, PEER, LOCAL)
        assert result.kind == "remote_error"
        assert result.is_remote_error
        assert isinstance(result.result, RemoteProtocolError)
        assert result.result == RemoteProtocolError(
            app_id="ttt",
            session_id=SID,
            code=ERR_INVALID_MOVE,
            message="Bad square",
            reference=CMD_MOVE,
        )
        assert router.list_sessions("ttt", LOCAL)[0].to_dict() == before
        assert router.dispatch_incoming(envelope, PEER, LOCAL).kind == "replay"


class TestReplayBounds:
    def test_duplicate_does_not_extend_absolute_ttl(self):
        dedup = ReplayDedup(ttl_seconds=10)
        envelope = _env()
        assert dedup.check(envelope, now=0) is False
        assert dedup.check(envelope, now=9) is True
        assert dedup.check(envelope, now=11) is False

    def test_outer_session_cache_is_bounded(self):
        dedup = ReplayDedup(max_sessions=2)
        first = _env(sid="0000000000000001", nonce=b"\x01" * 8)
        second = _env(sid="0000000000000002", nonce=b"\x02" * 8)
        third = _env(sid="0000000000000003", nonce=b"\x03" * 8)
        assert dedup.check(first) is False
        assert dedup.check(second) is False
        assert dedup.check(third) is False
        assert len(dedup._by_session) == 2
        assert dedup.check(first) is False


class TestOutgoingAndSessions:
    @pytest.mark.parametrize("sender", ["", "  ", None])
    def test_incoming_requires_transport_authenticated_sender_before_parsing(
            self, sender):
        router = _router()
        with pytest.raises(AuthenticatedSenderRequired):
            router.dispatch_incoming({"not": "an envelope"}, sender, LOCAL)
        assert router.list_sessions("ttt", LOCAL) == []
        assert router._replay._by_session == {}

    @pytest.mark.parametrize("identity", ["", "\t", None])
    def test_incoming_requires_receiving_identity_before_parsing(self, identity):
        router = _router()
        with pytest.raises(ReceivingIdentityRequired):
            router.dispatch_incoming({"not": "an envelope"}, PEER, identity)
        assert router.list_sessions("ttt", LOCAL) == []
        assert router._replay._by_session == {}

    @pytest.mark.parametrize("identity", ["", "  ", None])
    def test_outgoing_requires_local_identity_without_creating_session(
            self, identity):
        router = _router()
        with pytest.raises(OutgoingIdentityRequired):
            router.dispatch_outgoing_to(
                "ttt", CMD_CHALLENGE, {}, SID, identity, PEER
            )
        assert router.list_sessions("ttt", LOCAL) == []

    @pytest.mark.parametrize("participant", ["", "\n", None])
    def test_outgoing_challenge_requires_nonblank_participant_without_mutation(
            self, participant):
        router = _router()
        with pytest.raises(ParticipantRequired):
            router.dispatch_outgoing_to(
                "ttt", CMD_CHALLENGE, {}, SID, LOCAL, participant
            )
        assert router.list_sessions("ttt", LOCAL) == []

    def test_bound_outgoing_entry_requires_recipient_before_app_resolution(self):
        router = _router()
        with pytest.raises(ParticipantRequired):
            router.dispatch_outgoing_to(
                "unknown", CMD_ACCEPT, {}, SID, LOCAL, "  "
            )

    def test_challenge_requires_and_binds_participant(self):
        router = _router()
        with pytest.raises(ParticipantRequired):
            router.dispatch_outgoing("ttt", CMD_CHALLENGE, {}, SID, LOCAL)

        prepared = router.dispatch_outgoing_to(
            "ttt", CMD_CHALLENGE, {}, SID, LOCAL, PEER
        )
        assert isinstance(prepared, PreparedOutgoing)
        assert prepared.session_id == SID
        assert prepared.delivery_method == "opportunistic"
        session = router.list_sessions("ttt", LOCAL)[0]
        assert session.contact_hash == PEER

    def test_empty_challenge_id_is_generated_and_returned(self):
        router = _router()
        prepared = router.dispatch_outgoing_to(
            "ttt", CMD_CHALLENGE, {}, "", LOCAL, PEER
        )
        assert len(prepared.session_id) == 16
        assert prepared.envelope["s"] == prepared.session_id

    def test_outgoing_mismatch_and_invalid_move_are_typed(self):
        router = _router()
        router.dispatch_outgoing_to(
            "ttt", CMD_CHALLENGE, {}, SID, LOCAL, PEER
        )
        with pytest.raises(UnauthorizedPeer):
            router.dispatch_outgoing_to(
                "ttt", CMD_ACCEPT, {}, SID, LOCAL, ATTACKER
            )

        accept = _env(
            CMD_ACCEPT, {"b": EMPTY_BOARD, "t": LOCAL}, nonce=b"\x04" * 8
        )
        router.dispatch_incoming(accept, PEER, LOCAL)
        with pytest.raises(OutgoingActionError) as error:
            router.dispatch_outgoing_to(
                "ttt", CMD_MOVE, {"i": 99}, SID, LOCAL, PEER
            )
        assert error.value.code == ERR_INVALID_MOVE
        assert error.value.ref == CMD_MOVE

    def test_outgoing_duplicate_challenge_is_typed_and_non_mutating(self):
        router = _router()
        router.dispatch_outgoing_to(
            "ttt", CMD_CHALLENGE, {}, SID, LOCAL, PEER
        )
        before = router.list_sessions("ttt", LOCAL)[0].to_dict()
        with pytest.raises(SessionExists) as error:
            router.dispatch_outgoing_to(
                "ttt", CMD_CHALLENGE, {}, SID, LOCAL, PEER
            )
        assert str(error.value) == "session already exists: {}".format(SID)
        assert router.list_sessions("ttt", LOCAL)[0].to_dict() == before


class TestGlobalSessionIdentity:
    @staticmethod
    def _two_app_router():
        router = _router()
        router.register(OtherTicTacToe())
        return router

    def test_outgoing_challenge_rejects_cross_app_collision(self):
        router = self._two_app_router()
        router.hydrate_session(_record(SID, app_id="other_ttt"))
        with pytest.raises(SessionExists):
            router.dispatch_outgoing_to(
                "ttt", CMD_CHALLENGE, {}, SID, LOCAL, PEER
            )

    def test_incoming_cross_app_collision_consumes_nonce(self):
        router = self._two_app_router()
        router.hydrate_session(_record(SID, app_id="other_ttt"))
        envelope = _env(nonce=b"\x81" * 8)
        with pytest.raises(SessionExists):
            router.dispatch_incoming(envelope, PEER, LOCAL)
        assert router.dispatch_incoming(envelope, PEER, LOCAL).kind == "replay"

    def test_restore_rejects_cross_app_collision(self):
        router = self._two_app_router()
        router.hydrate_session(_record(SID, app_id="other_ttt"))
        with pytest.raises(SessionExists):
            router.hydrate_session(_record(SID, app_id="ttt"))

    def test_same_app_restore_may_update_existing_session(self):
        router = self._two_app_router()
        router.hydrate_session(_record(SID))
        updated = _record(SID, peer="updated_peer")
        assert router.hydrate_session(updated).contact_hash == "updated_peer"


class TestChallengeAdmission:
    def test_participant_limit_is_checked_first_and_replay_is_retained(self):
        router = _router()
        for index in range(16):
            router.hydrate_session(_record("{:016x}".format(index), peer=PEER))

        rejected = _env(
            sid="ffffffffffffffff", nonce=b"\x91" * 8
        )
        with pytest.raises(AdmissionLimit) as error:
            router.dispatch_incoming(rejected, PEER, LOCAL)
        assert (error.value.scope, error.value.limit) == ("participant", 16)
        assert router.dispatch_incoming(rejected, PEER, LOCAL).kind == "replay"
        assert len(router.list_sessions("ttt", LOCAL)) == 16

    def test_same_peer_existing_challenge_bypasses_full_quota(self):
        router = _router()
        for index in range(16):
            router.hydrate_session(_record("{:016x}".format(index), peer=PEER))
        result = router.dispatch_incoming(
            _env(sid="0000000000000000", nonce=b"\x92" * 8), PEER, LOCAL
        )
        assert result.kind == "applied"
        assert result["emit"] is None

    def test_identity_limit_counts_pending_across_apps(self):
        router = _router()
        router.register(OtherTicTacToe())
        for index in range(128):
            app_id = "ttt" if index % 2 == 0 else "other_ttt"
            router.hydrate_session(_record(
                "{:016x}".format(index), app_id=app_id,
                peer="peer_{:03d}".format(index),
            ))
        with pytest.raises(AdmissionLimit) as error:
            router.dispatch_incoming(
                _env(sid="ffffffffffffffff", nonce=b"\x93" * 8),
                "new_peer", LOCAL,
            )
        assert (error.value.scope, error.value.limit) == ("identity", 128)

    def test_active_sessions_do_not_consume_pending_quota(self):
        router = _router()
        for index in range(128):
            router.hydrate_session(_record(
                "{:016x}".format(index),
                peer="peer_{:03d}".format(index),
                status=STATUS_ACTIVE,
            ))
        result = router.dispatch_incoming(
            _env(sid="ffffffffffffffff", nonce=b"\x94" * 8),
            "new_peer", LOCAL,
        )
        assert result.kind == "applied"
        assert len(router.list_sessions("ttt", LOCAL)) == 129

    def test_outgoing_error_has_canonical_generic_fallback(self):
        router = _router()
        router.dispatch_outgoing_to(
            "ttt", CMD_CHALLENGE, {}, SID, LOCAL, PEER
        )
        before = router.snapshot_session("ttt", SID, LOCAL).to_dict()
        prepared = router.dispatch_outgoing_to(
            "ttt",
            CMD_ERROR,
            {"code": ERR_INVALID_MOVE, "msg": "Bad square", "ref": CMD_MOVE},
            SID,
            LOCAL,
            PEER,
        )
        assert prepared.fallback_text == "[LRGP] Protocol error"
        assert prepared.envelope["p"] == {
            "code": ERR_INVALID_MOVE, "msg": "Bad square", "ref": CMD_MOVE,
        }
        assert router.snapshot_session("ttt", SID, LOCAL).to_dict() == before

    def test_standard_error_bypasses_game_specific_action_validator(self):
        router = LrgpRouter()
        router.register(ErrorRejectingTicTacToe())
        router.dispatch_outgoing_to(
            "ttt", CMD_CHALLENGE, {}, SID, LOCAL, PEER
        )

        prepared = router.dispatch_outgoing_to(
            "ttt", CMD_ERROR,
            {"code": ERR_INVALID_MOVE, "msg": "Bad square", "ref": CMD_MOVE},
            SID, LOCAL, PEER,
        )

        assert prepared.envelope["c"] == CMD_ERROR

    def test_legacy_outgoing_rejects_noncanonical_session_before_lookup(self):
        router = _router()
        with pytest.raises(InvalidEnvelope):
            router.dispatch_outgoing("ttt", CMD_ACCEPT, {}, "short", LOCAL)

    def test_ttl_is_enforced_before_inbound_and_outbound_actions(self):
        inbound = _router()
        inbound.dispatch_outgoing_to(
            "ttt", CMD_CHALLENGE, {}, SID, LOCAL, PEER
        )
        session = inbound.get_app("ttt")._get_session(SID, LOCAL)
        session.last_action_at = 0
        inbound.get_app("ttt")._save_session(session)
        with pytest.raises(SessionExpired):
            inbound.dispatch_incoming(
                _env(CMD_ACCEPT, {"b": EMPTY_BOARD, "t": LOCAL},
                     nonce=b"\x16" * 8),
                PEER,
                LOCAL,
            )
        outbound = _router()
        outbound.dispatch_outgoing_to(
            "ttt", CMD_CHALLENGE, {}, SID, LOCAL, PEER
        )
        session = outbound.get_app("ttt")._get_session(SID, LOCAL)
        session.last_action_at = 0
        outbound.get_app("ttt")._save_session(session)
        with pytest.raises(SessionExpired):
            outbound.dispatch_outgoing_to(
                "ttt", CMD_ACCEPT, {}, SID, LOCAL, PEER
            )

    def test_hydrate_applies_ttl_and_remove_deletes_session(self):
        router = _router()
        record = Session(
            session_id=OTHER_SID,
            identity_id=LOCAL,
            app_id="ttt",
            app_version=1,
            contact_hash=PEER,
            initiator=LOCAL,
            status="pending",
            last_action_at=0,
            created_at=0,
            updated_at=0,
        )
        restored = router.hydrate_session(
            record, now=86400 + 3600 + 1
        )
        assert restored.status == STATUS_EXPIRED
        assert router.list_sessions("ttt", LOCAL)[0].status == STATUS_EXPIRED
        assert router.remove_session("ttt", OTHER_SID, LOCAL) is True
        assert router.list_sessions("ttt", LOCAL) == []

    def test_hydrate_normalizes_legacy_draw_offer_without_owner(self):
        router = _router()
        record = _record(OTHER_SID, status=STATUS_ACTIVE)
        record.metadata = {
            "board": EMPTY_BOARD,
            "draw_offered": True,
        }

        restored = router.hydrate_session(record)

        assert restored.metadata["draw_offered"] is False
        assert restored.metadata["draw_offered_by"] == ""

    def test_hydrate_rejects_draw_owner_outside_bound_participants(self):
        router = _router()
        record = _record(OTHER_SID, status=STATUS_ACTIVE)
        record.metadata = {
            "board": EMPTY_BOARD,
            "draw_offered": True,
            "draw_offered_by": ATTACKER,
        }

        with pytest.raises(InvalidEnvelope):
            router.hydrate_session(record)

        assert router.list_sessions("ttt", LOCAL) == []

    def test_incoming_error_rolls_back_application_mutation(self):
        router = _router()
        router.dispatch_outgoing_to(
            "ttt", CMD_CHALLENGE, {}, SID, LOCAL, PEER
        )
        accept = _env(
            CMD_ACCEPT, {"b": EMPTY_BOARD, "t": LOCAL}, nonce=b"\x05" * 8
        )
        router.dispatch_incoming(accept, PEER, LOCAL)
        before = router.list_sessions("ttt", LOCAL)[0].to_dict()
        forged = _env(
            CMD_MOVE,
            {"i": 0, "b": "_________", "n": 1, "t": PEER, "x": ""},
            nonce=b"\x06" * 8,
        )
        result = router.dispatch_incoming(forged, PEER, LOCAL)
        assert set(result["error"]) == {"code", "msg", "ref"}
        assert result["error"]["ref"] == CMD_MOVE
        assert router.list_sessions("ttt", LOCAL)[0].to_dict() == before

    def test_snapshot_rollback_restores_nested_game_state(self):
        router = _router()
        router.dispatch_outgoing_to(
            "ttt", CMD_CHALLENGE, {}, SID, LOCAL, PEER
        )
        app = router.get_app("ttt")
        session = app._get_session(SID, LOCAL)
        session.metadata["nested"] = {"moves": ["original"]}
        app._save_session(session)
        snapshot = router.snapshot_before_outgoing("ttt", SID, LOCAL)

        session.metadata["nested"]["moves"].append("mutated")
        router.rollback_outgoing("ttt", SID, LOCAL, snapshot)

        restored = app._get_session(SID, LOCAL)
        assert restored.metadata["nested"]["moves"] == ["original"]

    def test_durable_incoming_rollback_restores_state_and_releases_nonce(self):
        router = _router()
        other_identity = "other_local_identity"
        router.dispatch_outgoing_to(
            "ttt", CMD_CHALLENGE, {}, SID, LOCAL, PEER
        )
        router.dispatch_outgoing_to(
            "ttt", CMD_CHALLENGE, {}, SID, other_identity, PEER
        )
        accept = _env(
            CMD_ACCEPT, {"b": EMPTY_BOARD, "t": LOCAL}, nonce=b"\x35" * 8
        )
        snapshot = router.snapshot_session("ttt", SID, LOCAL)

        first = router.dispatch_incoming(accept, PEER, LOCAL)
        assert first.kind == "applied"
        assert router.list_sessions("ttt", LOCAL)[0].status == STATUS_ACTIVE
        other_first = router.dispatch_incoming(accept, PEER, other_identity)
        assert other_first.kind == "applied"
        assert other_first["error"] is not None

        router.rollback_incoming(
            "ttt", SID, LOCAL, accept["n"], snapshot
        )
        assert router.list_sessions("ttt", LOCAL)[0].status == STATUS_PENDING

        retried = router.dispatch_incoming(accept, PEER, LOCAL)
        assert retried.kind == "applied"
        assert router.list_sessions("ttt", LOCAL)[0].status == STATUS_ACTIVE
        assert router.dispatch_incoming(
            accept, PEER, other_identity
        ).kind == "replay"

    def test_incoming_rollback_rejects_noncanonical_nonce(self):
        router = _router()
        with pytest.raises(InvalidEnvelope):
            router.rollback_incoming("ttt", SID, LOCAL, b"short", None)

    def test_unauthorized_fresh_nonce_cannot_evict_legitimate_replay(self):
        router = LrgpRouter(
            replay=ReplayDedup(max_per_session=1)
        )
        router.register(TicTacToeApp())
        router.hydrate_session(_record(SID, status=STATUS_ACTIVE))
        payload = {
            "code": ERR_INVALID_MOVE,
            "msg": "Bad square",
            "ref": CMD_MOVE,
        }
        legitimate = _env(CMD_ERROR, payload, nonce=b"\x61" * 8)
        unauthorized = _env(CMD_ERROR, payload, nonce=b"\x62" * 8)

        assert router.dispatch_incoming(
            legitimate, PEER, LOCAL
        ).kind == "remote_error"
        with pytest.raises(UnauthorizedPeer):
            router.dispatch_incoming(unauthorized, ATTACKER, LOCAL)

        assert router.dispatch_incoming(
            legitimate, PEER, LOCAL
        ).kind == "replay"

    def test_remote_error_nonce_recovery_is_scoped_and_keeps_state(self):
        router = _router()
        identities = ("local_a", "local_b")
        for identity in identities:
            record = _record(SID, status=STATUS_ACTIVE)
            record.identity_id = identity
            router.hydrate_session(record)
        before = {
            identity: router.snapshot_session("ttt", SID, identity).to_dict()
            for identity in identities
        }
        payload = {
            "code": ERR_INVALID_MOVE,
            "msg": "Bad square",
            "ref": CMD_MOVE,
        }
        remote_error = _env(CMD_ERROR, payload, nonce=b"\x58" * 8)
        for identity in identities:
            assert router.dispatch_incoming(
                remote_error, PEER, identity
            ).kind == "remote_error"

        router.forget_incoming_nonce("local_a", SID, remote_error["n"])

        assert router.dispatch_incoming(
            remote_error, PEER, "local_a"
        ).kind == "remote_error"
        assert router.dispatch_incoming(
            remote_error, PEER, "local_b"
        ).kind == "replay"
        for identity in identities:
            assert router.snapshot_session(
                "ttt", SID, identity
            ).to_dict() == before[identity]

    def test_remote_error_nonce_recovery_rejects_noncanonical_nonce(self):
        router = _router()
        with pytest.raises(InvalidEnvelope):
            router.forget_incoming_nonce(LOCAL, SID, b"short")
