# Writing an RLAP App

This guide walks through creating a new RLAP app. See `src/rlap/apps/tictactoe.py` for a complete example.

## 1. Subclass AppBase

```python
from rlap.app_base import AppBase

class CoinFlipApp(AppBase):
    app_id = "coin"
    version = 1
    display_name = "Coin Flip"
    icon = "coin"
    session_type = "one_shot"
    max_players = 2
    validation = "sender"
    actions = ["challenge", "accept", "decline", "flip"]
    preferred_delivery = {
        "challenge": "opportunistic",
        "accept": "opportunistic",
        "decline": "opportunistic",
        "flip": "opportunistic",
    }
    ttl = {"pending": 86400, "active": 3600}
```

## 2. Implement Required Methods

### handle_incoming

Process actions received from the other player:

```python
def handle_incoming(self, session_id, command, payload, sender_hash, identity_id):
    if command == "challenge":
        # Create a new session
        return {"session": {...}, "emit": {"type": "challenge"}, "error": None}
    if command == "flip":
        # Process the flip result
        return {"session": {...}, "emit": {"type": "flip"}, "error": None}
    # ...
```

### handle_outgoing

Prepare actions to send to the other player:

```python
def handle_outgoing(self, session_id, command, payload, identity_id):
    if command == "flip":
        result = "heads" if random.random() > 0.5 else "tails"
        return {"r": result}, "[RLAP Coin] Flipped: {}!".format(result)
    # Returns: (enriched_payload, fallback_text)
```

### validate_action

Validate whether an action is legal:

```python
def validate_action(self, session_id, command, payload, sender_hash):
    # Return (True, None) if valid, (False, "error message") if not
    return True, None
```

### render_fallback

Generate human-readable fallback text:

```python
def render_fallback(self, command, payload):
    if command == "flip":
        return "[RLAP Coin] Flipped: {}!".format(payload.get("r", "?"))
    return "[RLAP Coin] {}".format(command)
```

### get_session_state

Return session state for rendering:

```python
def get_session_state(self, session_id, identity_id):
    # Return a dict with current session data
    return {"session_id": session_id, "result": "heads"}
```

## 3. Wire Budget

Your envelope (packed with msgpack) must fit in 200 bytes. Use single-character payload keys:

```python
# Good: 12 bytes packed
{"r": "heads"}

# Bad: 22 bytes packed
{"result": "heads", "timestamp": 1234567890}
```

Verify with:

```python
from rlap.envelope import pack_envelope, validate_envelope_size

env = pack_envelope("coin", 1, "flip", session_id, {"r": "heads"})
size = validate_envelope_size(env)  # Raises if > 200B
```

## 4. Register Your App

```python
from rlap.router import register
register(CoinFlipApp())
```

Or place your module in `rlap/apps/` and use auto-discovery:

```python
from rlap.router import discover
import rlap.apps
discover(rlap.apps)
```

## 5. Test It

```python
def test_coin_flip():
    app = CoinFlipApp()
    result = app.handle_incoming("s1", "challenge", {}, "sender", "me")
    assert result["error"] is None
```
