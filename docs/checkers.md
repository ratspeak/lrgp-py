# Checkers Design

Checkers is the dependency-free `checkers.1` built-in: American checkers /
English draughts for two players, turn-based, with validation set to `both`.
The normative rules are in SPEC.md Appendix D; this page explains how the
implementation applies them.

## Board and roles

Squares use chess-style names. `a1` is a dark square and only dark squares
are playable. Black starts on ranks 6–8 and moves toward rank 1; White starts
on ranks 1–3 and moves toward rank 8. Black moves first.

The responder flips a coin when accepting and sends the Black player's
identity as exactly `{"b": black_identity}`. Each peer records that identity
as `first_turn` and its own color as `my_color` (`"b"` or `"w"`).

Local state keeps the board as 64 ASCII cells in row-major order from `a8`
(index 0) to `h1` (index 63). `_` is empty, `b`/`w` are men and `B`/`W` are
kings. The starting board is drawn like this:

```text
_b_b_b_b   8
b_b_b_b_   7
_b_b_b_b   6
________   5
________   4
w_w_w_w_   3
_w_w_w_w   2
w_w_w_w_   1
abcdefgh
```

## Rules

- Men step one square diagonally forward. Kings step one square diagonally
  in any direction; kings do not fly.
- Captures are mandatory. If several are available the mover may pick any of
  them, but the chosen jump sequence must be finished.
- Jumped pieces stay on the board until the move ends. They cannot be jumped
  twice and nothing can land on them. The moving piece's starting square is
  empty during the sequence, so a king may finish a loop on its own square
  (`c1 e3 c5 a3 c1`).
- A man that reaches the far rank is crowned and its move ends there, even if
  the new king could keep jumping.
- A player with no legal move loses: `np` when they have no pieces left,
  `nm` when they are blocked. There is no automatic draw.

## Move wire contract

The local move API accepts exactly `{"m": "a5 c3 e1"}`: the origin followed
by each landing square, joined by single spaces. The app derives the
canonical wire payload:

```python
{"m": "a5 c3 e1", "n": 12, "x": ""}
```

`n` is the 0-based ply (0 is Black's first move). A winning move adds `"r"`
(`"np"` or `"nm"`) and `"w"`, which must be the authenticated mover's
identity. The path must exactly match one of the legal moves in the
receiver's replayed position, so a partial jump, a non-canonical spelling, or
a move that skips a mandatory capture is rejected without changing state.

Moves never carry a board, a legal-move list, or a next-turn identity. Each
peer replays the `moves` history from the starting position and recomputes
the terminal state, reason, winner, and next turn itself.

## Draws

A negotiated draw uses a plain `draw_offer` followed by `draw_accept`.
Repetition and the forty-move rule are claims: `draw_offer` with `{"r":
"3fr"}` when the current position (board plus side to move) has occurred at
least three times, or `{"r": "40m"}` after 80 consecutive plies with no
capture and no man move. A receiver that verifies the claim ends the game as
a draw at once. An ineligible claim becomes a plain offer that the other
player may accept or decline. `draw_offer_reason` in local state names a
claim that is currently available.

## Local session metadata

| Key | Meaning |
|-----|---------|
| `board` | 64-cell local board derived from `moves` |
| `moves` | Accepted move paths in order |
| `my_color` | `b` or `w` for the local identity |
| `first_turn` | Black (first-moving) identity |
| `turn` | Identity whose turn is next, or empty after the game ends |
| `move_count` / `last_move` | History length and most recent path |
| `legal_moves` / `must_capture` | Moves available to the player to move |
| `winner` / `terminal` / `terminal_reason` | Result and its reason code |
| `draw_offered` / `draw_offered_by` / `draw_offer_reason` | Draw state |

Hydration replays `moves` and requires the stored board, counts, colors,
turn, terminal state, reason, winner, claim eligibility, and draw-offer owner
to match. When a session expires, any outstanding draw offer is cleared
because it can no longer be answered.

## Cross-language vectors

`tests/vectors/generate_checkers.py` writes the `checkers_*.bin` fixtures
shared byte-for-byte with lrgp-rs. It also defines a 33-ply reference game
that both implementations replay with the same deterministic self-play
policy, so any difference in rules or legal-move ordering fails on both
sides.
