"""Pente rules; the shared lifecycle, hydration, and vector tests in
``test_placement.py`` cover the app."""

import pytest

from lrgp.apps._placement import BLACK, EMPTY, WHITE, parse_square, play_checked, replay
from lrgp.apps.pente import R_CAPTURES, R_FIVE, SIZE, PenteRules


def sq(name):
    return parse_square(name, SIZE)


def test_notation_is_strict():
    assert PenteRules.is_canonical("a1")
    assert PenteRules.is_canonical("s19")
    for bad in ["", "t1", "a20", "a01", "J10", "pass", None]:
        assert not PenteRules.is_canonical(bad), bad


def test_flanking_a_pair_captures_it():
    # Black j10, White k10 l10, Black m10 captures both.
    position = replay(PenteRules, ["j10", "k10", "a1", "l10", "m10"])
    assert position.cells[sq("k10")] == EMPTY
    assert position.cells[sq("l10")] == EMPTY
    assert position.captures == [1, 0]
    # Diagonally too.
    assert replay(PenteRules, ["e5", "f6", "a1", "g7", "h8"]).captures == [1, 0]


def test_one_or_three_stones_are_not_captured_and_moving_in_is_safe():
    position = replay(PenteRules, ["j10", "k10", "a1", "l10", "a2", "m10", "n10"])
    assert position.captures == [0, 0], "three in a row is safe"
    # White's l10 completes W W between Black stones, which is safe.
    position = replay(PenteRules, ["j10", "k10", "m10", "l10"])
    assert position.cells[sq("l10")] == WHITE
    assert position.captures == [0, 0]


def test_five_in_a_row_or_five_pairs_wins():
    position = replay(PenteRules, ["a1", "a19", "b1", "b19", "c1", "c19", "d1", "d19", "e1"])
    assert PenteRules.outcome(position) == ("win", BLACK)
    assert PenteRules.reason(position) == R_FIVE

    position = PenteRules.start()
    position.captures = [4, 0]
    position.cells[sq("j10")] = BLACK
    position.cells[sq("k10")] = WHITE
    position.cells[sq("l10")] = WHITE
    play_checked(PenteRules, position, "m10")
    assert PenteRules.outcome(position) == ("win", BLACK)
    assert PenteRules.reason(position) == R_CAPTURES
    assert PenteRules.legal_moves(position) == []


def test_occupied_squares_are_illegal():
    with pytest.raises(ValueError, match="occupied"):
        play_checked(PenteRules, replay(PenteRules, ["j10"]), "j10")


def test_metadata_counts_captured_pairs():
    metadata = {}
    PenteRules.extra_metadata(replay(PenteRules, ["j10", "k10", "a1", "l10", "m10"]), metadata)
    assert metadata == {"black_captures": 1, "white_captures": 0}
