# Tic-Tac-Toe Design Rationale

## Board Encoding

The board is a 9-character ASCII string using `X`, `O`, and `_` (empty). Row-major order:

```
 0 | 1 | 2
-----------
 3 | 4 | 5
-----------
 6 | 7 | 8
```

The fixed-length string takes 10 MessagePack bytes (a one-byte header and nine ASCII bytes) and keeps board comparisons simple.

## Player Assignment

- Challenger = X (always moves first)
- Responder = O

This is fixed — no negotiation needed, reducing protocol complexity.

## Validation Model: `both`

Both sender and receiver validate every move. This ensures:
- The local API rejects an illegal move before changing its state
- Receiver catches bugs or tampering (cross-client play)
- Invalid moves get an `error` response with code `invalid_move`

## Move Payload

Each move carries the resulting board alongside the cell index. The receiver compares it with its own expected next board; it rejects an out-of-order or inconsistent move. This check does not repair missing actions:

```python
{"i": 4, "b": "____X____", "n": 1, "t": "opponent_hash", "x": ""}
```

The receiver validates that the board matches the expected result of applying the move to the previous board state.

## Terminal Detection

Win detection checks 8 lines (3 rows, 3 columns, 2 diagonals). Draw detection checks if the board is full with no winner.

The sender sets `"x": "win"` or `"x": "draw"` on terminal moves. The receiver independently verifies this claim — a false win claim is rejected.

## Wire Efficiency

All payload keys are single characters. Every packed envelope, including its
nonce and participant strings, must fit the 200-byte LRGP limit. The fallback,
LXMF fields and transport overhead add bytes outside that limit. Run
[`examples/envelope_sizes.py`](../examples/envelope_sizes.py) from the repository
root to inspect the current example envelopes and serialized message bodies;
it does not certify that the complete encrypted message fits one packet.

## Fallback Text

All fallback text uses the format `[LRGP TTT] <description>`. Non-LRGP clients display this as a regular message, so the game is partially visible even without app support.
