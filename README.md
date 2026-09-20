# BloodForge Resource Pack

Server-pushed resource pack for a private Minecraft 1.21.11 Paper server.

Gives the **God Spear** and the **Blood Mace** animated rainbow textures. The weapons opt in
via the vanilla `item_model` item component, so ordinary spears and maces are untouched.

The pack carries delight, never meaning: a player who declines it sees plain vanilla items and
can still read their tier from the weapon's name, upgrade sound and messages.

- `pack_format` **75** (Minecraft 1.21.11)
- 16 animation frames at 2 ticks each — a full colour cycle every ~1.6s
- Built by `build-pack.py`, which recolours the vanilla textures programmatically
  (no third-party image libraries)
- The spear needs two textures (flat for the GUI, `spear_in_hand` for the held model); the
  mace needs one, and keeps the `handheld_mace` model parent for its grip and swing arc

## Rebuilding

```
python3 build-pack.py <dir containing extracted vanilla assets>
```

The script prints the zip path and its sha1, which the server needs for client caching.
