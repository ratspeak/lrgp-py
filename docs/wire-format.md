# Wire Format

LRGP messages are encoded as LXMF custom fields using msgpack serialization.

## LXMF Content Structure

LXMF packs message content as:

```
msgpack([timestamp, title, content, fields_dict])
```

Where `fields_dict` contains the LRGP envelope:

```
{
    0xFB: "lrgp.v1",            # FIELD_CUSTOM_TYPE
    0xFD: {                     # FIELD_CUSTOM_META (envelope)
        "a": "ttt.1",
        "c": "move",
        "s": "a1b2c3d4e5f60718",
        "p": { ... },
        "n": <8 bytes>,         # CSPRNG replay-dedup nonce
    }
}
```

The `n` field is a fresh 8-byte CSPRNG nonce (msgpack `bin8`) on every outbound envelope. Receivers run `(receiving_identity_id, session_id, n)` through a bounded cache (512 nonces per scope, 1024 scopes, 600s absolute TTL) to drop duplicates. Terminal sessions retain entries through TTL. See [SPEC.md §3.1](../SPEC.md) for the full replay-protection contract.

## Hex Examples

### Challenge

Envelope dict (~57 bytes packed including the 8-byte nonce):

```
85                          # fixmap(5)
  a1 61                     # "a"
  a5 74 74 74 2e 31         # "ttt.1"
  a1 63                     # "c"
  a9 63 68 61 6c 6c 65 6e 67 65  # "challenge"
  a1 73                     # "s"
  b0 61 31 62 32 63 33 64 34 65 35 66 36 30 37 31 38  # "a1b2c3d4e5f60718"
  a1 70                     # "p"
  80                        # fixmap(0) (empty payload)
  a1 6e                     # "n"
  c4 08 <8 nonce bytes>     # bin8 length 8
```

### Move (normal)

Envelope dict (~85 bytes packed):

```
85                          # fixmap(5)
  a1 61  a5 74 74 74 2e 31  # "a": "ttt.1"
  a1 63  a4 6d 6f 76 65     # "c": "move"
  a1 73  b0 ...             # "s": session_id (16 chars)
  a1 70                     # "p":
  86                        # fixmap(6)
    a1 69  04               # "i": 4
    a1 62  a9 5f 5f 5f 5f 58 5f 5f 5f 5f  # "b": "____X____"
    a1 6e  01               # "n": 1
    a1 74  b0 ...           # "t": next_turn_hash
    a1 78  a0               # "x": "" (not terminal)
    a1 77  a0               # "w": "" (no winner yet)
  a1 6e  c4 08 <nonce>      # envelope nonce
```

### Move (win)

Same structure, payload's `"x"` becomes `"win"`, `"r"` (reason) is set, `"w"` carries the winner hash.

### Chess move (UCI only)

```
85                          # fixmap(5)
  a1 61  a7 63 68 65 73 73 2e 31  # "a": "chess.1"
  a1 63  a4 6d 6f 76 65     # "c": "move"
  a1 73  b0 ...             # "s": session_id
  a1 70                     # "p":
  85                        # fixmap(5)
    a1 6d  a4 65 32 65 34   # "m": "e2e4"
    a1 6e  00               # "n": 0 (ply, 0-based)
    a1 78  a0               # "x": ""
    a1 72  a0               # "r": ""
    a1 77  a0               # "w": ""
  a1 6e  c4 08 <nonce>
```

## Size Budget

| Component | Budget |
|-----------|--------|
| Envelope dict (packed) | max 200 B |
| Full LXMF content | max 295 B (OPPORTUNISTIC) |
| Full LXMF content | max 319 B (DIRECT/PROPAGATED packet) |
| LXMF overhead | 112 B (hashes + signature + timestamp) |

Every TTT and Chess action fits comfortably within OPPORTUNISTIC limits — worst-case observed across the canonical test vectors is ~110 B for a chess checkmate envelope (carrying winner hash + reason code + nonce).

## Key Ordering

msgpack maps are unordered by spec. Implementations MUST NOT compare envelopes byte-for-byte and MUST decode by key lookup. Two implementations may emit the same envelope with different on-the-wire byte ordering and both are equally conformant. Decoders reject duplicate map keys at every nesting level, and byte-oriented decoders reject trailing data after the one envelope.
