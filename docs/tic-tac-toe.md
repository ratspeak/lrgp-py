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

This encoding costs exactly 10 bytes in msgpack (1B header + 9B string) — far more efficient than a list of 9 integers or a nested structure.

## Player Assignment

- Challenger = X (always moves first)
- Responder = O

This is fixed — no negotiation needed, reducing protocol complexity.

## Validation Model: `both`

Both sender and receiver validate every move. This ensures:
- Sender can't send illegal moves (client-side prevention)
- Receiver catches bugs or tampering (cross-client play)
- Invalid moves get an `error` response with code `invalid_move`

## Move Payload

Each move carries the full board state, not just the cell index. This ensures state synchronization even if messages arrive out of order or are lost:

```python
{"i": 4, "b": "____X____", "n": 1, "t": "opponent_hash", "x": ""}
```

The receiver validates that the board matches the expected result of applying the move to the previous board state.

## Terminal Detection

Win detection checks 8 lines (3 rows, 3 columns, 2 diagonals). Draw detection checks if the board is full with no winner.

The sender sets `"x": "win"` or `"x": "draw"` on terminal moves. The receiver independently verifies this claim — a false win claim is rejected.

## Wire Efficiency

All payload keys are single characters. The worst-case envelope (error response with code + message + ref) is 84 bytes packed — well within the 200-byte envelope budget.

| Action | Envelope Size | Full Content | Headroom |
|--------|--------------|-------------|----------|
| challenge | 43 B | 96 B | 199 B |
| move (win) | 84 B | 127 B | 168 B |
| error | 84 B | 140 B | 155 B |

## Fallback Text

All fallback text uses the format `[RLAP TTT] <description>`. Non-RLAP clients display this as a regular message, so the game is partially visible even without app support.
