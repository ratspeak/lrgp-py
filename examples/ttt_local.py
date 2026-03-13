#!/usr/bin/env python3
"""Local Tic-Tac-Toe simulation — two players, in-memory, no network."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lrgp.apps.tictactoe import TicTacToeApp, EMPTY_BOARD, _check_winner, _check_draw
from lrgp.envelope import pack_envelope, validate_envelope_size
from lrgp.constants import CMD_CHALLENGE, CMD_ACCEPT, CMD_MOVE, CMD_RESIGN

PLAYER_X = "player_x_hash"
PLAYER_O = "player_o_hash"


def print_board(board):
    """Print an ASCII tic-tac-toe board."""
    symbols = [c if c != "_" else str(i) for i, c in enumerate(board)]
    print()
    print("  {} | {} | {}".format(symbols[0], symbols[1], symbols[2]))
    print(" -----------")
    print("  {} | {} | {}".format(symbols[3], symbols[4], symbols[5]))
    print(" -----------")
    print("  {} | {} | {}".format(symbols[6], symbols[7], symbols[8]))
    print()


def main():
    # Each player has their own app instance (simulating separate clients)
    app_x = TicTacToeApp()  # Challenger (X)
    app_o = TicTacToeApp()  # Responder (O)

    session_id = "local_game_001"

    print("=== LRGP Tic-Tac-Toe Local Simulation ===")
    print()

    # 1. X sends challenge
    payload_out, fallback = app_x.handle_outgoing(session_id, CMD_CHALLENGE,
                                                   {}, PLAYER_X)
    envelope = pack_envelope("ttt", 1, CMD_CHALLENGE, session_id, payload_out)
    env_size = validate_envelope_size(envelope)
    print("X: {} (envelope: {} B)".format(fallback, env_size))

    # O receives challenge
    app_o.handle_incoming(session_id, CMD_CHALLENGE, {}, PLAYER_X, PLAYER_O)

    # 2. O accepts
    payload_out, fallback = app_o.handle_outgoing(session_id, CMD_ACCEPT,
                                                   {}, PLAYER_O)
    envelope = pack_envelope("ttt", 1, CMD_ACCEPT, session_id, payload_out)
    env_size = validate_envelope_size(envelope)
    print("O: {} (envelope: {} B)".format(fallback, env_size))

    # X receives accept
    app_x.handle_incoming(session_id, CMD_ACCEPT, payload_out,
                           PLAYER_O, PLAYER_X)

    # 3. Play loop
    current_player = PLAYER_X
    current_app = app_x
    other_app = app_o
    other_player = PLAYER_O

    while True:
        session = current_app._get_session(session_id, current_player)
        board = session.metadata["board"]
        marker = session.metadata.get("my_marker", "?")

        print_board(board)
        print("{}'s turn ({}):".format(
            "Player X" if current_player == PLAYER_X else "Player O", marker))

        try:
            raw = input("  Enter cell (0-8) or 'q' to resign: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGame aborted.")
            return

        if raw.lower() == "q":
            payload_out, fallback = current_app.handle_outgoing(
                session_id, CMD_RESIGN, {}, current_player)
            envelope = pack_envelope("ttt", 1, CMD_RESIGN, session_id, payload_out)
            env_size = validate_envelope_size(envelope)
            print("{}: {} (envelope: {} B)".format(marker, fallback, env_size))
            other_app.handle_incoming(session_id, CMD_RESIGN, {},
                                       current_player, other_player)
            break

        try:
            cell = int(raw)
        except ValueError:
            print("  Invalid input, try again.")
            continue

        if cell < 0 or cell > 8:
            print("  Cell must be 0-8.")
            continue

        if board[cell] != "_":
            print("  Cell {} is already taken!".format(cell))
            continue

        # Make move
        payload_out, fallback = current_app.handle_outgoing(
            session_id, CMD_MOVE, {"i": cell}, current_player)
        envelope = pack_envelope("ttt", 1, CMD_MOVE, session_id, payload_out)
        env_size = validate_envelope_size(envelope)
        print("{}: {} (envelope: {} B)".format(marker, fallback, env_size))

        # Deliver to other player
        result = other_app.handle_incoming(session_id, CMD_MOVE, payload_out,
                                            current_player, other_player)
        if result["error"]:
            print("  ERROR from receiver: {}".format(result["error"]["msg"]))
            continue

        # Check for game end
        new_board = payload_out.get("b", board)
        terminal = payload_out.get("x", "")

        if terminal == "win":
            print_board(new_board)
            print("{} wins!".format(marker))
            break
        elif terminal == "draw":
            print_board(new_board)
            print("Game drawn!")
            break

        # Swap players
        current_player, other_player = other_player, current_player
        current_app, other_app = other_app, current_app

    print("\nGame over.")


if __name__ == "__main__":
    main()
