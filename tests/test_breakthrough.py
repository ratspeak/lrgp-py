"""Breakthrough rules; the shared lifecycle, hydration, and vector tests in
``test_placement.py`` cover the app."""

import pytest

from lrgp.apps._placement import BLACK, EMPTY, WHITE, parse_square, play_checked, replay
from lrgp.apps.breakthrough import CELLS, R_GOAL, R_PIECES, SIZE, BreakthroughRules


def sq(name):
    return parse_square(name, SIZE)


def setup(black, white):
    """Only the given pieces, and Black to move."""
    position = BreakthroughRules.start()
    position.cells = [EMPTY] * CELLS
    for name in black:
        position.cells[sq(name)] = BLACK
    for name in white:
        position.cells[sq(name)] = WHITE
    return position


def test_notation_is_strict():
    assert BreakthroughRules.is_canonical("a2b3")
    for bad in ["", "a2", "a2-b3", "a2b9", "i2b3", "A2b3", "a2b3 ", "a10b3", None]:
        assert not BreakthroughRules.is_canonical(bad), bad


def test_the_opening_has_twenty_two_steps():
    start = BreakthroughRules.start()
    legal = BreakthroughRules.legal_moves(start)
    assert len(legal) == 22
    assert legal[0] == "a2a3"
    assert "h2g3" in legal
    assert BreakthroughRules.render(start)[:16] == WHITE * 16


def test_captures_are_diagonal_only():
    position = setup(["d4"], ["d5", "e5", "a8"])
    assert BreakthroughRules.legal_moves(position) == ["d4c5", "d4e5"]
    play_checked(BreakthroughRules, position, "d4e5")
    assert position.cells.count(WHITE) == 2
    with pytest.raises(ValueError, match="Illegal"):
        play_checked(BreakthroughRules, setup(["d4"], ["d5"]), "d4d5")
    # Backwards is never allowed.
    with pytest.raises(ValueError, match="Illegal"):
        play_checked(BreakthroughRules, setup(["d4"], ["h8"]), "d4d3")


def test_reaching_the_far_rank_wins():
    position = setup(["c7"], ["h8", "a8"])
    play_checked(BreakthroughRules, position, "c7c8")
    assert BreakthroughRules.outcome(position) == ("win", BLACK)
    assert BreakthroughRules.reason(position) == R_GOAL
    assert BreakthroughRules.legal_moves(position) == []

    # White heads for rank 1.
    position = replay(BreakthroughRules, ["a2a3", "h7h6"])
    assert BreakthroughRules.side_to_move(position) == BLACK
    assert "a3a4" in BreakthroughRules.legal_moves(position)
    white = setup(["a1", "h4"], ["c2"])
    white.ply = 1
    play_checked(BreakthroughRules, white, "c2b1")
    assert BreakthroughRules.outcome(white) == ("win", WHITE)


def test_capturing_everything_wins():
    position = setup(["d4", "a1"], ["e5"])
    play_checked(BreakthroughRules, position, "d4e5")
    assert BreakthroughRules.outcome(position) == ("win", BLACK)
    assert BreakthroughRules.reason(position) == R_PIECES


def test_metadata_counts_pieces():
    metadata = {}
    BreakthroughRules.extra_metadata(setup(["d4", "a1"], ["e5"]), metadata)
    assert metadata == {"black_count": 2, "white_count": 1}
