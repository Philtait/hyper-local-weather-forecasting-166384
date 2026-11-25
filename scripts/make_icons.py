"""Draw the Imarika PWA icons (web/icons/) with Pillow only.

Usage: python scripts/make_icons.py
Writes icon-192.png, icon-512.png and icon-maskable-512.png: a white rain
drop on the site's navy. The maskable icon keeps the drop inside the
central safe zone (80% circle) so launchers can crop it to any shape.
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "web" / "icons"
NAVY = (11, 46, 107, 255)
WHITE = (255, 255, 255, 255)
ACCENT = (125, 152, 219, 255)


def drop(draw, cx, cy, height):
    """A rain drop: a circle with a triangle on top, height measured tip to base."""
    radius = height * 0.36
    centre_y = cy + height / 2 - radius
    tip = (cx, cy - height / 2)
    draw.ellipse([cx - radius, centre_y - radius, cx + radius, centre_y + radius], fill=WHITE)
    # The sides touch the circle at its tangent points, so the outline has no notch.
    distance = centre_y - tip[1]
    sin_a = radius / distance
    cos_a = (1 - sin_a ** 2) ** 0.5
    left = (cx - radius * cos_a, centre_y - radius * sin_a)
    right = (cx + radius * cos_a, centre_y - radius * sin_a)
    draw.polygon([tip, left, right], fill=WHITE)
    # a small highlight arc
    inner = radius * 0.58
    draw.arc([cx - inner, centre_y - inner, cx + inner, centre_y + inner], start=110, end=170,
             fill=ACCENT, width=max(2, int(height * 0.045)))


def icon(size, maskable=False, scale=4):
    big = size * scale
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if maskable:
        draw.rectangle([0, 0, big, big], fill=NAVY)
        drop(draw, big / 2, big / 2, big * 0.5)
    else:
        draw.rounded_rectangle([0, 0, big - 1, big - 1], radius=big * 0.22, fill=NAVY)
        drop(draw, big / 2, big / 2, big * 0.62)
    return image.resize((size, size), Image.LANCZOS)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    icon(192).save(OUT / "icon-192.png")
    icon(512).save(OUT / "icon-512.png")
    icon(512, maskable=True).save(OUT / "icon-maskable-512.png")
    print(f"Wrote icons to {OUT}")


if __name__ == "__main__":
    main()
