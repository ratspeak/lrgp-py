# LRGP-py

Python implementation of the **Lightweight Reticulum Gaming Protocol (LRGP)** — a compact, session-based protocol for multiplayer games over [LXMF](https://github.com/markqvist/LXMF) / [Reticulum](https://github.com/markqvist/Reticulum) mesh networks.

LRGP enables turn-based and real-time multiplayer games to run over LoRa radios, WiFi, TCP, and any other medium Reticulum supports. Game moves are encoded as tiny msgpack envelopes that fit in a single encrypted packet — no link setup needed.

## Quick Start

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

# Walk Scholar's Mate locally
python examples/chess_local.py

# Check wire budget for all TTT actions
python examples/envelope_sizes.py
```

## How It Works

LRGP encodes game sessions as LXMF custom fields:

```python
fields[0xFB] = "lrgp.v1"                       # protocol marker
fields[0xFD] = {                               # envelope
    "a": "ttt.1",                              # app_id.version
    "c": "move",                               # command
    "s": "a1b2c3d4e5f60718",                   # session_id
    "p": {"i": 4, "b": "____X____", ...},      # payload (game-specific)
    "n": b"\\xde\\xad\\xbe\\xef\\xc0\\xff\\xee\\x01",  # 8-byte CSPRNG nonce
}
```

The LXMF `content` field carries fallback text (e.g., `"[LRGP TTT] Move 3"` or `"[LRGP Chess] e2e4"`) for non-LRGP clients.

All envelopes are msgpack-serialized and fit within LXMF's 295-byte OPPORTUNISTIC delivery limit — no link setup needed, single encrypted packet.

### Replay protection

Every outbound envelope carries an 8-byte CSPRNG nonce under key `n`. The
router first probes without recording, authorizes the authenticated sender,
then atomically checks and records before application mutation. The cache is
scoped by receiving identity and session, bounded to 512 nonces per scope and
1024 scopes, with a 10-minute absolute TTL. This keeps unauthenticated traffic
from consuming or evicting replay state. Terminal sessions retain replay
entries through their normal TTL; only explicit session removal may drop that
identity/session scope.

Use `LrgpRouter` as the protocol boundary. It validates canonical wire data,
rejects trailing bytes and duplicate map keys in byte-oriented decoding, binds
each session to its authenticated participant, enforces global session-ID
uniqueness across apps, and caps unsolicited pending challenges at 16 per
participant and 128 per receiving identity. Incoming authenticated sender and
receiving-identity identifiers, and outgoing local identity/recipient
identifiers, must all be non-empty.

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
    tictactoe.py   # Tic-Tac-Toe reference game
    chess.py       # Chess (python-chess engine, UCI wire format; lrgp[chess])
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

- [lrgp-rs](../lrgp-rs) — Rust implementation (wire-compatible)

## License

MIT — see [LICENSE](LICENSE).
