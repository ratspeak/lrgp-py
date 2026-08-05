# Writing an LRGP Game

This guide describes the Python `GameBase` contract. Start with
`src/lrgp/apps/tictactoe.py` for a complete implementation, and use
`LrgpRouter` for all network-facing dispatch.

## 1. Subclass `GameBase`

```python
from lrgp.app_base import GameBase


class CoinFlipGame(GameBase):
    app_id = "coin"
    version = 1
    display_name = "Coin Flip"
    icon = "coin"
    session_type = "single_round"
    min_players = 2
    max_players = 2
    validation = "both"
    actions = ["challenge", "accept", "decline", "flip"]
    preferred_delivery = {
        "challenge": "opportunistic",
        "accept": "opportunistic",
        "decline": "opportunistic",
        "flip": "opportunistic",
    }
    ttl = {"pending": 86400, "active": 3600}
```

App IDs, action names, and versions are part of the canonical wire form. See
`SPEC.md` before publishing them; changing one is a protocol change.

## 2. Implement the application hooks

`GameBase` requires five methods:

```python
def handle_incoming(self, session_id, command, payload,
                    sender_hash, identity_id):
    """Apply an authenticated incoming action.

    Return {"session": ..., "emit": ..., "error": ...}. The router has
    already validated the envelope, replay nonce, app/version/action, and
    participant. Return an exact {code,msg,ref} error map on rejection.
    """

def handle_outgoing(self, session_id, command, payload, identity_id):
    """Mutate local state and return (wire_payload, fallback_text)."""

def validate_action(self, session_id, command, payload,
                    sender_hash, identity_id=""):
    """Return (True, None), or (False, message) for an invalid action."""

def get_session_state(self, session_id, identity_id):
    """Return renderable local state for one local identity/session."""

def render_fallback(self, command, payload):
    """Return text shown by LXMF clients that do not implement LRGP."""
```

Games create and update `lrgp.session.Session` records through the inherited
session helpers. A new challenge is `pending`; accepting makes it `active`.
Call `SessionStateMachine.apply_command()` for every stateful command,
including same-state actions such as draw offers and declines, so timestamps
and TTLs remain correct.

The application hook deliberately does **not** receive a participant argument.
The router validates the intended participant before mutation and binds a new
outgoing challenge after the app creates its session. Incoming handlers should
still store the authenticated `sender_hash` on newly created challenge
sessions. Do not call application hooks directly at a transport boundary.

## 3. Validate before mutation

Override `validate_outgoing()` when a game needs command-specific local
checks, but always call the base implementation first:

```python
from lrgp.errors import OutgoingActionError


def validate_outgoing(self, session_id, command, payload, identity_id,
                      participant_hash=""):
    session = super().validate_outgoing(
        session_id, command, payload, identity_id, participant_hash
    )
    if command == "flip" and "r" not in payload:
        raise OutgoingActionError("invalid_move", "Missing result", command)
    return session
```

The router snapshots state and rolls it back if outgoing preparation raises or
if an incoming handler returns an error. Do not perform irreversible external
side effects inside a handler before validation succeeds.

## 4. Register and use the router

```python
from lrgp.router import LrgpRouter

router = LrgpRouter()
router.register(CoinFlipGame())

prepared = router.dispatch_outgoing_to(
    "coin",
    "challenge",
    {},
    "0123456789abcdef",  # canonical 16-character lowercase hex session ID
    "my_identity_hash",
    "remote_identity_hash",
)

# Send prepared.envelope in LXMF custom fields, prepared.fallback_text in the
# LXMF content field, and use prepared.delivery_method as the preference.
```

For inbound messages:

```python
snapshot = router.snapshot_session(
    app_id, envelope["s"], receiving_identity_hash
)
verdict = router.dispatch_incoming(
    envelope,
    authenticated_sender_hash,
    receiving_identity_hash,
)

if verdict.kind == "applied":
    try:
        commit_session_and_action(verdict.result)
    except Exception:
        router.rollback_incoming(
            app_id,
            envelope["s"],
            receiving_identity_hash,
            envelope["n"],
            snapshot,
        )
        raise
    update_ui(verdict.result)
elif verdict.kind == "remote_error":
    try:
        commit_remote_error(verdict.result)
    except Exception:
        router.forget_incoming_nonce(
            receiving_identity_hash, envelope["s"], envelope["n"]
        )
        raise
    reconcile(verdict.result)
# "replay" is a silent duplicate.
```

The router owns canonical validation, participant authorization, replay
protection, global session-ID uniqueness across apps, challenge admission,
TTL checks, and rollback. A transport integration should not recreate those
steps around a direct game call.

`sender_hash` is a trust-boundary argument, not an identity claim LRGP can
verify by itself. Derive it from transport-authenticated LXMF/Reticulum
metadata. The optional `LrgpTransport` requires `signature_validated is True`
before forwarding an LXMF source hash. A custom bridge must enforce an
equivalent rule.

Persistent storage must distinguish creation from mutation. `LrgpStore` uses
`save_session()` only for initial insertion (a duplicate key fails) and
`update_session()` for the explicit mutable-field allowlist. Commit the session
and corresponding action atomically in the application database. If that
transaction fails after an applied inbound dispatch, use the snapshot rollback
shown above. If durable recording of an authenticated remote error fails, use
the nonce-only recovery shown above; restoring a session snapshot would be
incorrect because that result did not mutate game state. Ordinary application
rejections are not rolled back through either external-commit recovery path.

## 5. Keep the wire payload small

Every packed envelope must fit in 200 bytes. Prefer short payload keys:

```python
# Compact
{"r": "heads"}

# Wasteful
{"result": "heads", "timestamp": 1234567890}
```

Measure the canonical envelope:

```python
from lrgp.envelope import pack_envelope, validate_envelope_size

envelope = pack_envelope(
    "coin", 1, "flip", "0123456789abcdef", {"r": "heads"}
)
size = validate_envelope_size(envelope)
```

## 6. Test the protocol boundary

At minimum, test:

- canonical and oversized payload rejection;
- valid and invalid transitions without partial mutation;
- wrong-turn, malformed, and forged terminal actions;
- authenticated participant mismatch;
- duplicate byte-identical delivery and fresh-nonce challenge retry;
- pending/active TTL behavior;
- every fallback string and declared delivery preference.

Use canonical session IDs in router tests. Direct game-hook tests may be useful
for game rules, but they do not replace router tests because hooks intentionally
assume the protocol boundary has already authenticated and validated input.
