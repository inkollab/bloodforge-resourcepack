#!/usr/bin/env python3
"""
Builds the BloodForge resource pack.

Generates an ANIMATED RAINBOW recolour of the vanilla netherite spear, keeping the original
silhouette and shading, and assembles a pack that overrides nothing in vanilla — the weapons
opt in through the item_model component, so ordinary netherite spears are untouched.

No third-party dependencies: PIL is not available here, so PNG decode/encode is done directly.
Vanilla item textures are palette-indexed (colour type 3); output is RGBA (colour type 6)
because animation frames need per-frame colour.
"""
import json, struct, zlib, os, sys, hashlib, colorsys

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

def _unfilter(raw, w, h, bpp):
    stride = w * bpp
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
    assert depth == 8, "only 8-bit depth supported, got %s" % depth

    raw = zlib.decompress(idat)
    if ctype == 3:
        px = _unfilter(raw, w, h, 1)
        out = []
        for idx in px:
            r, g, b = plte[idx * 3], plte[idx * 3 + 1], plte[idx * 3 + 2]
            a = trns[idx] if (trns and idx < len(trns)) else 255
            out.append((r, g, b, a))
        return w, h, out
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
            "description": "BloodForge — rainbow God Spear"
        }
    })

    for name, src_name in [("god_spear", "netherite_spear"),
                           ("god_spear_in_hand", "netherite_spear_in_hand")]:
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
