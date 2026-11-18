"""Rock Paper Scissors: commitments, play, hydration, and shared vectors."""

import copy
import hashlib
import os
import sys

import pytest

from lrgp._msgpack import packb, unpackb
from lrgp.apps import rps
from lrgp.apps.rps import (
    PHASE_CHOOSE,
    PHASE_COMMIT,
    PHASE_REVEAL,
    R_ROUNDS,
    RpsApp,
    beats,
    commitment,
    is_commit,
    random_salt,
    replay,
)
from lrgp.constants import (
    CMD_ACCEPT,
    CMD_CHALLENGE,
    CMD_DECLINE,
    CMD_DRAW_ACCEPT,
    CMD_DRAW_DECLINE,
    CMD_DRAW_OFFER,
    CMD_MOVE,
    CMD_RESIGN,
    ENVELOPE_MAX_PACKED,
    STATUS_COMPLETED,
    STATUS_DECLINED,
    STATUS_EXPIRED,
)
from lrgp.envelope import pack_envelope, pack_lxmf_fields, unpack_envelope, validate_envelope_size
from lrgp.errors import InvalidEnvelope, OutgoingActionError
from lrgp.router import LrgpRouter

VECTORS_DIR = os.path.join(os.path.dirname(__file__), "vectors")
sys.path.insert(0, VECTORS_DIR)
import generate_rps  # noqa: E402

SID = "0123456789abcdef"
ALICE = "a" * 32
BOB = "b" * 32
SALT = "00112233445566778899aabbccddeeff"


def other(identity):
    return BOB if identity == ALICE else ALICE


def meta(app, identity, session_id=SID):
    return app._get_session(session_id, identity).metadata


def setup(challenger_leads=True):
    """Alice challenges and Bob accepts; the coin picks the leader."""
    rps.force_coin(challenger_leads)
    try:
        app = RpsApp()
        challenge, _ = app.handle_outgoing(SID, CMD_CHALLENGE, {}, ALICE)
        assert challenge == {}
        app.bind_peer(SID, ALICE, BOB)
        assert app.handle_incoming(SID, CMD_CHALLENGE, challenge, ALICE, BOB)["error"] is None
        accept, _ = app.handle_outgoing(SID, CMD_ACCEPT, {}, BOB)
        accepted = app.handle_incoming(SID, CMD_ACCEPT, accept, BOB, ALICE)
        assert accepted["error"] is None, accepted["error"]
    finally:
        rps.force_coin(None)
    return app


def choose(choice):
    return {"c": choice, "k": SALT}


def send(app, intent, mover):
    app.validate_outgoing(SID, CMD_MOVE, intent, mover)
    wire, fallback = app.handle_outgoing(SID, CMD_MOVE, intent, mover)
    assert wire, fallback
    result = app.handle_incoming(SID, CMD_MOVE, wire, mover, other(mover))
    assert result["error"] is None, result["error"]
    return wire


def refused(app, command, intent, identity):
    with pytest.raises(OutgoingActionError):
        app.validate_outgoing(SID, command, intent, identity)
    return True


def play(app, rounds):
    """Play rounds of (leader, follower) choices; the leader is Alice."""
    wires = [send(app, choose(rounds[0][0]), ALICE)]
    for index, (_, follow) in enumerate(rounds):
        wires.append(send(app, choose(follow), BOB))
        nxt = choose(rounds[index + 1][0]) if index + 1 < len(rounds) else {}
        wires.append(send(app, nxt, ALICE))
    return wires


def test_choices_and_commitments():
    assert beats("r", "s") is True
    assert beats("s", "r") is False
    assert beats("p", "p") is None
    assert commitment("r", SALT) == (
        "43cde9c5adb91a12b3311cc17a33f446dc379049bae82ad0ff5b391c13cd7a4a"
    )
    assert is_commit(commitment("r", SALT))
    assert commitment("r", SALT) != commitment("p", SALT)
    assert len(random_salt()) == 32


def test_a_match_to_three():
    app = setup()
    bob = meta(app, BOB)
    assert bob["phase"] == PHASE_COMMIT
    assert bob["turn"] == ALICE

    # Alice wins, ties, loses, wins, wins: 3 to 1.
    wires = play(app, [("r", "s"), ("p", "p"), ("s", "r"), ("p", "r"), ("s", "p")])
    assert wires[0] == {"h": commitment("r", SALT), "n": 0, "x": ""}
    assert wires[1] == {"c": "s", "n": 1, "x": ""}
    assert set(wires[2]) == {"c", "k", "h", "n", "x"}
    assert wires[-1] == {"c": "s", "k": SALT, "n": 10, "x": "win", "w": ALICE}

    for identity in (ALICE, BOB):
        session = app._get_session(SID, identity)
        assert session.status == STATUS_COMPLETED
        metadata = session.metadata
        assert metadata["winner"] == ALICE
        assert metadata["terminal_reason"] == R_ROUNDS
        assert metadata["black_score"] == 3
        assert metadata["white_score"] == 1
        assert metadata["rounds"][2] == ["s", "r"]
        assert len(metadata["rounds"]) == 5
        assert metadata["phase"] == ""
        RpsApp().upsert_session(session.to_dict())


def test_the_follower_can_win_and_the_last_reveal_is_empty():
    app = setup(challenger_leads=False)
    assert meta(app, ALICE)["turn"] == BOB
    assert meta(app, ALICE)["my_color"] == "w"
    send(app, choose("r"), BOB)
    assert meta(app, ALICE)["phase"] == PHASE_CHOOSE
    for round_index in range(3):
        send(app, {"c": "p"}, ALICE)
        ends = round_index == 2
        bob = meta(app, BOB)
        assert bob["phase"] == PHASE_REVEAL
        assert (bob["legal_moves"] == []) == ends
        if ends:
            assert refused(app, CMD_MOVE, choose("r"), BOB)
            last = send(app, {}, BOB)
            assert last["w"] == ALICE
            assert set(last) == {"c", "k", "n", "x", "w"}
        else:
            assert refused(app, CMD_MOVE, {}, BOB)
            send(app, choose("r"), BOB)
    assert app._get_session(SID, ALICE).status == STATUS_COMPLETED
    assert meta(app, ALICE)["white_score"] == 3


def test_a_salt_is_picked_when_none_is_given():
    app = setup()
    wire = send(app, {"c": "r"}, ALICE)
    alice = meta(app, ALICE)
    assert alice["my_choice"] == "r"
    assert commitment("r", alice["my_salt"]) == wire["h"]
    # The follower never learns the leader's choice before choosing.
    assert meta(app, BOB)["my_choice"] == ""


def test_forged_moves_are_rejected_without_mutation():
    app = setup()
    send(app, choose("r"), ALICE)
    send(app, choose("s"), BOB)
    before = copy.deepcopy(meta(app, BOB))
    following = commitment("p", SALT)
    cases = [
        # Revealing a different choice from the one committed, or the wrong salt.
        {"c": "p", "k": SALT, "h": following, "n": 2, "x": ""},
        {"c": "r", "k": "0" * 32, "h": following, "n": 2, "x": ""},
        # No commitment for the next round, or a false win.
        {"c": "r", "k": SALT, "n": 2, "x": ""},
        {"c": "r", "k": SALT, "n": 2, "x": "win", "w": ALICE},
        # Wrong ply, bad hash, extra key, wrong types, bad marker.
        {"c": "r", "k": SALT, "h": following, "n": 3, "x": ""},
        {"c": "r", "k": SALT, "h": "zz", "n": 2, "x": ""},
        {"c": "r", "k": SALT, "h": following, "n": 2, "x": "", "z": ""},
        {"c": 1, "k": SALT, "h": following, "n": 2, "x": ""},
        {"c": "r", "k": SALT, "h": following, "n": True, "x": ""},
        {"c": "r", "k": SALT, "h": following, "n": 2, "x": "draw"},
        {"c": "x", "k": SALT, "h": following, "n": 2, "x": ""},
    ]
    for payload in cases:
        assert app.handle_incoming(SID, CMD_MOVE, payload, ALICE, BOB)["error"], payload
    # Bob choosing out of turn.
    assert app.handle_incoming(SID, CMD_MOVE, {"c": "r", "n": 2, "x": ""}, BOB, ALICE)["error"]
    assert meta(app, BOB) == before

    for bad in ({}, choose("x"), {"c": "r", "z": ""}, {"c": "r", "k": "xyz"}, "r"):
        assert refused(app, CMD_MOVE, bad, ALICE), bad
    assert app.handle_outgoing(SID, CMD_MOVE, {}, ALICE)[0] == {}
    assert refused(app, CMD_MOVE, {"c": "r"}, BOB)
    assert app.handle_incoming("2222222222222222", CMD_CHALLENGE, choose("r"), ALICE, BOB)["error"]


def test_replay_checks_every_ply():
    app = setup()
    send(app, choose("r"), ALICE)
    send(app, choose("s"), BOB)
    session = app._get_session(SID, BOB)
    good = copy.deepcopy(session.metadata["plies"])
    for plies, message in [
        ([{"c": "r"}], "first ply"),
        ([good[0], {"c": "r", "k": SALT}], "exactly one choice"),
        ([good[0], good[1], {"c": "r"}], "must carry"),
        ([good[0], good[1], {"c": "q", "k": SALT}], "invalid choice"),
        ([good[0], good[1], {"c": "r", "k": SALT, "h": "00"}], "64 lowercase"),
        ("r", "array"),
        (["r"], "object"),
        ([{"h": 1}], "strings"),
    ]:
        session.metadata["plies"] = plies
        with pytest.raises(ValueError, match=message):
            replay(session)
    # A reveal that ends the match must not commit again, and nothing follows it.
    session.metadata["plies"] = good
    session.metadata["first_turn"] = "c" * 32
    with pytest.raises(ValueError, match="bound participant"):
        replay(session)


def test_the_end_of_the_match_is_checked():
    app = setup()
    wires = play(app, [("r", "s"), ("r", "s"), ("r", "s")])
    session = app._get_session(SID, BOB)
    plies = session.metadata["plies"]
    session.metadata["plies"] = plies + [{"c": "r"}]
    with pytest.raises(ValueError, match="end of the match"):
        replay(session)
    session.metadata["plies"] = plies[:-1] + [dict(plies[-1], h=commitment("r", SALT))]
    with pytest.raises(ValueError, match="must not commit"):
        replay(session)
    assert wires[-1]["w"] == ALICE


def test_resign_draw_and_decline():
    app = setup()
    app.handle_outgoing(SID, CMD_DRAW_OFFER, {}, BOB)
    app.handle_incoming(SID, CMD_DRAW_OFFER, {}, BOB, ALICE)
    assert refused(app, CMD_DRAW_ACCEPT, {}, BOB)
    app.handle_outgoing(SID, CMD_DRAW_DECLINE, {}, ALICE)
    app.handle_incoming(SID, CMD_DRAW_DECLINE, {}, ALICE, BOB)
    assert meta(app, BOB)["draw_offered"] is False
    send(app, choose("r"), ALICE)

    app.handle_outgoing(SID, CMD_RESIGN, {}, BOB)
    app.handle_incoming(SID, CMD_RESIGN, {}, BOB, ALICE)
    for identity in (ALICE, BOB):
        session = app._get_session(SID, identity)
        assert session.metadata["winner"] == ALICE
        assert session.metadata["phase"] == ""
        RpsApp().upsert_session(session.to_dict())

    app = setup()
    app.handle_outgoing(SID, CMD_DRAW_OFFER, {}, ALICE)
    app.handle_incoming(SID, CMD_DRAW_OFFER, {}, ALICE, BOB)
    app.handle_outgoing(SID, CMD_DRAW_ACCEPT, {}, BOB)
    app.handle_incoming(SID, CMD_DRAW_ACCEPT, {}, BOB, ALICE)
    for identity in (ALICE, BOB):
        session = app._get_session(SID, identity)
        assert session.metadata["terminal"] == "draw"
        RpsApp().upsert_session(session.to_dict())

    fresh = RpsApp()
    fresh.handle_outgoing(SID, CMD_CHALLENGE, {}, ALICE)
    fresh.handle_incoming(SID, CMD_CHALLENGE, {}, ALICE, BOB)
    fresh.handle_outgoing(SID, CMD_DECLINE, {}, BOB)
    fresh.bind_peer(SID, ALICE, BOB)
    fresh.handle_incoming(SID, CMD_DECLINE, {}, BOB, ALICE)
    for identity in (ALICE, BOB):
        session = fresh._get_session(SID, identity)
        assert session.status == STATUS_DECLINED
        RpsApp().upsert_session(session.to_dict())
    fresh.handle_outgoing("3333333333333333", CMD_CHALLENGE, {}, ALICE)
    assert fresh.handle_outgoing("3333333333333333", CMD_DECLINE, {}, ALICE)[1] == (
        "[LRGP Rock Paper Scissors] Challenge cancelled"
    )


def test_hydration_keeps_the_secret_and_rejects_tampering():
    app = setup()
    send(app, choose("r"), ALICE)
    send(app, choose("s"), BOB)
    for identity in (ALICE, BOB):
        record = app._get_session(SID, identity).to_dict()
        restored = RpsApp().upsert_session(copy.deepcopy(record))
        assert restored.metadata == record["metadata"]

    record = app._get_session(SID, ALICE).to_dict()
    assert record["metadata"]["my_choice"] == "r"
    for key, value in [
        ("my_choice", "p"),
        ("my_salt", "0" * 32),
        ("turn", BOB),
        ("my_color", "w"),
        ("first_turn", "c" * 32),
        ("move_count", 1),
        ("move_count", True),
        ("plies", ["r"]),
        ("terminal", "win"),
        ("phase", 3),
    ]:
        tampered = copy.deepcopy(record)
        tampered["metadata"][key] = value
        with pytest.raises(InvalidEnvelope):
            RpsApp().upsert_session(tampered)

    for status, key, value in [
        ("pending", "my_choice", "r"),
        ("completed", "terminal", "win"),
        ("completed", "terminal", "loss"),
        ("archived", "terminal", ""),
    ]:
        tampered = copy.deepcopy(record)
        tampered["status"] = status
        tampered["metadata"][key] = value
        with pytest.raises(InvalidEnvelope):
            RpsApp().upsert_session(tampered)
    offered = copy.deepcopy(record)
    offered["metadata"].update({"draw_offered": True, "draw_offered_by": "c" * 32})
    with pytest.raises(InvalidEnvelope):
        RpsApp().upsert_session(offered)
    broken = copy.deepcopy(record)
    broken["metadata"] = []
    with pytest.raises(InvalidEnvelope):
        RpsApp().upsert_session(broken)

    expired = copy.deepcopy(record)
    expired["last_action_at"] = 0.0
    assert RpsApp().upsert_session(expired).status == STATUS_EXPIRED


def test_manifest_and_fallback():
    app = RpsApp()
    manifest = app.get_manifest()
    assert manifest["app_id"] == "rps"
    assert manifest["session_type"] == "round_based"
    prefix = "[LRGP Rock Paper Scissors]"
    assert app.render_fallback(CMD_MOVE, {"h": "00"}) == prefix + " Chose in secret"
    assert app.render_fallback(CMD_MOVE, {"c": "r"}) == prefix + " Chose rock"
    assert app.render_fallback(CMD_MOVE, choose("p")) == prefix + " Showed paper and chose again"
    assert app.render_fallback(CMD_MOVE, {"c": "s", "x": "win"}) == (
        prefix + " Showed scissors (match over)"
    )
    assert app.render_fallback(CMD_RESIGN, {}) == prefix + " Resigned."
    router = LrgpRouter()
    import lrgp.apps

    router.discover(lrgp.apps)
    assert isinstance(router.get_app("rps"), RpsApp)


def test_worst_case_envelope_fits_the_budget():
    payload = {"c": "s", "k": SALT, "h": commitment("s", SALT), "n": 65535, "x": ""}
    size = validate_envelope_size(pack_envelope("rps", 1, CMD_MOVE, SID, payload))
    assert size <= 190 <= ENVELOPE_MAX_PACKED


# --- Cross-language vectors ------------------------------------------------


class TestVectors:
    DIGESTS = {
        "rps_accept.bin": "f550cb15dae7fc9915f83272d282d2df31f5340ede7b9b8b069909b40458b6e8",
        "rps_challenge.bin": "735c399b6aaf67658f8bdd107863291afd9d1bc69ef60a2435379f89750fb835",
        "rps_choose.bin": "92016ed951b4eafa1556ca1abcf872d0f50630d954570c8894e74542660f65e8",
        "rps_move.bin": "83816e965cfcebbc89deebc74118f8d67994f4f7aadea302caf44dad98717e05",
        "rps_move_win.bin": "fd89c42b6212e8a76b0741a6caf6956104c73e8640e773597ab2492eca08eec1",
        "rps_reveal.bin": "1ab69329c31ab8c44c58f2326a7cd7cf19f994337ec9d4fed20e91ddaa1cbd50",
    }

    @staticmethod
    def _raw(name):
        with open(os.path.join(VECTORS_DIR, name), "rb") as vector:
            return vector.read()

    @pytest.mark.parametrize("filename", sorted(DIGESTS))
    def test_fixture_bytes_have_locked_cross_language_digest(self, filename):
        assert hashlib.sha256(self._raw(filename)).hexdigest() == self.DIGESTS[filename]

    def test_generator_reproduces_every_fixture(self):
        vectors = generate_rps.vectors()
        assert set(vectors) == set(self.DIGESTS)
        for filename, (command, payload, nonce) in vectors.items():
            envelope = pack_envelope("rps", 1, command, SID, payload, nonce=nonce)
            assert packb(envelope) == self._raw(filename)
            decoded = unpack_envelope(pack_lxmf_fields(unpackb(self._raw(filename))))
            assert decoded["p"] == payload
            assert RpsApp()._incoming_payload_error(command, payload) is None

    def test_reference_match_contents(self):
        wires = generate_rps.play_reference_game()
        assert len(wires) == 2 * len(generate_rps.ROUNDS) + 1
        win = wires[-1]
        assert win["w"] == generate_rps.RESPONDER
        assert win["k"] == generate_rps.salt(len(generate_rps.ROUNDS) - 1)
        assert wires[0]["h"] == commitment("r", generate_rps.salt(0))
