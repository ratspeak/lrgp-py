"""Battleship: fleets, commitments, play, hydration, and shared vectors."""

import copy
import hashlib
import os
import sys

import pytest

from lrgp._msgpack import packb, unpackb
from lrgp.apps import battleship
from lrgp.apps.battleship import (
    CELLS,
    FLEET,
    HIT,
    MISS,
    PHASE_CONCEDE,
    PHASE_REVEAL,
    PHASE_SHOOT,
    R_SUNK,
    SUNK,
    BattleshipApp,
    commitment,
    is_commit,
    is_salt,
    parse_fleet,
    random_fleet,
    random_salt,
    replay,
)
from lrgp.constants import (
    CMD_ACCEPT,
    CMD_CHALLENGE,
    CMD_DECLINE,
    CMD_DRAW_ACCEPT,
    CMD_DRAW_OFFER,
    CMD_MOVE,
    CMD_RESIGN,
    ENVELOPE_MAX_PACKED,
    STATUS_ACTIVE,
    STATUS_COMPLETED,
    STATUS_DECLINED,
    STATUS_EXPIRED,
)
from lrgp.envelope import pack_envelope, pack_lxmf_fields, unpack_envelope, validate_envelope_size
from lrgp.errors import InvalidEnvelope, OutgoingActionError
from lrgp.router import LrgpRouter

VECTORS_DIR = os.path.join(os.path.dirname(__file__), "vectors")
sys.path.insert(0, VECTORS_DIR)
import generate_battleship  # noqa: E402

SID = "0123456789abcdef"
ALICE = "a" * 32
BOB = "b" * 32
ALICE_FLEET = "a1h a2h a3h a4h a5h"
BOB_FLEET = "a10h a9h a8h a7h a6h"
ALICE_SALT = "00112233445566778899aabbccddeeff"
BOB_SALT = "ffeeddccbbaa99887766554433221100"

# Bob's ship squares, ship by ship, so each ship sinks on its last square.
BOB_SQUARES = [
    "a10", "b10", "c10", "d10", "e10", "a9", "b9", "c9", "d9", "a8", "b8", "c8",
    "a7", "b7", "c7", "a6", "b6",
]
# Bob's shots, all misses on Alice's fleet.
BOB_MISSES = [
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "g1", "g2", "g3",
    "g4", "g5", "g6",
]


def other(identity):
    return BOB if identity == ALICE else ALICE


def meta(app, identity, session_id=SID):
    return app._get_session(session_id, identity).metadata


def setup(challenger_first=True):
    """Alice challenges with her fleet and Bob accepts with his."""
    battleship.force_coin(challenger_first)
    try:
        app = BattleshipApp()
        challenge, _ = app.handle_outgoing(
            SID, CMD_CHALLENGE, {"f": ALICE_FLEET, "k": ALICE_SALT}, ALICE
        )
        assert challenge == {
            "h": "c64fbc70ca15a8f7ab30eb4855d65df26150d34d7016b6a1e44f57396e0a8aeb"
        }
        app.bind_peer(SID, ALICE, BOB)
        received = app.handle_incoming(SID, CMD_CHALLENGE, challenge, ALICE, BOB)
        assert received["error"] is None
        app.validate_outgoing(SID, CMD_ACCEPT, {"f": BOB_FLEET, "k": BOB_SALT}, BOB)
        accept, _ = app.handle_outgoing(SID, CMD_ACCEPT, {"f": BOB_FLEET, "k": BOB_SALT}, BOB)
        assert set(accept) == {"b", "h"}
        accepted = app.handle_incoming(SID, CMD_ACCEPT, accept, BOB, ALICE)
        assert accepted["error"] is None, accepted["error"]
    finally:
        battleship.force_coin(None)
    return app


def send(app, intent, mover):
    app.validate_outgoing(SID, CMD_MOVE, intent, mover)
    wire, fallback = app.handle_outgoing(SID, CMD_MOVE, intent, mover)
    assert wire, fallback
    result = app.handle_incoming(SID, CMD_MOVE, wire, mover, other(mover))
    assert result["error"] is None, result["error"]
    return wire


def refused(app, command, intent, identity, session_id=SID):
    with pytest.raises(Exception):
        app.validate_outgoing(session_id, command, intent, identity)
    return True


def play_to_concession(app):
    """Play Alice's winning game up to Bob's concession."""
    wires = []
    for index, square in enumerate(BOB_SQUARES):
        wires.append(send(app, {"s": square}, ALICE))
        if index < len(BOB_MISSES):
            wires.append(send(app, {"s": BOB_MISSES[index]}, BOB))
    assert meta(app, BOB)["phase"] == PHASE_CONCEDE
    wires.append(send(app, {}, BOB))
    return wires


def test_fleets_are_strict():
    ships = parse_fleet(ALICE_FLEET)
    assert [len(ship) for ship in ships] == list(FLEET)
    assert ships[0] == [0, 1, 2, 3, 4]
    assert parse_fleet("a1v b1v c1v d1v e1v")[4] == [4, 14]
    for bad in [
        "",
        "a1h a2h a3h a4h",
        "a1h a2h a3h a4h a5h a6h",
        "a1h  a2h a3h a4h a5h",
        "a1h a1v a3h a4h a5h",  # overlap
        "g1h a2h a3h a4h a5h",  # off the board
        "a7v a2h a3h a4h a5h",  # carrier from a7 up is off the board
        "a1x a2h a3h a4h a5h",
        "A1h a2h a3h a4h a5h",
        "a1h a2h a3h a4h a5h ",
        None,
    ]:
        assert parse_fleet(bad) is None, bad
    for _ in range(50):
        assert parse_fleet(random_fleet()) is not None
    assert is_salt(ALICE_SALT)
    assert not is_salt(ALICE_SALT.upper())
    assert is_salt(random_salt())
    assert is_commit(commitment(ALICE_FLEET, ALICE_SALT))


def test_a_whole_game_reveals_both_fleets():
    app = setup()
    for identity in (ALICE, BOB):
        metadata = meta(app, identity)
        assert metadata["turn"] == ALICE
        assert metadata["first_turn"] == ALICE
        assert metadata["phase"] == PHASE_SHOOT
        assert len(metadata["legal_moves"]) == CELLS
    first = send(app, {"s": "a10"}, ALICE)
    assert first == {"s": "a10", "a": "", "n": 0, "x": ""}
    assert send(app, {"s": "f1"}, BOB)["a"] == HIT

    app = setup()
    wires = play_to_concession(app)
    # Bob's answers: hits, with a sinking on each ship's last square.
    answers = [wire["a"] for wire in wires[1::2]]
    assert answers.count(SUNK) == 5
    assert MISS not in answers

    concession = wires[-1]
    assert set(concession) == {"a", "f", "k", "n", "x"}
    assert concession["f"] == BOB_FLEET
    assert concession["n"] == 33
    alice = meta(app, ALICE)
    assert alice["phase"] == PHASE_REVEAL
    assert alice["their_fleet"] == BOB_FLEET
    assert alice["their_sunk"] == 5
    assert app._get_session(SID, ALICE).status == STATUS_ACTIVE

    last = send(app, {}, ALICE)
    assert set(last) == {"f", "k", "n", "x", "w"}
    assert last["w"] == ALICE
    for identity in (ALICE, BOB):
        session = app._get_session(SID, identity)
        assert session.status == STATUS_COMPLETED
        assert session.metadata["winner"] == ALICE
        assert session.metadata["terminal_reason"] == R_SUNK
        assert session.metadata["turn"] == ""
        BattleshipApp().upsert_session(session.to_dict())
    bob = meta(app, BOB)
    assert bob["their_fleet"] == ALICE_FLEET
    assert bob["my_sunk"] == 5
    assert bob["my_board"].startswith("xxxxx_____xxxx______xxx_______xxx_______xx________")
    assert refused(app, CMD_MOVE, {"s": "j1"}, BOB)


def test_the_coin_can_let_the_responder_shoot_first():
    app = setup(challenger_first=False)
    metadata = meta(app, ALICE)
    assert metadata["turn"] == BOB
    assert metadata["my_color"] == "w"
    assert refused(app, CMD_MOVE, {"s": "a1"}, ALICE)
    send(app, {"s": "a1"}, BOB)
    assert send(app, {"s": "a10"}, ALICE)["a"] == HIT
    assert meta(app, BOB)["their_board"][90] == "x"
    assert meta(app, BOB)["my_board"][0] == "x"


def test_bad_intents_are_refused_before_anything_is_sent():
    app = setup()
    for bad in ({}, {"s": "k1"}, {"s": "a0"}, {"f": ALICE_FLEET, "k": ALICE_SALT}, {"s": 3}):
        assert refused(app, CMD_MOVE, bad, ALICE), bad
        assert app.handle_outgoing(SID, CMD_MOVE, bad, ALICE)[0] == {}
    assert refused(app, CMD_MOVE, {"s": "a1"}, BOB)
    send(app, {"s": "a1"}, ALICE)
    send(app, {"s": "j1"}, BOB)
    with pytest.raises(OutgoingActionError, match="already"):
        app.validate_outgoing(SID, CMD_MOVE, {"s": "a1"}, ALICE)

    for bad in (
        {"f": "a1h", "k": ALICE_SALT},
        {"f": ALICE_FLEET, "k": "xyz"},
        {"k": ALICE_SALT},
        {"z": ""},
    ):
        with pytest.raises(OutgoingActionError):
            app.validate_outgoing("1111111111111111", CMD_CHALLENGE, bad, ALICE, BOB)
        assert app.handle_outgoing("1111111111111111", CMD_CHALLENGE, bad, ALICE)[0] == {}
    # A challenge with no fleet picks one at random.
    random_challenge, _ = app.handle_outgoing("2222222222222222", CMD_CHALLENGE, {}, ALICE)
    assert is_commit(random_challenge["h"])
    assert parse_fleet(meta(app, ALICE, "2222222222222222")["my_fleet"]) is not None


def test_forged_moves_are_rejected_without_mutation():
    app = setup()
    before = copy.deepcopy(meta(app, BOB))
    cases = [
        {"s": "a1", "a": "", "n": 1, "x": ""},
        {"s": "a1", "a": "h", "n": 0, "x": ""},
        {"s": "k1", "a": "", "n": 0, "x": ""},
        {"s": "a1", "a": "", "n": 0, "x": "draw"},
        {"s": "a1", "n": 0, "x": ""},
        {"s": "a1", "a": "", "n": 0, "x": "", "z": ""},
        {"s": "a1", "a": "", "n": True, "x": ""},
        # Conceding at once, or revealing with nothing to answer.
        {"a": "s", "f": ALICE_FLEET, "k": ALICE_SALT, "n": 0, "x": ""},
        {"f": ALICE_FLEET, "k": ALICE_SALT, "n": 0, "x": "win", "w": ALICE},
    ]
    for payload in cases:
        assert app.handle_incoming(SID, CMD_MOVE, payload, ALICE, BOB)["error"], payload
    # Bob moving out of turn.
    bob_shot = {"s": "a1", "a": "", "n": 0, "x": ""}
    assert app.handle_incoming(SID, CMD_MOVE, bob_shot, BOB, ALICE)["error"]
    assert meta(app, BOB) == before

    # Junk challenges create nothing.
    for junk in ({}, {"h": "00"}):
        assert app.handle_incoming("3333333333333333", CMD_CHALLENGE, junk, ALICE, BOB)["error"]
    assert app._get_session("3333333333333333", BOB) is None


def test_false_answers_are_caught_by_the_reveal():
    app = setup()
    send(app, {"s": "a10"}, ALICE)
    # Bob claims a miss on Alice's a10 shot, which hit his carrier.
    lie = {"s": "f1", "a": MISS, "n": 1, "x": ""}
    received = app.handle_incoming(SID, CMD_MOVE, lie, BOB, ALICE)
    assert received["error"] is None, "a lie can't be seen before the reveal"

    # Once Bob's fleet is revealed, the lie no longer replays.
    session = app._get_session(SID, ALICE)
    session.metadata["their_fleet"] = BOB_FLEET
    session.metadata["their_salt"] = BOB_SALT
    with pytest.raises(ValueError, match="false"):
        replay(session)
    # A reveal that doesn't match the commitment is refused.
    session.metadata["their_fleet"] = ALICE_FLEET
    with pytest.raises(ValueError, match="commitment"):
        replay(session)


def test_a_concession_must_be_true():
    app = setup()
    send(app, {"s": "a10"}, ALICE)
    concede = {"a": SUNK, "f": BOB_FLEET, "k": BOB_SALT, "n": 1, "x": ""}
    assert app.handle_incoming(SID, CMD_MOVE, concede, BOB, ALICE)["error"]
    # And Bob's own client offers only shots while his fleet floats.
    assert refused(app, CMD_MOVE, {}, BOB)


def test_resign_draw_and_decline():
    app = setup()
    app.handle_outgoing(SID, CMD_RESIGN, {}, ALICE)
    app.handle_incoming(SID, CMD_RESIGN, {}, ALICE, BOB)
    for identity in (ALICE, BOB):
        session = app._get_session(SID, identity)
        assert session.status == STATUS_COMPLETED
        assert session.metadata["winner"] == BOB
        assert session.metadata["terminal_reason"] == "rsn"
        assert session.metadata["phase"] == ""
        BattleshipApp().upsert_session(session.to_dict())

    app = setup()
    app.handle_outgoing(SID, CMD_DRAW_OFFER, {}, ALICE)
    app.handle_incoming(SID, CMD_DRAW_OFFER, {}, ALICE, BOB)
    assert refused(app, CMD_DRAW_ACCEPT, {}, ALICE)
    assert app.handle_incoming(SID, CMD_DRAW_OFFER, {}, ALICE, BOB)["error"]
    app.validate_outgoing(SID, CMD_DRAW_ACCEPT, {}, BOB)
    app.handle_outgoing(SID, CMD_DRAW_ACCEPT, {}, BOB)
    app.handle_incoming(SID, CMD_DRAW_ACCEPT, {}, BOB, ALICE)
    for identity in (ALICE, BOB):
        session = app._get_session(SID, identity)
        assert session.metadata["terminal"] == "draw"
        BattleshipApp().upsert_session(session.to_dict())

    fresh = BattleshipApp()
    challenge, _ = fresh.handle_outgoing(SID, CMD_CHALLENGE, {}, ALICE)
    fresh.handle_incoming(SID, CMD_CHALLENGE, challenge, ALICE, BOB)
    assert fresh.handle_outgoing(SID, CMD_DECLINE, {}, BOB)[1] == (
        "[LRGP Battleship] Challenge declined"
    )
    fresh.bind_peer(SID, ALICE, BOB)
    fresh.handle_incoming(SID, CMD_DECLINE, {}, BOB, ALICE)
    for identity in (ALICE, BOB):
        session = fresh._get_session(SID, identity)
        assert session.status == STATUS_DECLINED
        BattleshipApp().upsert_session(session.to_dict())
    assert fresh.handle_outgoing("4444444444444444", CMD_CHALLENGE, {}, ALICE)[0]
    assert fresh.handle_outgoing("4444444444444444", CMD_DECLINE, {}, ALICE)[1] == (
        "[LRGP Battleship] Challenge cancelled"
    )


def test_hydration_replays_and_rejects_tampering():
    app = setup()
    for square in ("a10", "f1", "b10"):
        send(app, {"s": square}, meta(app, ALICE)["turn"])
    for identity in (ALICE, BOB):
        record = app._get_session(SID, identity).to_dict()
        restored = BattleshipApp().upsert_session(copy.deepcopy(record))
        assert restored.metadata == record["metadata"]

    record = app._get_session(SID, BOB).to_dict()
    for key, value in [
        ("my_fleet", ALICE_FLEET),
        ("my_salt", ALICE_SALT),
        ("turn", ALICE),
        ("my_color", "b"),
        ("move_count", 1),
        ("move_count", True),
        ("my_board", "_" * CELLS),
        ("answers", ["", "m", "m"]),
        ("shots", ["a10", "f1", "a10"]),
        ("shots", "a10"),
        ("their_commit", ""),
        ("their_commit", "zz"),
        ("terminal", "win"),
        ("phase", 3),
    ]:
        tampered = copy.deepcopy(record)
        tampered["metadata"][key] = value
        with pytest.raises(InvalidEnvelope):
            BattleshipApp().upsert_session(tampered)

    bad = copy.deepcopy(record)
    bad["app_id"] = "chess"
    with pytest.raises(InvalidEnvelope):
        BattleshipApp().upsert_session(bad)
    expired = copy.deepcopy(record)
    expired["last_action_at"] = 0.0
    restored = BattleshipApp().upsert_session(expired)
    assert restored.status == STATUS_EXPIRED


def test_pending_records_need_their_commitment():
    app = BattleshipApp()
    app.handle_outgoing(SID, CMD_CHALLENGE, {"f": ALICE_FLEET, "k": ALICE_SALT}, ALICE)
    record = app._get_session(SID, ALICE).to_dict()
    BattleshipApp().upsert_session(copy.deepcopy(record))
    record["metadata"].update({"my_fleet": "", "my_commit": ""})
    with pytest.raises(InvalidEnvelope):
        BattleshipApp().upsert_session(record)


def test_manifest_and_fallback():
    app = BattleshipApp()
    manifest = app.get_manifest()
    assert manifest["app_id"] == "battleship"
    assert manifest["session_type"] == "turn_based"
    assert manifest["preferred_delivery"][CMD_RESIGN] == "direct"
    assert app.render_fallback(CMD_MOVE, {"s": "e5"}) == "[LRGP Battleship] Fired at e5"
    assert app.render_fallback(CMD_MOVE, {"x": "win"}) == (
        "[LRGP Battleship] Fleet revealed (game won)"
    )
    assert app.render_fallback(CMD_MOVE, {}) == "[LRGP Battleship] Fleet sunk"
    router = LrgpRouter()
    import lrgp.apps

    router.discover(lrgp.apps)
    assert isinstance(router.get_app("battleship"), BattleshipApp)


def test_worst_case_envelopes_fit_the_budget():
    fleet = "a10h a9h a8h a7h a6h"
    payload = {"f": fleet, "k": BOB_SALT, "n": 199, "x": "win", "w": ALICE}
    size = validate_envelope_size(pack_envelope("battleship", 1, CMD_MOVE, SID, payload))
    assert size <= 180 <= ENVELOPE_MAX_PACKED
    accept = {"b": ALICE, "h": commitment(fleet, BOB_SALT)}
    validate_envelope_size(pack_envelope("battleship", 1, CMD_ACCEPT, SID, accept))


# --- Cross-language vectors ------------------------------------------------


class TestVectors:
    DIGESTS = {
        "battleship_accept.bin": "b8d0c9e568dcfe14e672969cc1f60d54823baf2d08e225342a20ad82290f482e",
        "battleship_challenge.bin": "ada47bf68f9d8f04c48383c8d079d83bd94d56d2bda50c7e7b36ea1a8072199e",
        "battleship_concede.bin": "83db2ac6666fe2dc935d929ed3d80f36a7e15362be42ae71599e6dba1906b6e3",
        "battleship_move.bin": "7f1252b4a597bd9692d074f21ff36c2d47f40793b91338a5eb0e12e91a3b6733",
        "battleship_move_win.bin": "46c04d230816371abb18134ee4585f676f22427ae28799fc91c070897d0d2691",
        "battleship_reference.txt": "151d31fc3310cf6da8fedcd28a50460c2264db45dfc52c3928246d67a72a945e",
    }

    @staticmethod
    def _raw(name):
        with open(os.path.join(VECTORS_DIR, name), "rb") as vector:
            return vector.read()

    @pytest.mark.parametrize("filename", sorted(DIGESTS))
    def test_fixture_bytes_have_locked_cross_language_digest(self, filename):
        assert hashlib.sha256(self._raw(filename)).hexdigest() == self.DIGESTS[filename]

    def test_generator_reproduces_every_fixture(self):
        vectors = generate_battleship.vectors()
        assert set(vectors) | {"battleship_reference.txt"} == set(self.DIGESTS)
        for filename, (command, payload, nonce) in vectors.items():
            envelope = pack_envelope("battleship", 1, command, SID, payload, nonce=nonce)
            assert packb(envelope) == self._raw(filename)
            decoded = unpack_envelope(pack_lxmf_fields(unpackb(self._raw(filename))))
            assert decoded["p"] == payload
            assert BattleshipApp()._incoming_payload_error(command, payload) is None
        lines = generate_battleship.reference_lines(generate_battleship.play_reference_game())
        assert self._raw("battleship_reference.txt") == ("\n".join(lines) + "\n").encode("ascii")

    def test_reference_game_contents(self):
        lines = self._raw("battleship_reference.txt").decode("ascii").splitlines()
        assert len(lines) == 150
        assert lines[0].endswith(" -")
        assert lines[-2:] == ["- s", "- -"]
        answers = [line.split(" ")[1] for line in lines[1:-1]]
        assert {MISS, HIT, SUNK} <= set(answers)
        win = generate_battleship.vectors()["battleship_move_win.bin"][1]
        assert win["w"] == generate_battleship.RESPONDER
        assert win["f"] == generate_battleship.RESPONDER_FLEET
