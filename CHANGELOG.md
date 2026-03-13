# Changelog

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
