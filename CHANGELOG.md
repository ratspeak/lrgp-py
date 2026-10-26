# Changelog

## Unreleased

### Added

- **Ultimate Tic-Tac-Toe (`ultimate_ttt.1`)** — nine Tic-Tac-Toe boards on
  one 9x9 board, built on the single-token engine. The cell you take sends
  your opponent to the matching small board; three small boards in a row
  wins. Moves are a square such as `e5`. A reference game and a draw are
  shared with `lrgp-rs`. SPEC.md Appendix G.1 is normative.
- **Go (`go.1`)** — 9x9 Go with area scoring, positional superko, and 7.5
  komi. Moves are a point such as `e5` or `pass`; two passes in a row end
  the game and every stone left on the board counts. A reference game won
  on komi is shared with `lrgp-rs`. SPEC.md Appendix G.2 is normative.
- **Battleship (`battleship.1`)** — the first game with hidden
  information. Each player commits to a 10x10 fleet with a SHA-256 hash of
  the fleet and a random salt; every shot carries the answer to the
  opponent's last shot, and both fleets are revealed at the end so each
  side checks the other's answers. Uses only the standard library. A
  reference game is shared with `lrgp-rs`. SPEC.md Appendix H is normative.
- **Quoridor (`quoridor.1`)** — race a pawn across a 9x9 board while
  placing walls in the other player's way, without ever shutting a pawn in.
  Moves are a square (`e2`) or a wall (`c3h`, `c3v`); a third repetition
  draws. A reference game and a draw are shared with `lrgp-rs`. SPEC.md
  Appendix G.3 is normative.
- **Nine Men's Morris (`morris.1`)** — place nine pieces, then slide them
  along the lines, flying once down to three; every mill removes an
  opponent piece. Moves read `d2`, `d2-d3`, or `d2-d3xg7` with a removal.
  Repetition and 100 quiet plies draw. A reference game and a draw are
  shared with `lrgp-rs`. SPEC.md Appendix G.4 is normative. Single-token
  rules can now give more than one win or draw reason.
- **Breakthrough (`breakthrough.1`)** — two rows of pieces each on an 8x8
  board, stepping forward and capturing diagonally; the first piece to the
  far rank wins. Moves read `a2b3`. A reference game is shared with
  `lrgp-rs`. SPEC.md Appendix G.5 is normative.
- **Amazons (`amazons.1`)** — move a queen-like amazon on a 10x10 board,
  then shoot an arrow that burns a square for good; whoever runs out of
  moves loses. Moves read `d1d7/g7`. A reference game is shared with
  `lrgp-rs`. SPEC.md Appendix G.6 is normative.
- **Pente (`pente.1`)** — Gomoku on a 19x19 board with captures:
  flanking two opposing stones removes them, and five in a row or five
  captured pairs wins. A reference game won on captures is shared with
  `lrgp-rs`. SPEC.md Appendix G.7 is normative.

- **Hex (`hex.1`), Gomoku (`gomoku.1`), and Reversi (`reversi.1`)** —
  dependency-free stone-placement games sharing one wire contract: moves are
  a single square such as `h8` in `{m, n, x}` (plus `w` on a win), and each
  peer replays the history. Hex is 11x11 with the swap rule, Gomoku is 15x15
  freestyle, and Reversi passes automatically, so its winner can be the
  non-mover. Binary fixtures and reference games are shared with `lrgp-rs`.

- **Dots and Boxes (`dots_and_boxes.1`) and Mancala (`mancala.1`)** — two
  more dependency-free games on the same engine and wire contract. Dots and
  Boxes is a 5x5-box grid with moves naming a line (`c3h`, `c3v`);
  completing a box claims it and earns another move. Mancala uses Kalah rules
  with six pits of four seeds, moves naming a pit (`a`–`l`), extra turns for
  ending in your store, and captures from an empty pit of your own. Each peer
  derives who moves next, so extra turns need no message. Reference games
  (both won by the player who did not move last) and a 24–24 Mancala draw are
  shared with `lrgp-rs`.

- **Checkers (`checkers.1`)** — fourth built-in game: dependency-free
  American checkers / English draughts with mandatory captures, completed
  multi-jumps, crowning that ends the move, both-side validation by replay,
  and claim-based threefold-repetition and forty-move draws. Moves carry a
  space-separated square path such as `a5 c3 e1` in `{m, n, x}` (plus `r`/`w`
  for a win). Hydration replays the stored history. Binary fixtures and a
  33-ply reference game are shared with `lrgp-rs`.

- **Four in a Row (`four_in_a_row.1`)** — third built-in game, with a
  dependency-free 7-column by 6-row gravity engine, fixed challenger-first
  `A`/`B` roles, both-side validation, negotiated draws, semantic session
  hydration, and compact `{c,n,x}` moves that reconstruct board and turn
  locally. Deterministic binary fixtures are shared with `lrgp-rs`.

## 0.4.0 — 2026-08-04

### Breaking

- Canonical envelope, session ID, native LXMF field types, strict built-in
  payloads, participant binding, and scoped replay semantics now match
  `lrgp-rs` and the normative specification.
- `LrgpStore.save_session()` is insert-only. Existing records must be changed
  through the explicit mutable-field allowlist in `update_session()`.
- The optional LXMF bridge forwards LRGP messages only when LXMF has validated
  the sender signature.

### Added

- Typed router results, explicit-participant outgoing preparation, TTL-aware
  hydration/list/removal, challenge-admission limits, and public transactional
  snapshot/rollback helpers for durable inbound and outbound integration.
- Draw-offer ownership, legacy hydration normalization, strict terminal claim
  verification, canonical Rust interoperability vectors, and duplicate-key /
  trailing-byte decoder rejection.

## 0.3.0 — 2026-05-01

### Added

- **Chess (`chess.1`)** — second built-in game alongside Tic-Tac-Toe. UCI-only wire format, board state replayed locally via [python-chess](https://pypi.org/project/chess/), two-side validation. Terminal reason codes (`cm`, `sm`, `ins`, `3fr`, `50m`, `rsn`, `agr`) keep move envelopes ≤150 bytes. Available as `lrgp.apps.chess.ChessApp`.
- **`[chess]` extra** — `pip install 'lrgp[chess]'` pulls in `chess>=1.10`. The chess module raises a clear `ImportError` if imported without the extra. Core lib stays zero-deps.
- **`examples/chess_local.py`** — Scholar's Mate walkthrough between two `ChessApp` instances. Pins the coin via `force_coin(True)` for determinism.
- **Chess binary test vectors** — `tests/vectors/chess_*.bin`. Byte-identical to `lrgp-rs/tests/chess_*.bin`; a divergence in either repo's vectors would surface immediately on test re-run.
- **License switched to MIT** — was AGPL-3.0 in v0.2.0, now matches the long-standing pyproject.toml declaration.

### Changed

- **Wire `n` is now required.** Every envelope MUST carry an 8-byte CSPRNG nonce under key `n`. `unpack_envelope` rejects missing/malformed nonce. `ReplayDedup.check` returns `True` (drop) on protocol violations.
- **SPEC.md updated to v0.3** — section 3.1 documents the replay-protection mechanism; appendix B documents the Chess reference game; legacy-marker section removed.
- **`docs/wire-format.md` rewritten** — no longer references RLAP or the 4-key envelope; includes the Chess move shape and key-ordering note.

### Removed

- **Legacy protocol markers `rlap.v1` and `ratspeak.game`** are no longer recognized on inbound. Pre-release implementations using these markers must upgrade to `lrgp.v1`.
- **`GameBase.migrate_legacy()`** — no longer needed now that legacy markers are gone.
- **The `LEGACY_TYPES` constant.**

---

## 0.2.0 — 2026-03-12

### Breaking — Renamed to LRGP

RLAP (Reticulum LXMF App Protocol) has been renamed and re-purposed to **LRGP** (Lightweight Reticulum Gaming Protocol). The protocol now focuses specifically on multiplayer gaming over Reticulum mesh networks.

#### Wire Protocol
- Protocol marker: `rlap.v1` -> `lrgp.v1`
- Legacy `rlap.v1` and `ratspeak.game` messages still recognized.
- All outbound messages use `lrgp.v1`

#### API Renames
- `AppBase` -> `GameBase`
- `RlapStore` -> `LrgpStore`
- `RlapTransport` -> `LrgpTransport`
- `RlapError` -> `LrgpError`
- `rlap` module renamed to `lrgp`

#### New Features
- `GameBase` adds `min_players`, `genre`, and `turn_timeout` fields
- New game session types: `round_based`, `single_round`
- `game_sessions` and `game_actions` database tables replacing old ones.

#### Fallback Text
- Format changed from `[RLAP ...]` to `[LRGP ...]`.

---

## 0.1.0 — 2026-03-05

- Initial release
- RLAP protocol specification
- Core library: envelope, session, router, store
- Tic-Tac-Toe app with full validation
- Local simulation example
- Test suite with wire budget verification and interop vectors
