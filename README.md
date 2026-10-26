# LRGP-py

Python implementation of the **Lightweight Reticulum Gaming Protocol (LRGP)** — a compact, session-based protocol for multiplayer games over [LXMF](https://github.com/markqvist/LXMF) / [Reticulum](https://github.com/markqvist/Reticulum) mesh networks.

LRGP carries game actions in LXMF custom fields over any reachable Reticulum path. The built-in games are turn-based. Each packed game envelope is limited to 200 bytes; title, fallback text, stamps and the rest of LXMF add overhead. The transport chooses packet or Link delivery for the complete message.

This checkout declares **0.4.0** and includes sixteen built-in apps. Tic-Tac-Toe, Four in a Row, Checkers,
and the new **Hex**, **Gomoku**, **Reversi**, **Dots and Boxes**,
**Mancala**, **Ultimate Tic-Tac-Toe**, **Go**, **Battleship**, **Quoridor**,
**Nine Men's Morris**, **Breakthrough**, and **Amazons** apps are
dependency-free; Chess is available through the optional `chess` dependency.

## Quick Start

Run these commands from this repository. Use Python **3.8+** in a virtual environment; Chess and test tools are optional extras. For example, create one with `python3 -m venv .venv`, then activate it with `source .venv/bin/activate` on macOS/Linux or `.venv\Scripts\Activate.ps1` in Windows PowerShell.

```bash
# Install (zero dependencies for core library)
pip install -e .

# Add Chess support (pulls in python-chess)
pip install -e ".[chess]"

# Run tests
pip install -e ".[chess,dev]"
pytest

# Play Tic-Tac-Toe locally (no network needed)
python examples/ttt_local.py

# Inspect the built-in Four in a Row manifest (no extra dependency)
python -c "from lrgp.apps import FourInARowApp; print(FourInARowApp().get_manifest())"

# Inspect the built-in Checkers manifest (no extra dependency)
python -c "from lrgp.apps import CheckersApp; print(CheckersApp().get_manifest())"

# Walk Scholar's Mate locally
python examples/chess_local.py

# Measure representative TTT envelopes and message bodies
python examples/envelope_sizes.py
```

## How It Works

LRGP encodes game sessions as LXMF custom fields:

```python
import os

fields = {}
fields[0xFB] = "lrgp.v1"                       # protocol marker
fields[0xFD] = {                               # envelope
    "a": "ttt.1",                              # app_id.version
    "c": "move",                               # command
    "s": "a1b2c3d4e5f60718",                   # session_id
    "p": {"i": 4, "b": "____X____", "n": 1,
          "t": "remote_identity", "x": ""},   # canonical non-terminal TTT move
    "n": os.urandom(8),                        # fresh 8-byte CSPRNG nonce
}
```

The LXMF `content` field carries fallback text (e.g., `"[LRGP TTT] Move 3"` or `"[LRGP Chess] e2e4"`) for non-LRGP clients.

Four in a Row uses `four_in_a_row.1`. Its move intent is just `{"c": 0}`;
the canonical wire move is `{"c": 0, "n": 1, "x": ""}`. Both peers apply
gravity and reconstruct the 42-cell board and next turn locally, so neither a
board snapshot nor a turn identity is sent with a move. A winning move alone
adds `"w"`, set to the authenticated mover.

Use the game router to construct canonical payloads from local intent. The wire sketch above is explanatory; participant identifiers come from authenticated transport metadata, and transport sizing must cover the complete LXMF message.

Checkers uses `checkers.1`. Its move intent is a space-separated square path
such as `{"m": "a5 c3 e1"}`; the canonical wire move is
`{"m": "a5 c3 e1", "n": 12, "x": ""}`. Each peer replays the path history to
rebuild the board, so neither the board nor the next turn is sent. See
[docs/checkers.md](docs/checkers.md).

Hex (`hex.1`), Gomoku (`gomoku.1`), and Reversi (`reversi.1`) share one wire
contract: a move intent is a single square such as `{"m": "h8"}` (Hex also
accepts `{"m": "swap"}` as White's first move) and the wire move is
`{"m": "h8", "n": 0, "x": ""}`, plus `"w"` on a win. Dots and Boxes
(`dots_and_boxes.1`) and Mancala (`mancala.1`) use the same contract with a
line (`{"m": "c3h"}`) or a pit letter (`{"m": "d"}`) as the move; a player who
completes a box or ends in their store simply moves again. See
[docs/placement-games.md](docs/placement-games.md).

The newer single-token games use the same contract; SPEC.md Appendix G
defines each one. Ultimate Tic-Tac-Toe (`ultimate_ttt.1`) moves are a square
such as `{"m": "e5"}` on its 9x9 board. Go (`go.1`) moves are a point such as
`{"m": "e5"}` or `{"m": "pass"}`.

Battleship (`battleship.1`) is the first game with hidden information. Each
player commits to a fleet with a SHA-256 hash of the fleet and a random salt
(`{"h": ...}` on challenge and accept); a shot `{"s": "e5", "a": "h", ...}`
carries the answer to the opponent's last shot, and both fleets are revealed
at the end so each side checks the other's answers. The local intents are
`{"f": fleet, "k": salt}` (both optional) to challenge or accept, `{"s": "e5"}`
to shoot, and `{}` to concede or reveal. SPEC.md Appendix H defines it.

Quoridor (`quoridor.1`) moves step the pawn (`{"m": "e2"}`) or place a wall
(`{"m": "c3h"}`, `{"m": "c3v"}`).

Nine Men's Morris (`morris.1`) moves place a piece (`{"m": "d2"}`) or slide it
(`{"m": "d2-d3"}`), with any removal after an `x` (`{"m": "d2-d3xg7"}`).

Breakthrough (`breakthrough.1`) moves are a step written as two squares, such
as `{"m": "a2b3"}`.

Amazons (`amazons.1`) moves slide an amazon and shoot an arrow, written as
`{"m": "d1d7/g7"}`.

### Replay protection

Every outbound envelope carries an 8-byte CSPRNG nonce under key `n`. The
router first probes without recording, authorizes the authenticated sender,
then atomically checks and records before application mutation. The cache is
scoped by receiving identity and session, bounded to 512 nonces per scope and
1024 scopes, with a 10-minute absolute TTL. This keeps unauthenticated traffic
from consuming or evicting replay state. Ending a session does not clear its
entries; normal TTL expiry and bounded-cache eviction still apply. Explicit
session removal may drop that identity/session scope.

Use `LrgpRouter` as the protocol boundary. It validates envelope fields, binds
each session to the authenticated participant supplied by the transport,
enforces session-ID uniqueness across apps within each local identity, and caps
unsolicited pending challenges at 16 per participant and 128 per receiving identity. Incoming authenticated sender and
receiving-identity identifiers, and outgoing local identity/recipient
identifiers, must all be non-empty.

For byte input, `unpack_from_bytes` rejects trailing data, but duplicate-key
handling depends on its MessagePack decoder. The bundled fallback rejects
repeated string keys but can silently overwrite repeated array keys. The
protocol requires rejecting duplicate map keys at every level. Decode
untrusted input with a decoder that enforces that rule before constructing
dictionaries; the router cannot recover keys already discarded during decoding.
The serializer first tries RNS's vendored codec; check the selected decoder's
duplicate-key handling.

The optional `LrgpTransport` forwards an LXMF `source_hash` only after LXMF
reports a validated message signature. Custom integrations must likewise pass
`dispatch_incoming` a sender derived from authenticated transport metadata,
never a display name, fallback text, or envelope value.

Inbound dispatch changes live game state before the caller commits its durable
session/action transaction. Snapshot first; if that commit fails, call
`rollback_incoming(app_id, session_id, identity_id, envelope["n"], snapshot)`.
This restores the session and releases only the accepted scoped nonce, so the
transport's exact retransmission can be applied. If durable recording of an
authenticated `remote_error` fails, call
`forget_incoming_nonce(identity_id, session_id, envelope["n"])` instead: that
result consumed a nonce but did not mutate game state. SQLite `save_session`
is insert-only; use its allowlisted `update_session` method for existing
records.

## Project Structure

```
src/lrgp/
  constants.py     # Protocol constants
  errors.py        # Error hierarchy
  envelope.py      # Pack/unpack/validate envelopes
  dedup.py         # Receiving-identity/session replay cache
  session.py       # Session state machine
  app_base.py      # Abstract GameBase for games
  router.py        # App registry and dispatch
  store.py         # SQLite persistence
  transport.py     # LXMF bridge (optional, requires lrgp[rns])
  apps/
    checkers.py    # Checkers (English draughts, square-path wire format)
    _placement.py  # Shared engine for the single-token games below
    hex.py         # Hex (11x11, swap rule)
    gomoku.py      # Gomoku (15x15 freestyle)
    reversi.py     # Reversi (8x8, automatic passing)
    dots_and_boxes.py # Dots and Boxes (5x5 boxes, extra turns)
    mancala.py     # Mancala (Kalah, six pits of four seeds)
    ultimate_ttt.py # Ultimate Tic-Tac-Toe (nine small boards)
    go.py          # Go (9x9, area scoring, superko)
    battleship.py  # Battleship (10x10, committed fleets)
    quoridor.py    # Quoridor (9x9, ten walls each)
    morris.py      # Nine Men's Morris (flying at three)
    breakthrough.py # Breakthrough (8x8, diagonal captures)
    amazons.py     # Amazons (10x10, queen moves and arrows)
    four_in_a_row.py # Four in a Row (7x6 gravity, reconstructed state)
    tictactoe.py   # Tic-Tac-Toe reference game
    chess.py       # Chess (python-chess engine, UCI wire format; lrgp[chess])
```

## Writing a Game

Implement the `GameBase` class. This declaration is a skeleton, not an instantiable game; the [game-writing guide](docs/writing-an-app.md) describes the required hooks:

```python
from lrgp.app_base import GameBase

class MyGame(GameBase):
    app_id = "mygame"
    version = 1
    display_name = "My Game"
    session_type = "turn_based"
    validation = "both"
    actions = ["challenge", "accept", "decline", "move"]
    # ... implement abstract methods ...
```

## Protocol Spec

See [SPEC.md](SPEC.md) for the formal protocol specification — implementable without seeing the Python code.
The game-specific design is summarized in
[docs/four-in-a-row.md](docs/four-in-a-row.md).

## Network Usage

For the one-challenge LXMF transport demonstration (requires Reticulum and a working path between its local destinations):

```bash
pip install -e ".[rns]"
python examples/ttt_cli.py
```

This demonstration creates temporary identities and local `lxmf_x`/`lxmf_o` storage, and uses your Reticulum configuration. It is not an interactive two-player client or proof that a configured network path is working.

## See Also

- [lrgp-rs](https://github.com/ratspeak/lrgp-rs) — Rust implementation of the same wire protocol

## License

MIT — see [LICENSE](LICENSE).
