# Wire Format

RLAP messages are encoded as LXMF custom fields using msgpack serialization.

## LXMF Content Structure

LXMF packs message content as:

```
msgpack([timestamp, title, content, fields_dict])
```

Where `fields_dict` contains the RLAP envelope:

```
{
    0xFB: "rlap.v1",           # FIELD_CUSTOM_TYPE
    0xFD: {                     # FIELD_CUSTOM_META (envelope)
        "a": "ttt.1",
        "c": "move",
        "s": "a1b2c3d4e5f6g7h8",
        "p": { ... }
    }
}
```

## Hex Examples

### Challenge

Envelope dict (43 bytes packed):
```
84                          # fixmap(4)
  a1 61                     # fixstr "a"
  a5 74 74 74 2e 31         # fixstr "ttt.1"
  a1 63                     # fixstr "c"
  a9 63 68 61 6c 6c 65 6e 67 65  # fixstr "challenge"
  a1 73                     # fixstr "s"
  b0 61 31 62 32 63 33 64 34 65 35 66 36 67 37 68 38  # fixstr "a1b2c3d4e5f6g7h8"
  a1 70                     # fixstr "p"
  80                        # fixmap(0)  (empty payload)
```

### Move (normal)

Envelope dict (78 bytes packed):
```
84                          # fixmap(4)
  a1 61  a5 74 74 74 2e 31  # "a": "ttt.1"
  a1 63  a4 6d 6f 76 65     # "c": "move"
  a1 73  b0 ...             # "s": session_id (16 chars)
  a1 70                     # "p":
  85                        # fixmap(5)
    a1 69  04               # "i": 4
    a1 62  a9 5f 5f 5f 5f 58 5f 5f 5f 5f  # "b": "____X____"
    a1 6e  01               # "n": 1
    a1 74  b0 ...           # "t": next_turn_hash
    a1 78  a0               # "x": "" (not terminal)
```

### Move (win)

Same structure, but payload includes `"w"` (winner hash) and `"x"` = `"win"`.
Total envelope: ~84 bytes packed.

## Size Budget

| Component | Budget |
|-----------|--------|
| Envelope dict (packed) | max 200 B |
| Full LXMF content | max 295 B (OPPORTUNISTIC) |
| Full LXMF content | max 319 B (DIRECT/PROPAGATED packet) |
| LXMF overhead | 112 B (hashes + signature + timestamp) |

Every TTT action fits comfortably within OPPORTUNISTIC limits (worst case: 140 B full content, 155 B headroom).
