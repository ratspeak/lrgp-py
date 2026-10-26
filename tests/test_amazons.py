"""Amazons rules; the shared lifecycle, hydration, and vector tests in
``test_placement.py`` cover the app."""

import pytest

from lrgp.apps._placement import BLACK, EMPTY, WHITE, parse_square, play_checked
from lrgp.apps.amazons import ARROW, CELLS, SIZE, AmazonsRules


def sq(name):
    return parse_square(name, SIZE)


def test_notation_is_strict():
    for good in ["d1d7/g7", "a10j10/a10", "a4a5/a4"]:
        assert AmazonsRules.is_canonical(good), good
    for bad in [
        "", "d1d7", "d1-d7/g7", "d1d7/", "d1d7/g7/", "d01d7/g7", "k1d7/g7", "D1d7/g7",
        "d1d11/g7", "/g7", "d/g7", None,
    ]:
        assert not AmazonsRules.is_canonical(bad), bad


def test_the_opening_has_2176_moves():
    legal = AmazonsRules.legal_moves(AmazonsRules.start())
    assert len(legal) == 2176
    assert legal == sorted(legal)
    assert "d1d7/g7" in legal
    # The arrow may land on the square the amazon left.
    assert "d1d2/d1" in legal


def test_amazons_and_arrows_cannot_pass_through_anything():
    position = AmazonsRules.start()
    play_checked(AmazonsRules, position, "d1d7/g7")
    assert position.cells[sq("g7")] == ARROW
    assert position.cells[sq("d1")] == EMPTY
    # White's g10 can't slide through the arrow on g7.
    for blocked in ["g10g6/g5", "d10d6/d5", "a7c7/i7"]:
        with pytest.raises(ValueError, match="cannot reach"):
            play_checked(AmazonsRules, _copy(position), blocked)
    play_checked(AmazonsRules, _copy(position), "g10g8/g9")
    # Not your amazon, not a queen line.
    with pytest.raises(ValueError, match="No amazon"):
        play_checked(AmazonsRules, _copy(position), "a4a5/a6")
    with pytest.raises(ValueError, match="cannot reach"):
        play_checked(AmazonsRules, position, "a7b9/b10")


def _copy(position):
    clone = AmazonsRules.start()
    clone.cells = list(position.cells)
    clone.ply = position.ply
    return clone


def test_the_player_left_without_a_move_loses():
    # Everything is burnt but White's a1, Black's c3, and a2 and b2.
    position = AmazonsRules.start()
    position.cells = [ARROW] * CELLS
    position.cells[sq("a1")] = WHITE
    position.cells[sq("c3")] = BLACK
    position.cells[sq("a2")] = EMPTY
    position.cells[sq("b2")] = EMPTY
    assert position.has_move(WHITE)
    assert AmazonsRules.legal_moves(position) == ["c3b2/a2", "c3b2/c3"]

    still_open = _copy(position)
    play_checked(AmazonsRules, still_open, "c3b2/c3")
    assert AmazonsRules.outcome(still_open) == ("", None), "a1 can still reach a2"

    play_checked(AmazonsRules, position, "c3b2/a2")
    assert AmazonsRules.outcome(position) == ("win", BLACK)
    assert AmazonsRules.legal_moves(position) == []
