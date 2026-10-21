# Single-Token Games Design

Hex (`hex.1`), Gomoku (`gomoku.1`), Reversi (`reversi.1`), Dots and Boxes
(`dots_and_boxes.1`), and Mancala (`mancala.1`) are dependency-free built-ins
in which every move is a single token: a square, a line, or a pit. They are
two-player and turn-based, with validation set to `both`. SPEC.md Appendix E
(the stone-placement games and the shared wire contract) and Appendix F (Dots
and Boxes and Mancala) are normative; this page explains how the
implementation applies them.

## One engine, five rule sets

All five games share `lrgp.apps._placement.PlacementApp`, which owns the LRGP
lifecycle: challenge and coin-flip accept, move validation by replay, draw
offers, resignation, hydration, and expiry. Each game module supplies only a
rules class (`HexRules`, `GomokuRules`, `ReversiRules`, `DotsAndBoxesRules`,
`MancalaRules`) with the starting position, legal moves, how a move is
applied, who moves next, and how the game ends. lrgp-rs has the same split
(`apps/placement.rs`), and both implementations list legal moves in the same
order.

Because the rules decide who moves next, Reversi's automatic pass, a
completed box, and a seed sown into your own store all work the same way: the
mover simply sends another move, and the receiver's replay agrees it is their
turn.

`PlacementApp` itself has an empty `app_id`, so `LrgpRouter.discover` skips it
and registers only the five concrete apps.

## Squares, roles, and the wire

Squares are a lowercase file and a rank with no leading zero: `a1` is the
bottom-left square. Black moves first; the responder flips a coin when
accepting and sends exactly `{"b": black_identity}`.

The local move API accepts exactly `{"m": "h8"}`. The app derives the wire
payload:

```python
{"m": "h8", "n": 0, "x": ""}
```

`n` is the number of moves already played. `x` is `""`, `"win"`, or `"draw"`
(Hex and Dots and Boxes never draw). A winning move adds `"w"`, the identity the rules name as
winner. Each peer replays the move history, so the board, legal moves, and
next turn are never sent.

## Hex

- 11×11, `a1`–`k11`. Each cell touches six others: left and right on its rank,
  up and down its file, and the `(file+1, rank-1)` / `(file-1, rank+1)`
  diagonal.
- Black connects rank 1 to rank 11; White connects file `a` to file `k`. A full
  board always has exactly one winner.
- **Swap.** As its first move only, White may send `{"m": "swap"}`. Black's
  stone becomes a White stone on the mirrored cell (`c7` becomes `g3`). Players
  keep their colors and Black moves next. `legal_moves` lists `swap` last when
  it is available.

## Gomoku

- 15×15, `a1`–`o15`, freestyle: completing five or more in a row in any
  direction wins. Filling the board without five is a draw.
- There is no opening restriction (no swap2 or renju rules), so Black has a
  first-move advantage.

## Reversi

- 8×8 with the standard four-disc start. A move must flip at least one disc.
- **Automatic passing.** When the opponent has no legal move, the mover simply
  moves again; no pass message exists. `skipped_turn` in local state is true
  right after such a move.
- The game ends when neither side can move. More discs wins and equal counts
  draw, so the winner can be the player who did not make the last move.
- `black_count` and `white_count` are kept in local state for display.

## Dots and Boxes

- 5×5 boxes on 6×6 dots (`a1`–`f6`). A move names a dot and a direction:
  `c3h` is the line from c3 to d3 and `c3v` the line from c3 to c4, so there
  are 60 lines.
- Completing a box (or two at once) claims it and the mover draws again;
  `extra_turn` in local state is true right after such a move.
- The game ends when all 25 boxes are claimed, and more boxes wins. There are
  no draws by board.
- `board` is an 11×11 picture from the top-left dot, split into rows of 11:
  `+` dots, `-` / `|` drawn lines, spaces for undrawn lines, and `_` / `b` /
  `w` for box owners. `black_boxes` and `white_boxes` hold the score.

## Mancala

- Kalah with six pits of four seeds. Black (the first mover) owns pits
  `a`–`f` and White owns `g`–`l`; a move is one of your own non-empty pits.
- Seeds are sown in the order `a`…`f`, Black's store, `g`…`l`, White's store,
  skipping the opponent's store. Ending in your own store earns another move
  (`extra_turn`); ending in an empty pit of your own captures it and the
  facing pit (`a` faces `l`) if that pit has seeds.
- When either side's pits are empty, both sides sweep their remaining seeds
  into their stores and the larger store wins; 24–24 is a draw.
- `board` is the fourteen hole counts in sowing order, comma-separated
  (`4,4,4,4,4,4,0,4,4,4,4,4,4,0` at the start). `black_store` and
  `white_store` hold the score.

## Local session metadata

| Key | Meaning |
|-----|---------|
| `board` | The position, encoded per game (see above and SPEC.md) |
| `moves` | Accepted moves in order |
| `my_color` | `b` or `w` for the local identity |
| `first_turn` | Black (first-moving) identity |
| `turn` | Identity to move next, or empty after the game ends |
| `move_count` / `last_move` | History length and most recent move |
| `legal_moves` | Moves available to the player to move |
| `winner` / `terminal` / `terminal_reason` | Result and its reason |
| `draw_offered` / `draw_offered_by` | Negotiated-draw state |

Board reasons are `con` (Hex connection), `5ir` and `ful` (Gomoku five and
full board), and `cnt` (the disc, box, or seed count in Reversi, Dots and
Boxes, and Mancala). Resignation and agreement use `rsn` and `agr`. Hydration
replays `moves` and requires every field above to agree.

## Cross-language fixtures

`tests/vectors/generate_placement.py` writes the `hex_*`, `gomoku_*`,
`reversi_*`, `dots_and_boxes_*`, and `mancala_*` fixtures shared
byte-for-byte with lrgp-rs. The `*_reference.txt`
files hold one reference game per game, played by the self-play policy
`legal[(ply * 7 + seed) % len(legal)]`; `gomoku_draw.txt` holds a full-board
draw and `mancala_draw.txt` a 24–24 Mancala game. The seeds were chosen so the
Hex game swaps at ply 1, the Reversi game contains an automatic pass, the Dots
and Boxes game contains a double box, and the Mancala game contains extra
turns and captures. The Reversi, Dots and Boxes, and Mancala games are each
won on count by the player who did not move last.
