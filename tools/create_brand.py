"""Prepare official HealthLog icons from a downloaded upstream 512px PNG.

Run with: uv run --with pillow python tools/create_brand.py /path/to/logo-512.png
Logo permission and ownership: docs/DEVELOPMENT.md#license-and-attribution.
"""

import argparse
from pathlib import Path
from shutil import copyfile

from PIL import Image

BRAND = Path(__file__).resolve().parents[1] / "custom_components/healthlog/brand"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Unmodified upstream public/logo-512.png")
    source = parser.parse_args().source.resolve()
    with Image.open(source) as image:
        image.load()
        if image.format != "PNG" or image.size != (512, 512) or image.mode != "RGBA":
            parser.error("Source must be the upstream 512 × 512 RGBA PNG")
        BRAND.mkdir(parents=True, exist_ok=True)
        image.resize((256, 256), Image.Resampling.LANCZOS).save(BRAND / "icon.png", optimize=True)
    # Keep upstream bytes, transparency and composition intact at native size.
    target = BRAND / "icon@2x.png"
    if source != target.resolve():
        copyfile(source, target)


if __name__ == "__main__":
    main()
