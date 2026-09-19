# BloodForge Resource Pack

Server-pushed resource pack for a private Minecraft 1.21.11 Paper server.

Gives the **God Spear** an animated rainbow texture. The weapons opt in via the vanilla
`item_model` item component, so ordinary netherite spears are untouched.

- `pack_format` **75** (Minecraft 1.21.11)
- 16 animation frames at 2 ticks each — a full colour cycle every ~1.6s
- Built by `build-pack.py`, which recolours the vanilla spear texture programmatically
  (no third-party image libraries)

## Rebuilding

```
python3 build-pack.py <dir containing extracted vanilla assets>
```

The script prints the zip path and its sha1, which the server needs for client caching.
