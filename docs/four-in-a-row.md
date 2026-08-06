# Four in a Row Design

Four in a Row is the dependency-free `four_in_a_row.1` built-in. It is a
two-player, turn-based gravity game with validation set to `both`.

## Board and roles

The board is a 42-character ASCII string representing seven columns by six
rows in row-major, top-to-bottom order. `_` is empty, `A` belongs to the
challenger, and `B` belongs to the responder. The challenger always moves
first. A move selects a column from 0 through 6, and the marker lands in the
lowest empty cell in that column.

For example, the first move in column 3 produces this local board:

```text
_______
_______
_______
_______
_______
___A___
```

## Compact wire contract

The local move API accepts exactly `{"c": 3}`. The app derives the canonical
wire payload:

```python
{"c": 3, "n": 1, "x": ""}
```

`n` is a one-based monotonic move count and `x` is exactly `""`, `"win"`, or
`"draw"`. A winning move has one additional key, `"w"`, whose value must be
the authenticated mover's identity. `"w"` is forbidden on every other move.

Moves never transmit the board or next-turn identity. Each peer independently
applies gravity, chooses `A` or `B` from `n`, detects the resulting terminal
state, and derives the next player. A disagreement in the move number,
landing cell, terminal claim, or winner is rejected without changing state.

The responder accepts with exactly `{"t": challenger_identity}`. Challenge,
decline, resign, draw offer, draw accept, and draw decline payloads are empty.

## Local session metadata

Each peer persists these game-specific keys:

| Key | Meaning |
|-----|---------|
| `board` | Canonical 42-character local board |
| `turn` | Identity whose turn is next, or empty after a terminal action |
| `first_turn` | Challenger identity |
| `my_marker` | `A` for challenger, `B` for responder |
| `first_marker` / `second_marker` | Canonical marker labels `A` / `B` |
| `move_count` | Number of applied moves |
| `last_column` / `last_row` / `last_cell` | Last landing coordinates, initially null |
| `winner` | Winning identity, or empty when there is none |
| `terminal` | Empty, `win`, `draw`, or `resign` |
| `draw_offered` / `draw_offered_by` | Outstanding negotiated-draw state |

Hydration validates more than the string shape. Gravity, marker counts, move
count, turn parity, last-move coordinates, terminal state, winner ownership,
and draw-offer ownership must all agree. A restored winning board must also
show that removing the recorded last marker removes every winning line, which
prevents a session from continuing after it should have completed.
When an active session expires, any outstanding draw offer is cleared before
the expired record is serialized or restored because it can no longer be
answered.

## Terminal behavior

Four aligned markers in a row, column, or either diagonal completes the game
with `terminal="win"`. A full board with no winner completes it with
`terminal="draw"`. A negotiated draw can also complete before the board is
full. Resignation records the other bound participant as winner. Moves and
all terminal lifecycle actions clear any outstanding draw offer.
