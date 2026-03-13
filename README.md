# LRGP-py

Python implementation of the **Lightweight Reticulum Gaming Protocol (LRGP)** — a compact, session-based protocol for multiplayer games over [LXMF](https://github.com/markqvist/LXMF) / [Reticulum](https://github.com/markqvist/Reticulum) mesh networks.

LRGP enables turn-based and real-time multiplayer games to run over LoRa radios, WiFi, TCP, and any other medium Reticulum supports. Game moves are encoded as tiny msgpack envelopes that fit in a single encrypted packet — no link setup needed.

## Quick Start

```bash
# Install (zero dependencies for core library)
pip install -e .

# Run tests
pip install -e ".[dev]"
pytest

# Play Tic-Tac-Toe locally (no network needed)
python examples/ttt_local.py

# Check wire budget for all TTT actions
python examples/envelope_sizes.py
```

## How It Works

LRGP encodes game sessions as LXMF custom fields:

```python
fields[0xFB] = "lrgp.v1"                     # protocol marker
fields[0xFD] = {                             # envelope
    "a": "ttt.1",                            # app_id.version
    "c": "move",                             # command
    "s": "a1b2c3d4e5f6g7h8",                 # session_id
    "p": {"i": 4, "b": "____X____", ...},    # payload
}
```

The LXMF `content` field carries fallback text (e.g., `"[LRGP TTT] Move 3"`) for non-LRGP clients.

All envelopes are msgpack-serialized and fit within LXMF's 295-byte OPPORTUNISTIC delivery limit — no link setup needed, single encrypted packet.

## Project Structure

```
src/lrgp/
  constants.py     # Protocol constants
  errors.py        # Error hierarchy
  envelope.py      # Pack/unpack/validate envelopes
  session.py       # Session state machine
  app_base.py      # Abstract GameBase for games
  router.py        # App registry and dispatch
  store.py         # SQLite persistence
  transport.py     # LXMF bridge (optional, requires lrgp[rns])
  apps/
    tictactoe.py   # Tic-Tac-Toe reference game
```

## Writing a Game

Implement the `GameBase` class:

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

## Network Usage

For LXMF transport (requires Reticulum):

```bash
pip install -e ".[rns]"
python examples/ttt_cli.py
```

## See Also

- [lrgp-rs](../rlap-rs) — Rust implementation (wire-compatible)

## License

AGPL-3.0 — see [LICENSE](LICENSE).
