"""Quoridor rules; the shared lifecycle, hydration, and vector tests in
``test_placement.py`` cover the app."""

import pytest

from lrgp.apps._placement import BLACK, WHITE, parse_square, play_checked, replay
from lrgp.apps.quoridor import SIZE, SLOTS, QuoridorRules, _slot


def pawn_moves(position):
    return [m for m in QuoridorRules.legal_moves(position) if parse_square(m, SIZE) is not None]


def sq(name):
    return parse_square(name, SIZE)


def test_notation_is_strict():
    for good in ["a1", "i9", "a1h", "h8v", "e3h"]:
        assert QuoridorRules.is_canonical(good), good
    for bad in ["", "i1h", "a9v", "a1x", "A1h", "a1hv", "j1", "e3 h", "h", None]:
        assert not QuoridorRules.is_canonical(bad), bad


def test_the_opening_lists_pawn_steps_and_every_wall():
    start = QuoridorRules.start()
    board = QuoridorRules.render(start)
    assert len(board) == 17 * 17
    assert board.find(WHITE) == 8
    assert board.rfind(BLACK) == 16 * 17 + 8
    walled = QuoridorRules.render(replay(QuoridorRules, ["e1h", "a8v"]))
    assert walled[15 * 17 + 8:15 * 17 + 11] == "---"
    for row in range(3):
        assert walled[row * 17 + 1] == "|"
    assert pawn_moves(start) == ["d1", "e2", "f1"]
    legal = QuoridorRules.legal_moves(start)
    assert len(legal) == 3 + 2 * SLOTS
    assert legal[0] == "a1h"
    assert legal == sorted(legal)


def test_walls_block_steps_and_may_not_overlap_or_cross():
    # e1h lies between e1/f1 and e2/f2, so Black can't step up from e1.
    black = replay(QuoridorRules, ["e1h"])
    black.ply = 0
    assert pawn_moves(black) == ["d1", "f1"]
    for clash in ["e1h", "d1h", "f1h", "e1v"]:
        with pytest.raises(ValueError, match="overlaps"):
            play_checked(QuoridorRules, replay(QuoridorRules, ["e1h"]), clash)
    # Next to it, and the other way round elsewhere, are fine.
    position = replay(QuoridorRules, ["e1h", "c1h", "d1v"])
    assert position.walls_left == [8, 9]


def test_a_wall_may_not_shut_a_pawn_in():
    sealed = QuoridorRules.start()
    for file in (0, 2, 4, 6):
        sealed.horizontal[_slot(file, 0)] = True
    # Rank 1 is walled off from a1 to h1; i1 is the last way up.
    with pytest.raises(ValueError, match="cut"):
        play_checked(QuoridorRules, sealed, "h1v")
    assert not sealed.vertical[_slot(7, 0)], "a refused wall leaves no trace"
    assert "h1v" not in QuoridorRules.legal_moves(sealed)


def test_pawns_jump_and_side_step():
    position = QuoridorRules.start()
    position.pawns = [sq("e4"), sq("e5")]
    assert pawn_moves(position) == ["d4", "e3", "e6", "f4"]
    # A wall behind White turns the jump into side steps.
    position.horizontal[_slot(4, 4)] = True
    assert pawn_moves(position) == ["d4", "d5", "e3", "f4", "f5"]
    # At the edge there is nothing behind, so side steps again.
    position.horizontal[_slot(4, 4)] = False
    position.pawns = [sq("e8"), sq("e9")]
    assert pawn_moves(position) == ["d8", "d9", "e7", "f8", "f9"]
    with pytest.raises(ValueError, match="cannot move"):
        play_checked(QuoridorRules, position, "e9")


def test_reaching_the_far_rank_wins():
    position = QuoridorRules.start()
    position.pawns = [sq("a8"), sq("i8")]
    play_checked(QuoridorRules, position, "a9")
    assert QuoridorRules.outcome(position) == ("win", BLACK)
    assert QuoridorRules.legal_moves(position) == []


def test_a_third_repetition_draws():
    shuffle = ["d1", "d9", "e1", "e9", "d1", "d9", "e1"]
    assert QuoridorRules.outcome(replay(QuoridorRules, shuffle)) == ("", None)
    assert QuoridorRules.outcome(replay(QuoridorRules, shuffle + ["e9"])) == ("draw", None)


def test_walls_run_out():
    position = QuoridorRules.start()
    position.walls_left = [0, 10]
    with pytest.raises(ValueError, match="No walls"):
        play_checked(QuoridorRules, position, "a1h")
    assert len(QuoridorRules.legal_moves(position)) == 3
    metadata = {}
    QuoridorRules.extra_metadata(replay(QuoridorRules, ["e1h", "a8v"]), metadata)
    assert metadata["walls"] == ["a8v", "e1h"]
    assert metadata["black_walls"] == 9
    assert metadata["white_walls"] == 9
