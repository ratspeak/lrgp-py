# Changelog

## Unreleased

### Added

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
