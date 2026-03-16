"""Generate the Portfolio Maker application icon.

Creates a multi-size .ico file with the Sentinel drone/grid motif.
Run once: python generate_icon.py
"""

import math
from PIL import Image, ImageDraw, ImageFont

SENTINEL_PURPLE = "#5B2C6F"
SENTINEL_LIGHT = "#AF7AC5"
ACCENT_GOLD = "#F4D03F"
WHITE = "#FFFFFF"
DARK = "#1A0A2E"


def draw_icon(size):
    """Draw the Portfolio Maker icon at a given size."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = size * 0.06
    r = size * 0.18  # corner radius

    # Background: rounded rectangle with gradient feel
    draw.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=r,
        fill=SENTINEL_PURPLE,
    )

    # Inner border glow
    draw.rounded_rectangle(
        [pad + 2, pad + 2, size - pad - 2, size - pad - 2],
        radius=r - 1,
        outline=SENTINEL_LIGHT,
        width=max(1, size // 64),
    )

    cx, cy = size / 2, size / 2
    unit = size / 16  # base unit for scaling elements

    # ── Grid pattern (represents sorted photos / nadir grid) ──
    grid_left = cx - unit * 3.5
    grid_top = cy - unit * 1
    cell = unit * 1.6
    gap = unit * 0.4

    for row in range(3):
        for col in range(4):
            x1 = grid_left + col * (cell + gap)
            y1 = grid_top + row * (cell + gap)
            x2 = x1 + cell
            y2 = y1 + cell

            # Alternate colors: nadir (light) vs oblique (dark)
            if (row + col) % 3 == 0:
                fill = ACCENT_GOLD
                outline = None
            elif (row + col) % 2 == 0:
                fill = SENTINEL_LIGHT
                outline = None
            else:
                fill = DARK
                outline = SENTINEL_LIGHT

            cr = max(1, size // 48)
            draw.rounded_rectangle([x1, y1, x2, y2], radius=cr, fill=fill, outline=outline,
                                    width=max(1, size // 128))

    # ── Drone silhouette at top ──
    drone_cy = cy - unit * 3.2
    arm_len = unit * 2.2
    body_r = unit * 0.7
    prop_r = unit * 0.9
    arm_w = max(2, size // 40)

    # Body circle
    draw.ellipse(
        [cx - body_r, drone_cy - body_r, cx + body_r, drone_cy + body_r],
        fill=WHITE,
    )

    # Arms + props (X pattern)
    for angle in [45, 135, 225, 315]:
        rad = math.radians(angle)
        ax = cx + arm_len * math.cos(rad)
        ay = drone_cy + arm_len * math.sin(rad)

        # Arm line
        draw.line([cx, drone_cy, ax, ay], fill=WHITE, width=arm_w)

        # Propeller circle
        draw.ellipse(
            [ax - prop_r, ay - prop_r, ax + prop_r, ay + prop_r],
            outline=WHITE,
            width=max(1, size // 64),
        )

    # Camera dot on body
    cam_r = unit * 0.25
    draw.ellipse(
        [cx - cam_r, drone_cy - cam_r, cx + cam_r, drone_cy + cam_r],
        fill=SENTINEL_PURPLE,
    )

    return img


def main():
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [draw_icon(s) for s in sizes]

    # Save .ico with all sizes
    images[-1].save(
        "portfolio_maker.ico",
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=images[:-1],
    )

    # Also save a PNG for other uses
    png = draw_icon(512)
    png.save("portfolio_maker.png", format="PNG")

    print(f"Created portfolio_maker.ico ({len(sizes)} sizes)")
    print(f"Created portfolio_maker.png (512x512)")


if __name__ == "__main__":
    main()
