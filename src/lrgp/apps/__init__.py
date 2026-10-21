from .checkers import CheckersApp
from .dots_and_boxes import DotsAndBoxesApp
from .four_in_a_row import FourInARowApp
from .gomoku import GomokuApp
from .hex import HexApp
from .mancala import MancalaApp
from .reversi import ReversiApp
from .tictactoe import TicTacToeApp

__all__ = [
    "CheckersApp",
    "DotsAndBoxesApp",
    "FourInARowApp",
    "GomokuApp",
    "HexApp",
    "MancalaApp",
    "ReversiApp",
    "TicTacToeApp",
]

# Chess is gated behind the [chess] extra to keep the core lib zero-deps.
# Importing lrgp.apps.chess directly raises a clear ImportError when the
# extra isn't installed.
