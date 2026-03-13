"""Exhaustive wire budget verification for all TTT action types."""

import pytest
from lrgp._msgpack import packb
from lrgp.envelope import pack_envelope, validate_envelope_size, pack_lxmf_fields
from lrgp.constants import ENVELOPE_MAX_PACKED, OPPORTUNISTIC_MAX_CONTENT

SESSION_ID = "a1b2c3d4e5f6g7h8"
HASH_16 = "abcdef0123456789"  # 16-char hex hash


def _measure_full_content(envelope, fallback):
    """Measure full LXMF content size: [ts, title, content, fields]."""
    import time
    fields = pack_lxmf_fields(envelope)
    payload = [time.time(), "", fallback, fields]
    return len(packb(payload))


ENVELOPES = {
    "challenge": (
        pack_envelope("ttt", 1, "challenge", SESSION_ID, {}),
        "[LRGP TTT] Sent a challenge!",
    ),
    "accept": (
        pack_envelope("ttt", 1, "accept", SESSION_ID, {
            "b": "_________", "t": HASH_16,
        }),
        "[LRGP TTT] Challenge accepted",
    ),
    "decline": (
        pack_envelope("ttt", 1, "decline", SESSION_ID, {}),
        "[LRGP TTT] Challenge declined",
    ),
    "move_normal": (
        pack_envelope("ttt", 1, "move", SESSION_ID, {
            "i": 4, "b": "____X____", "n": 1, "t": HASH_16, "x": "",
        }),
        "[LRGP TTT] Move 1",
    ),
    "move_win": (
        pack_envelope("ttt", 1, "move", SESSION_ID, {
            "i": 2, "b": "XXX_OO___", "n": 5, "t": "", "x": "win",
            "w": HASH_16,
        }),
        "[LRGP TTT] X wins!",
    ),
    "move_draw": (
        pack_envelope("ttt", 1, "move", SESSION_ID, {
            "i": 8, "b": "XOXXOOOXX", "n": 9, "t": "", "x": "draw",
        }),
        "[LRGP TTT] Game drawn!",
    ),
    "resign": (
        pack_envelope("ttt", 1, "resign", SESSION_ID, {}),
        "[LRGP TTT] Resigned.",
    ),
    "draw_offer": (
        pack_envelope("ttt", 1, "draw_offer", SESSION_ID, {}),
        "[LRGP TTT] Offered a draw",
    ),
    "draw_accept": (
        pack_envelope("ttt", 1, "draw_accept", SESSION_ID, {}),
        "[LRGP TTT] Draw accepted",
    ),
    "draw_decline": (
        pack_envelope("ttt", 1, "draw_decline", SESSION_ID, {}),
        "[LRGP TTT] Draw declined",
    ),
    "error": (
        pack_envelope("ttt", 1, "error", SESSION_ID, {
            "code": "invalid_move", "msg": "Not your turn", "ref": "move",
        }),
        "[LRGP TTT] Error: Not your turn",
    ),
}


class TestEnvelopeSizes:
    @pytest.mark.parametrize("name", ENVELOPES.keys())
    def test_envelope_fits_budget(self, name):
        envelope, _ = ENVELOPES[name]
        size = len(packb(envelope))
        assert size <= ENVELOPE_MAX_PACKED, \
            "{}: envelope is {} bytes (max {})".format(name, size, ENVELOPE_MAX_PACKED)

    @pytest.mark.parametrize("name", ENVELOPES.keys())
    def test_full_content_fits_opportunistic(self, name):
        envelope, fallback = ENVELOPES[name]
        size = _measure_full_content(envelope, fallback)
        assert size <= OPPORTUNISTIC_MAX_CONTENT, \
            "{}: full content is {} bytes (max {})".format(
                name, size, OPPORTUNISTIC_MAX_CONTENT)

    @pytest.mark.parametrize("name", ENVELOPES.keys())
    def test_validate_envelope_size_passes(self, name):
        envelope, _ = ENVELOPES[name]
        validate_envelope_size(envelope)  # should not raise
