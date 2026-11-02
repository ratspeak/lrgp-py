# LRGP-py

Python implementation of the **Lightweight Reticulum Gaming Protocol (LRGP)** — a compact, session-based protocol for multiplayer games over [LXMF](https://github.com/markqvist/LXMF) / [Reticulum](https://github.com/markqvist/Reticulum) mesh networks.

LRGP carries game actions in LXMF custom fields over any reachable Reticulum path. The built-in games are turn-based. Each packed game envelope is limited to 200 bytes; title, fallback text, stamps and the rest of LXMF add overhead. The transport chooses packet or Link delivery for the complete message.

This checkout declares **0.4.0** and includes three built-in apps. Tic-Tac-Toe and **Four in a Row**
are dependency-free; Chess is available through the optional `chess`
dependency.

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
