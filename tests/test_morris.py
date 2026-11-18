"""Nine Men's Morris rules; the shared lifecycle, hydration, and vector
tests in ``test_placement.py`` cover the app."""

import pytest

from lrgp.apps._placement import BLACK, EMPTY, WHITE, play_checked, replay
from lrgp.apps.morris import (
    POINTS,
    QUIET_PLIES,
    R_BLOCKED,
    R_PIECES,
    R_QUIET,
    R_REPETITION,
    MorrisRules,
    _adjacent,
)


def point(name):
    return POINTS.index(name)


def setup(black, white):
    """Pieces on *black* and *white*, nothing in hand, and Black to move."""
    position = MorrisRules.start()
    position.in_hand = [0, 0]
    for name in black:
        position.cells[point(name)] = BLACK
    for name in white:
        position.cells[point(name)] = WHITE
    position.ply = 18
    position.seen = {}
    return position


def test_notation_is_strict():
    for good in ["a1", "d2xg7", "a1-a4", "a1-a4xg7"]:
        assert MorrisRules.is_canonical(good), good
    for bad in ["", "a2", "d4", "a1-", "a1-a4x", "A1", "a1a4", "a1-a4xg7x", "a1 ", "x", None]:
        assert not MorrisRules.is_canonical(bad), bad


def test_every_point_has_two_to_four_neighbours():
    for index, name in enumerate(POINTS):
        assert 2 <= len(_adjacent(index)) <= 4, name
    assert len(_adjacent(point("d2"))) == 4
    assert len(_adjacent(point("a1"))) == 2


def test_placing_a_mill_removes_a_piece():
    # Black a1 a4, White d6 d7, then Black's a7 closes a1-a4-a7.
    position = replay(MorrisRules, ["a1", "d6", "a4", "d7"])
    legal = MorrisRules.legal_moves(position)
    assert "a7xd6" in legal
    assert "a7" not in legal, "a mill must remove"
    with pytest.raises(ValueError, match="Illegal"):
        play_checked(MorrisRules, replay(MorrisRules, ["a1", "d6", "a4", "d7"]), "a7")
    play_checked(MorrisRules, position, "a7xd7")
    assert MorrisRules.render(position)[point("d7")] == EMPTY
    assert position.in_hand == [6, 7]


def test_pieces_in_mills_are_safe_unless_all_are():
    # White's e3-e4-e5 is a mill and b6 is not: only b6 can go.
    position = setup(["a1", "a4", "d7", "g1"], ["e3", "e4", "e5", "b6"])
    legal = MorrisRules.legal_moves(position)
    assert "d7-a7xb6" in legal
    assert "d7-a7xe4" not in legal
    assert "d7-a7" not in legal
    # With b6 gone, the mill's pieces can be taken.
    position.cells[point("b6")] = EMPTY
    assert "d7-a7xe4" in MorrisRules.legal_moves(position)


def test_three_pieces_fly():
    position = setup(["a1", "d2", "g7"], ["b4", "c4", "e5", "f6"])
    assert "a1-e3" in MorrisRules.legal_moves(position), "flying to any empty point"
    white = setup(["a1", "d2", "g7", "g1"], ["b4", "c4", "e5", "f6"])
    white.ply = 19
    assert "b4-e3" not in MorrisRules.legal_moves(white)


def test_two_pieces_or_no_move_lose():
    position = setup(["a1", "a4", "d7", "d2"], ["b6", "f6", "g1"])
    play_checked(MorrisRules, position, "d7-a7xb6")
    assert MorrisRules.outcome(position) == ("win", BLACK)
    assert MorrisRules.reason(position) == R_PIECES
    assert MorrisRules.legal_moves(position) == []

    # White's a1, a4, a7, and d1 have nowhere to go once g1 is taken.
    position = setup(["b4", "d2", "d7", "g4", "b6", "c5"], ["a1", "a4", "a7", "d1"])
    play_checked(MorrisRules, position, "g4-g1")
    assert MorrisRules.outcome(position) == ("win", BLACK)
    assert MorrisRules.reason(position) == R_BLOCKED


def test_repetition_and_quiet_moves_draw():
    position = setup(["a1", "d2", "g7", "c3"], ["b4", "c4", "e5", "f6"])
    position.seen = {position.key(): 1}
    steps = ["a1-d1", "b4-b2", "d1-a1", "b2-b4", "a1-d1", "b4-b2", "d1-a1", "b2-b4"]
    for index, step in enumerate(steps):
        assert MorrisRules.outcome(position) == ("", None), index
        play_checked(MorrisRules, position, step)
    assert MorrisRules.outcome(position) == ("draw", None)
    assert MorrisRules.reason(position) == R_REPETITION

    position = setup(["a1", "d2", "g7", "c3"], ["b4", "c4", "e5", "f6"])
    position.quiet = QUIET_PLIES - 1
    play_checked(MorrisRules, position, "a1-d1")
    assert MorrisRules.reason(position) == R_QUIET


def test_metadata_counts_pieces_in_hand_and_on_the_board():
    metadata = {}
    MorrisRules.extra_metadata(replay(MorrisRules, ["a1", "d6", "a4", "d7", "a7xd7"]), metadata)
    assert metadata == {"black_in_hand": 6, "white_in_hand": 7, "black_count": 3, "white_count": 1}
