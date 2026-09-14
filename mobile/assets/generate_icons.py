#!/usr/bin/env python3
"""
Generates Pathfinder Run's actual app icon set, replacing the unmodified
Expo template default (still had the design-guideline overlay circles
visible in the exported PNG -- confirmed never replaced). Run once;
regenerate by re-running this script if the design ever changes, not by
hand-editing the PNGs.

Design: a loop (ring) with a start/end marker dot -- directly represents
what this app actually does (generates loop running routes), not a
generic abstract mark. Green matches the app's own route-polyline color
(#2E7D32, mobile/App.js) for consistency between the icon and the app's
actual UI, not a color picked independently of the product.
"""
from PIL import Image, ImageDraw

GREEN = (46, 125, 50)       # #2E7D32 -- exact match to App.js's route polyline color
GREEN_LIGHT = (76, 175, 80)  # a lighter shade for a subtle background gradient feel
WHITE = (255, 255, 255)


def vertical_gradient(size, top_color, bottom_color):
    """A smooth top-to-bottom gradient, since PIL has no built-in RGB
    gradient primitive -- Image.linear_gradient only produces a
    single-channel (L-mode) ramp, used here as a per-row interpolation
    factor between the two flat colors rather than a hard band split."""
    base = Image.new("RGB", (size, size))
    top = Image.new("RGB", (size, size), top_color)
    bottom = Image.new("RGB", (size, size), bottom_color)
    mask = Image.linear_gradient("L").resize((size, size))
    return Image.composite(bottom, top, mask)


def draw_loop_glyph(draw, cx, cy, radius, ring_width, dot_radius, color):
    """The actual glyph: a ring (the loop route) with a filled dot at its
    top (the start/end point every generated loop returns to)."""
    bbox = [cx - radius, cy - radius, cx + radius, cy + radius]
    draw.ellipse(bbox, outline=color, width=ring_width)
    dot_cx, dot_cy = cx, cy - radius
    draw.ellipse(
        [dot_cx - dot_radius, dot_cy - dot_radius, dot_cx + dot_radius, dot_cy + dot_radius],
        fill=color,
    )


def make_icon(size, background=True):
    """One square canvas at `size`, glyph scaled proportionally. background=False
    produces a transparent-background glyph-only layer (Android adaptive
    icon foreground)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if background:
        img.paste(vertical_gradient(size, GREEN_LIGHT, GREEN))
        draw = ImageDraw.Draw(img)
        glyph_color = WHITE
    else:
        glyph_color = GREEN

    cx = cy = size / 2
    radius = size * 0.28
    ring_width = max(2, int(size * 0.075))
    dot_radius = size * 0.075
    draw_loop_glyph(draw, cx, cy, radius, ring_width, dot_radius, glyph_color)
    return img if not background else img.convert("RGB")


def make_monochrome(size):
    """Android's themed-icon monochrome layer: glyph only, single color
    (white -- the OS applies its own tint), transparent background."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx = cy = size / 2
    radius = size * 0.28
    ring_width = max(2, int(size * 0.075))
    dot_radius = size * 0.075
    draw_loop_glyph(draw, cx, cy, radius, ring_width, dot_radius, WHITE)
    return img


def make_background_only(size):
    return vertical_gradient(size, GREEN_LIGHT, GREEN).convert("RGBA")


if __name__ == "__main__":
    make_icon(1024, background=True).save("icon.png")
    make_icon(512, background=False).save("android-icon-foreground.png")
    make_background_only(512).save("android-icon-background.png")
    make_monochrome(432).save("android-icon-monochrome.png")
    make_icon(48, background=True).convert("RGBA").save("favicon.png")
    make_icon(1024, background=True).convert("P", palette=Image.ADAPTIVE).save("splash-icon.png")
    print("wrote icon.png, android-icon-foreground.png, android-icon-background.png, "
          "android-icon-monochrome.png, favicon.png, splash-icon.png")
