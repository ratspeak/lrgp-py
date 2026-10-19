from .checkers import CheckersApp
from .four_in_a_row import FourInARowApp
from .tictactoe import TicTacToeApp

__all__ = ["CheckersApp", "FourInARowApp", "TicTacToeApp"]

# Chess is gated behind the [chess] extra to keep the core lib zero-deps.
# Importing lrgp.apps.chess directly raises a clear ImportError when the
# extra isn't installed.
