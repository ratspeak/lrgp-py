from .amazons import AmazonsApp
from .battleship import BattleshipApp
from .breakthrough import BreakthroughApp
from .checkers import CheckersApp
from .dots_and_boxes import DotsAndBoxesApp
from .four_in_a_row import FourInARowApp
from .go import GoApp
from .gomoku import GomokuApp
from .hex import HexApp
from .mancala import MancalaApp
from .morris import MorrisApp
from .pente import PenteApp
from .quoridor import QuoridorApp
from .reversi import ReversiApp
from .tictactoe import TicTacToeApp
from .ultimate_ttt import UltimateTicTacToeApp

__all__ = [
    "AmazonsApp",
    "BattleshipApp",
    "BreakthroughApp",
    "CheckersApp",
    "DotsAndBoxesApp",
    "FourInARowApp",
    "GoApp",
    "GomokuApp",
    "HexApp",
    "MancalaApp",
    "MorrisApp",
    "PenteApp",
    "QuoridorApp",
    "ReversiApp",
    "TicTacToeApp",
    "UltimateTicTacToeApp",
]

# Chess is gated behind the [chess] extra to keep the core lib zero-deps.
# Importing lrgp.apps.chess directly raises a clear ImportError when the
# extra isn't installed.
