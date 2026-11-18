"""Ultimate Tic-Tac-Toe rules; the shared lifecycle, hydration, and vector
tests in ``test_placement.py`` cover the app."""

import pytest

from lrgp.apps._placement import BLACK, EMPTY, WHITE, parse_square, play_checked, replay, square_name
from lrgp.apps.ultimate_ttt import (
    CELLS,
    DEAD,
    SIZE,
    UltimateRules,
    _has_line,
    board_of,
    cell_of,
    squares_of,
)


def sq(name):
    return parse_square(name, SIZE)


def boards(position):
    metadata = {}
    UltimateRules.extra_metadata(position, metadata)
    return metadata["boards"]


def setup(black, white, states):
    """Stones placed, small-board *states*, no target, and Black to move."""
    assert len(black) == len(white)
    position = UltimateRules.start()
    for name in black:
        position.cells[sq(name)] = BLACK
    for name in white:
        position.cells[sq(name)] = WHITE
    position.boards = list(states)
    position.ply = len(black) * 2
    return position


def legal_boards(position):
    return sorted({board_of(sq(move)) for move in UltimateRules.legal_moves(position)})


def test_notation_is_strict():
    assert UltimateRules.is_canonical("a1")
    assert UltimateRules.is_canonical("i9")
    for bad in ["", "a0", "a10", "j1", "a01", "E5", "e 5", None]:
        assert not UltimateRules.is_canonical(bad), bad


def test_squares_map_to_boards_and_cells():
    assert (board_of(sq("e5")), cell_of(sq("e5"))) == (4, 4)
    assert (board_of(sq("i9")), cell_of(sq("i9"))) == (8, 8)
    assert (board_of(sq("d3")), cell_of(sq("d3"))) == (1, 6)
    assert all(board_of(square) == 4 for square in squares_of(4))
    assert len(squares_of(4)) == 9


def test_each_cell_sends_the_opponent_to_its_board():
    assert len(UltimateRules.legal_moves(UltimateRules.start())) == CELLS

    # e5 is the centre cell of the centre board.
    legal = UltimateRules.legal_moves(replay(UltimateRules, ["e5"]))
    assert legal == [square_name(s, SIZE) for s in squares_of(4) if s != sq("e5")]

    # a1 is the bottom-left cell, so White must play in a1-c3.
    position = replay(UltimateRules, ["a1"])
    assert legal_boards(position) == [0]
    with pytest.raises(ValueError, match="sent to"):
        play_checked(UltimateRules, replay(UltimateRules, ["a1"]), "e5")
    play_checked(UltimateRules, position, "c3")
    assert legal_boards(position) == [8]


def test_a_finished_board_frees_the_next_move():
    position = setup(["a1", "b2"], ["d4", "f6"], [EMPTY] * 9)
    play_checked(UltimateRules, position, "c3")
    assert boards(position) == "______b__"
    assert legal_boards(position) == [8]

    # g7 is the bottom-left cell of its board, sending Black to the won board 0.
    play_checked(UltimateRules, position, "g7")
    assert position.target is None
    assert legal_boards(position) == [1, 2, 3, 4, 5, 6, 7, 8]
    with pytest.raises(ValueError, match="finished"):
        play_checked(UltimateRules, position, "a2")


def test_three_won_boards_in_a_row_win():
    states = [BLACK, BLACK, EMPTY, EMPTY, WHITE, EMPTY, EMPTY, WHITE, EMPTY]
    position = setup(["g1", "h2"], ["e5", "e8"], states)
    play_checked(UltimateRules, position, "i3")
    assert UltimateRules.outcome(position) == ("win", BLACK)
    assert UltimateRules.legal_moves(position) == []
    with pytest.raises(ValueError):
        play_checked(UltimateRules, position, "e4")


def test_all_boards_finished_without_a_line_is_a_draw():
    states = [BLACK, WHITE, BLACK, BLACK, WHITE, WHITE, WHITE, BLACK, EMPTY]
    position = setup(["g7", "i7", "g8", "h9"], ["h7", "h8", "i8", "g9"], states)
    play_checked(UltimateRules, position, "i9")
    assert boards(position) == "wb" + DEAD + "bwwbwb"
    assert UltimateRules.outcome(position) == ("draw", None)
    assert not _has_line(list("bwbbwwwbb"), BLACK)
    assert not _has_line(list("bwbbwwwbb"), WHITE)


def test_occupied_squares_are_illegal():
    with pytest.raises(ValueError, match="occupied"):
        play_checked(UltimateRules, setup(["e5"], ["a1"], [EMPTY] * 9), "e5")
