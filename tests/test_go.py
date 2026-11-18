"""Go rules; the shared lifecycle, hydration, and vector tests in
``test_placement.py`` cover the app."""

import pytest

from lrgp.apps._placement import BLACK, EMPTY, WHITE, parse_square, play_checked, replay
from lrgp.apps.go import CELLS, PASS, SIZE, GoRules, area


def at(position, name):
    return position.cells[parse_square(name, SIZE)]


def test_notation_is_strict():
    assert GoRules.is_canonical("a1")
    assert GoRules.is_canonical("i9")
    assert GoRules.is_canonical(PASS)
    for bad in ["", "j1", "a10", "PASS", "pass ", "a01", "E5", None]:
        assert not GoRules.is_canonical(bad), bad


def test_surrounded_stones_are_captured():
    # White's a1 stone loses its last liberty when Black plays b1.
    position = replay(GoRules, ["a2", "a1", "b1"])
    assert at(position, "a1") == EMPTY
    assert position.captures == [1, 0]

    # A two-stone chain in the middle.
    position = replay(
        GoRules, ["d5", "e5", "e4", "f5", "f4", "a1", "e6", "a2", "f6", "a3", "g5"]
    )
    assert at(position, "e5") == EMPTY
    assert at(position, "f5") == EMPTY
    assert position.captures == [2, 0]


def test_suicide_is_illegal_unless_it_captures():
    # Black surrounds a1; White may not play there.
    position = replay(GoRules, ["a2", "e5", "b1"])
    with pytest.raises(ValueError, match="suicide"):
        play_checked(GoRules, position, "a1")
    assert "a1" not in GoRules.legal_moves(position)

    # Filling the last liberty is fine when it captures.
    position = replay(GoRules, ["c1", "b1", "b2", "a2", "a3", "e5", "a1"])
    assert at(position, "a1") == BLACK
    assert at(position, "a2") == EMPTY
    assert at(position, "b1") == EMPTY


def test_a_ko_cannot_be_retaken_at_once():
    # Black a2 b1 b3 and White c1 c3 d2 make a ko at b2 and c2: Black plays
    # c2, and White takes it back by playing b2.
    moves = ["b1", "c1", "a2", "d2", "b3", "c3", "c2", "b2"]
    position = replay(GoRules, moves)
    assert at(position, "c2") == EMPTY
    with pytest.raises(ValueError, match="repeat"):
        play_checked(GoRules, replay(GoRules, moves), "c2")
    assert "c2" not in GoRules.legal_moves(position)
    # After a move elsewhere Black may retake.
    for move in ("g7", "g8", "c2"):
        play_checked(GoRules, position, move)
    assert at(position, "b2") == EMPTY


def test_two_passes_end_the_game_by_area():
    position = replay(GoRules, [PASS, PASS])
    # Empty board: 0 to 0, so komi gives White the game.
    assert GoRules.outcome(position) == ("win", WHITE)
    assert GoRules.legal_moves(position) == []

    # One pass and a move do not end it.
    assert GoRules.outcome(replay(GoRules, [PASS, "e5", PASS])) == ("", None)

    # A lone Black stone owns the whole board: 81 to 0.
    position = replay(GoRules, ["e5", PASS, PASS])
    assert area(position.cells) == [81, 0]
    assert GoRules.outcome(position) == ("win", BLACK)


def test_area_counts_stones_and_surrounded_points():
    cells = [EMPTY] * CELLS
    # A Black wall on file d and a White wall on file f.
    for rank in range(SIZE):
        cells[rank * SIZE + 3] = BLACK
        cells[rank * SIZE + 5] = WHITE
    # a-c (27) plus file d (9) for Black, file f plus g-i for White; file e
    # touches both and counts for nobody.
    assert area(cells) == [36, 36]


def test_metadata_reports_score_and_captures():
    metadata = {}
    GoRules.extra_metadata(replay(GoRules, ["a2", "a1", "b1"]), metadata)
    assert metadata["black_count"] == 81
    assert metadata["white_count"] == 0
    assert metadata["komi"] == 7.5
    assert metadata["black_captures"] == 1
    assert metadata["white_captures"] == 0


def test_pass_is_listed_last():
    legal = GoRules.legal_moves(GoRules.start())
    assert len(legal) == CELLS + 1
    assert legal[0] == "a1"
    assert legal[-1] == PASS
