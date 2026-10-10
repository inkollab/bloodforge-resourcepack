#!/usr/bin/env python3
"""
Builds the BloodForge resource pack.

Generates ANIMATED RAINBOW recolours of the vanilla netherite spear and mace, keeping the
original silhouettes and shading, and assembles a pack that overrides nothing in vanilla — the
weapons opt in through the item_model component, so ordinary spears and maces are untouched.

No third-party dependencies: PIL is not available here, so PNG decode/encode is done directly.
Vanilla item textures are palette-indexed (colour type 3); output is RGBA (colour type 6)
because animation frames need per-frame colour.
"""
import json, struct, zlib, os, sys, hashlib, colorsys, random

CLIENT_JAR = os.path.expanduser(
    "~/Library/Application Support/minecraft/versions/1.21.11/1.21.11.jar")
PACK_FORMAT = 75          # from the client's own version.json: pack_version.resource_major
FRAMES = 16               # frames in the rainbow cycle
FRAMETIME = 2             # ticks per frame -> 16*2 = 32 ticks ~= 1.6s per full cycle
SATURATION = 0.95
# The vanilla netherite spear is DARK: opaque values span only 0.14-0.53. Colourising it
# as-is produces a muddy spear that is barely rainbow at all, and a naive "leave dark pixels
# alone" rule left 54% of it grey. Every opaque pixel is therefore remapped onto this
# brightness range, which keeps the original shading order while making the colour vivid.
VALUE_FLOOR = 0.35
VALUE_CEIL = 1.00
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "BloodForge")


# ---------------------------------------------------------------- PNG decode

def _unfilter(raw, w, h, bpp, stride=None):
    stride = stride or w * bpp
    out = bytearray()
    prev = bytearray(stride)
    pos = 0
    for _ in range(h):
        ft = raw[pos]; pos += 1
        line = bytearray(raw[pos:pos + stride]); pos += stride
        if ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                c = prev[i - bpp] if i >= bpp else 0
                b = prev[i]
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        elif ft != 0:
            raise ValueError("bad filter %d" % ft)
        out += line
        prev = line
    return bytes(out)


def read_png(path):
    """Return (width, height, [ (r,g,b,a) ... ]) for indexed or RGBA PNGs."""
    data = open(path, 'rb').read()
    assert data[:8] == b'\x89PNG\r\n\x1a\n', "not a png"
    pos, idat, plte, trns = 8, b'', None, None
    w = h = depth = ctype = None
    while pos < len(data):
        ln = struct.unpack('>I', data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        if typ == b'IHDR':
            w, h, depth, ctype = struct.unpack('>IIBB', body[:10])
        elif typ == b'PLTE':
            plte = body
        elif typ == b'tRNS':
            trns = body
        elif typ == b'IDAT':
            idat += body
        elif typ == b'IEND':
            break
        pos += 12 + ln
    raw = zlib.decompress(idat)
    if ctype == 3:
        # Indexed PNGs may pack several pixels per byte: the vanilla diamond axe is 4-bit.
        # Rows are byte-aligned and the filter works on whole bytes (bpp 1), so unfilter the
        # packed rows first, then unpack each row's indices.
        assert depth in (1, 2, 4, 8), "unsupported indexed depth %s" % depth
        stride = (w * depth + 7) // 8
        packed = _unfilter(raw, w, h, 1, stride)
        px = []
        per_byte, mask = 8 // depth, (1 << depth) - 1
        for y in range(h):
            row = packed[y * stride:(y + 1) * stride]
            for x in range(w):
                byte = row[x // per_byte]
                shift = 8 - depth * (x % per_byte + 1)
                px.append((byte >> shift) & mask)
        out = []
        for idx in px:
            r, g, b = plte[idx * 3], plte[idx * 3 + 1], plte[idx * 3 + 2]
            a = trns[idx] if (trns and idx < len(trns)) else 255
            out.append((r, g, b, a))
        return w, h, out
    assert depth == 8, "only 8-bit depth supported for RGBA, got %s" % depth
    if ctype == 2:
        # Plain RGB — the vanilla armor-trim colour palettes are stored this way.
        px = _unfilter(raw, w, h, 3)
        return w, h, [tuple(px[i:i + 3]) + (255,) for i in range(0, len(px), 3)]
    if ctype == 6:
        px = _unfilter(raw, w, h, 4)
        return w, h, [tuple(px[i:i + 4]) for i in range(0, len(px), 4)]
    raise ValueError("unsupported colour type %d" % ctype)


# ---------------------------------------------------------------- PNG encode

def _chunk(typ, body):
    return (struct.pack('>I', len(body)) + typ + body
            + struct.pack('>I', zlib.crc32(typ + body) & 0xFFFFFFFF))


def write_png(path, w, h, pixels):
    raw = bytearray()
    for y in range(h):
        raw.append(0)                      # filter type 0: none
        for x in range(w):
            raw += bytes(pixels[y * w + x])
    body = (_chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
            + _chunk(b'IDAT', zlib.compress(bytes(raw), 9))
            + _chunk(b'IEND', b''))
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n' + body)


# ---------------------------------------------------------------- rainbow

def rainbowise(w, h, pixels):
    """Stack FRAMES vertically, hue sweeping along the item AND advancing per frame."""
    vals = [max(p[0], p[1], p[2]) / 255.0 for p in pixels if p[3] > 0]
    lo, hi = (min(vals), max(vals)) if vals else (0.0, 1.0)
    span = (hi - lo) or 1.0
    out = []
    for frame in range(FRAMES):
        shift = frame / FRAMES
        for y in range(h):
            for x in range(w):
                r, g, b, a = pixels[y * w + x]
                if a == 0:
                    out.append((0, 0, 0, 0))
                    continue
                # Normalise this pixel's brightness within the texture's own range, then
                # stretch it into the vivid band. Darker pixels stay darker, so the
                # silhouette survives without any pixel being left grey.
                norm = (max(r, g, b) / 255.0 - lo) / span
                value = VALUE_FLOOR + (VALUE_CEIL - VALUE_FLOOR) * norm
                hue = ((y / max(1, h - 1)) + shift) % 1.0
                nr, ng, nb = colorsys.hsv_to_rgb(hue, SATURATION, value)
                out.append((int(nr * 255), int(ng * 255), int(nb * 255), a))
    return w, h * FRAMES, out


# ---------------------------------------------------------------- eye trim

# The trim MATERIAL the God Axe's eye is drawn in. Any vanilla palette name works (quartz,
# gold, netherite, redstone, iron, amethyst, resin...). Diamond, chosen by Roni on 2026-10-03.
TRIM_MATERIAL = "diamond"

# The eye, as (x, y) -> palette index (0 brightest .. 7 darkest), on the vanilla 16x16
# NETHERITE axe. Hand-placed, not computed: an eye at this size is eleven pixels and every one
# of them is a decision. Lids at 1 (glowing — Roni picked the bright-lid variant, #4 of four,
# over the softer lid at 2), iris at 0, pupil at 7. Plus three diamond studs down the haft.
EYE = {
    (8, 3): 1, (9, 3): 1, (10, 3): 1,
    (7, 4): 1, (8, 4): 0, (9, 4): 7, (10, 4): 0, (11, 4): 1,
    (8, 5): 1, (9, 5): 1, (10, 5): 1,
    (7, 8): 1, (5, 11): 1, (3, 13): 1,
}


def eye_trim(w, h, pixels, palette):
    """
    Draws the EYE armor-trim idea onto the vanilla netherite axe: a 5x3 almond eye across the
    blade and diamond studs down the haft.

    Vanilla has no trim textures for ITEMS — trims exist only as worn armour — so this is
    authored, not copied. What IS vanilla is the colour: `palette` is the real diamond
    armour-trim palette (8 entries, bright to dark).

    History: the axe launched on 2026-10-02 as a DIAMOND axe with RIB trim in resin orange
    (bars across the blade, bands on the haft). Replaced on 2026-10-03 at Roni's request.
    """
    out = list(pixels)
    for (x, y), shade in EYE.items():
        # Every pixel must land on the axe itself. If vanilla ever redraws the netherite axe,
        # this fails loudly at build time instead of painting eye pixels into thin air.
        assert pixels[y * w + x][3], "eye pixel (%d,%d) is outside the axe" % (x, y)
        out[y * w + x] = palette[shade][:3] + (255,)
    return out


# ---------------------------------------------------------------- code rain

# The Hacker Spear: a DIAMOND spear turned black-green, with bright code streaming along it from
# the tip into the hand. Variant "B · Data stream", picked by Roni on 2026-10-10 from four
# animated previews (Matrix-style straight-down rain, diamond-tip-only and a bright green head
# were the others).
#
# At 16x16 there is no room for glyphs, so a "character" is one pixel: a near-white head with a
# fading green trail, flickering as it falls the way the film's glyphs change.
RAIN_TRAIL = [(225, 255, 225), (70, 255, 100), (35, 205, 70), (20, 150, 50), (12, 105, 35)]
RAIN_SEED = 7             # fixes the lane layout so every rebuild is the same animation
RAIN_FLICKER = 0.3        # chance per frame that a trail pixel dims a step: a glyph changing
# The four darkest vanilla colours draw the spear's edge. The first render let rain run over
# them and the head dissolved into a green blob, so the outline never streams.
RAIN_OUTLINE_BELOW = 70


def coderain(w, h, pixels, frames):
    """
    Stack `frames` vertically. Drops travel ALONG the spear, tip to hand, rather than down the
    picture, so the flow still reads correctly once the in-hand model rotates the texture.

    Position along the spear is t = the pixel's distance along the diagonal; a lane is one line
    of pixels parallel to the shaft. Diagonal neighbours differ by 2 in t, so drops advance 2 per
    frame (one pixel) and the cycle is 2*frames long, which is what makes the loop seamless.
    """
    vals = [max(p[:3]) / 255.0 for p in pixels if p[3] > 0]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    # The GUI texture points its tip top-right, the in-hand texture top-left.
    tip_left = pixels[0][3] > 0 or pixels[w][3] > 0
    period = frames * 2
    rnd = random.Random(RAIN_SEED)
    lanes = {}
    out = []
    for frame in range(frames):
        flicker = random.Random(RAIN_SEED * 1000 + frame)
        for y in range(h):
            for x in range(w):
                r, g, b, a = pixels[y * w + x]
                if a == 0:
                    out.append((0, 0, 0, 0))
                    continue
                # Base: the vanilla shading, darkened onto a black-green terminal range.
                norm = (max(r, g, b) / 255.0 - lo) / span
                nr, ng, nb = colorsys.hsv_to_rgb(0.36, 0.9, 0.07 + 0.25 * norm)
                colour = (int(nr * 255), int(ng * 255), int(nb * 255))
                if tip_left:
                    lane, t = x - y, x + y
                else:
                    lane, t = x + y, y - x + (w - 1)
                if lane not in lanes:
                    lanes[lane] = (rnd.randrange(period), 1 if rnd.random() < 0.55 else 2)
                phase, drops = lanes[lane]
                if max(r, g, b) >= RAIN_OUTLINE_BELOW:
                    head = (frame * 2 + phase) % period
                    behind = min(((head + k * period // drops - t) % period) // 2
                                 for k in range(drops))
                    if behind < len(RAIN_TRAIL):
                        if behind > 0 and flicker.random() < RAIN_FLICKER:
                            behind = min(behind + 1, len(RAIN_TRAIL) - 1)
                        colour = RAIN_TRAIL[behind]
                out.append(colour + (a,))
    return w, h * frames, out


# ---------------------------------------------------------------- build

def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(obj, f, indent=2)


def main():
    src = sys.argv[1]  # directory holding extracted vanilla assets
    tex_in = os.path.join(src, "assets/minecraft/textures/item")

    for d in ["assets/bloodforge/textures/item", "assets/bloodforge/models/item",
              "assets/bloodforge/items"]:
        os.makedirs(os.path.join(OUT, d), exist_ok=True)

    write_json(os.path.join(OUT, "pack.mcmeta"), {
        "pack": {
            "pack_format": PACK_FORMAT,
            "description": "BloodForge — rainbow God Spear and God Mace, eye-trimmed God Axe, code-rain Hacker Spear"
        }
    })

    for name, src_name in [("god_spear", "netherite_spear"),
                           ("god_spear_in_hand", "netherite_spear_in_hand"),
                           ("blood_mace", "mace")]:
        w, h, px = read_png(os.path.join(tex_in, src_name + ".png"))
        nw, nh, npx = rainbowise(w, h, px)
        write_png(os.path.join(OUT, "assets/bloodforge/textures/item", name + ".png"),
                  nw, nh, npx)
        write_json(os.path.join(OUT, "assets/bloodforge/textures/item", name + ".png.mcmeta"),
                   {"animation": {"frametime": FRAMETIME}})
        print("  texture %-20s %dx%d -> %dx%d (%d frames)" % (name, w, h, nw, nh, FRAMES))

    # Mirrors vanilla netherite_spear.json exactly: flat model in the GUI, spear model in hand.
    write_json(os.path.join(OUT, "assets/bloodforge/items/god_spear.json"), {
        "model": {
            "type": "minecraft:select",
            "property": "minecraft:display_context",
            "cases": [{
                "when": ["gui", "ground", "fixed", "on_shelf"],
                "model": {"type": "minecraft:model", "model": "bloodforge:item/god_spear"}
            }],
            "fallback": {"type": "minecraft:model",
                         "model": "bloodforge:item/god_spear_in_hand"}
        },
        "swap_animation_scale": 1.95
    })
    write_json(os.path.join(OUT, "assets/bloodforge/models/item/god_spear.json"), {
        "parent": "minecraft:item/generated",
        "textures": {"layer0": "bloodforge:item/god_spear"}
    })
    write_json(os.path.join(OUT, "assets/bloodforge/models/item/god_spear_in_hand.json"), {
        "parent": "minecraft:item/spear_in_hand",
        "textures": {"layer0": "bloodforge:item/god_spear_in_hand"}
    })

    # The mace is far simpler than the spear: vanilla's own items/mace.json is a plain
    # model reference with no display_context select and no separate in-hand texture, so
    # this mirrors that shape exactly. The parent MUST stay `handheld_mace` — that is what
    # gives the mace its distinctive grip and swing arc; `generated` would flatten it into
    # a signboard held edge-on.
    write_json(os.path.join(OUT, "assets/bloodforge/items/blood_mace.json"), {
        "model": {"type": "minecraft:model", "model": "bloodforge:item/blood_mace"}
    })
    write_json(os.path.join(OUT, "assets/bloodforge/models/item/blood_mace.json"), {
        "parent": "minecraft:item/handheld_mace",
        "textures": {"layer0": "bloodforge:item/blood_mace"}
    })

    # The God Axe: a static eye-trimmed NETHERITE axe, not a rainbow. The `handheld` parent is
    # vanilla's own for every axe — it is what holds the head up and out instead of flat.
    w, h, px = read_png(os.path.join(tex_in, "netherite_axe.png"))
    _, _, palette = read_png(os.path.join(src, "assets/minecraft/textures/trims/color_palettes",
                                          TRIM_MATERIAL + ".png"))
    write_png(os.path.join(OUT, "assets/bloodforge/textures/item/god_axe.png"),
              w, h, eye_trim(w, h, px, palette))
    print("  texture %-20s %dx%d (eye trim, %s)" % ("god_axe", w, h, TRIM_MATERIAL))
    write_json(os.path.join(OUT, "assets/bloodforge/items/god_axe.json"), {
        "model": {"type": "minecraft:model", "model": "bloodforge:item/god_axe"}
    })
    write_json(os.path.join(OUT, "assets/bloodforge/models/item/god_axe.json"), {
        "parent": "minecraft:item/handheld",
        "textures": {"layer0": "bloodforge:item/god_axe"}
    })

    # The Hacker Spear mirrors the God Spear's two-texture structure exactly. Both textures loop
    # in the same 1.6 s, so the rain crosses the spear at the same pace in the hand and the GUI:
    # 16 frames x 2 ticks for the 16px icon, 32 frames x 1 tick for the 32px in-hand texture.
    for name, src_name, frames, frametime in [
            ("hacker_spear", "diamond_spear", 16, 2),
            ("hacker_spear_in_hand", "diamond_spear_in_hand", 32, 1)]:
        w, h, px = read_png(os.path.join(tex_in, src_name + ".png"))
        nw, nh, npx = coderain(w, h, px, frames)
        write_png(os.path.join(OUT, "assets/bloodforge/textures/item", name + ".png"), nw, nh, npx)
        write_json(os.path.join(OUT, "assets/bloodforge/textures/item", name + ".png.mcmeta"),
                   {"animation": {"frametime": frametime}})
        print("  texture %-20s %dx%d -> %dx%d (%d frames, code rain)" % (name, w, h, nw, nh, frames))
    write_json(os.path.join(OUT, "assets/bloodforge/items/hacker_spear.json"), {
        "model": {
            "type": "minecraft:select",
            "property": "minecraft:display_context",
            "cases": [{
                "when": ["gui", "ground", "fixed", "on_shelf"],
                "model": {"type": "minecraft:model", "model": "bloodforge:item/hacker_spear"}
            }],
            "fallback": {"type": "minecraft:model",
                         "model": "bloodforge:item/hacker_spear_in_hand"}
        },
        "swap_animation_scale": 1.95
    })
    write_json(os.path.join(OUT, "assets/bloodforge/models/item/hacker_spear.json"), {
        "parent": "minecraft:item/generated",
        "textures": {"layer0": "bloodforge:item/hacker_spear"}
    })
    write_json(os.path.join(OUT, "assets/bloodforge/models/item/hacker_spear_in_hand.json"), {
        "parent": "minecraft:item/spear_in_hand",
        "textures": {"layer0": "bloodforge:item/hacker_spear_in_hand"}
    })

    zip_path = OUT + ".zip"
    if os.path.exists(zip_path):
        os.remove(zip_path)
    import zipfile
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(OUT):
            for fn in files:
                full = os.path.join(root, fn)
                z.write(full, os.path.relpath(full, OUT))

    sha1 = hashlib.sha1(open(zip_path, 'rb').read()).hexdigest()
    print("\npack:  %s (%d bytes)" % (zip_path, os.path.getsize(zip_path)))
    print("sha1:  %s" % sha1)
    with open(OUT + ".sha1", 'w') as f:
        f.write(sha1 + "\n")


if __name__ == "__main__":
    main()
