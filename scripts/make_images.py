"""Convert the illustration originals to small WebP files for the website.

Usage: python scripts/make_images.py [--src docs/reference/images-src]
Reads field.png, station.png and farmer.png (git-ignored originals) and writes
web/img/about-field.webp (1600 px wide), station.webp and farmer.webp (960 px),
each under 150 KB. Quality is lowered step by step until a file fits.
"""

import argparse
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "img"
LIMIT = 150 * 1024
JOBS = [("field.png", "about-field.webp", 1600), ("station.png", "station.webp", 960),
        ("farmer.png", "farmer.webp", 960)]


def convert(source, target, width):
    image = Image.open(source).convert("RGB")
    height = round(image.height * width / image.width)
    image = image.resize((width, height), Image.LANCZOS)
    for quality in range(82, 30, -4):
        image.save(target, "WEBP", quality=quality, method=6)
        if target.stat().st_size <= LIMIT:
            return width, height, quality, target.stat().st_size
    raise SystemExit(f"{target.name} is still over 150 KB at quality 34")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--src", default=str(ROOT / "docs" / "reference" / "images-src"))
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for source, target, width in JOBS:
        w, h, quality, size = convert(Path(args.src) / source, OUT / target, width)
        print(f"{target}: {w}x{h}, quality {quality}, {size // 1024} KB")


if __name__ == "__main__":
    main()
