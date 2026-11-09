"""Render the original integration notebook/pulse mark (MIT, no borrowed artwork).

Run with: uv run --with pillow python tools/create_brand.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

BRAND = Path(__file__).resolve().parents[1] / "custom_components/healthlog/brand"
BRAND.mkdir(parents=True, exist_ok=True)
image = Image.new("RGBA", (1024, 1024))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((0, 0, 1023, 1023), radius=176, fill="#087F8C")
draw.rounded_rectangle((216, 112, 858, 912), radius=54, fill="#FFF9ED")
draw.rectangle((216, 166, 272, 858), fill="#BCD9D5")
for y in (252, 772):
    draw.line([(350, y), (738, y)], fill="#BCD9D5", width=24)
draw.line(
    [(330, 530), (410, 530), (466, 422), (536, 636), (604, 486), (650, 530), (750, 530)],
    fill="#087F8C",
    width=30,
    joint="curve",
)
for size, filename in ((256, "icon.png"), (512, "icon@2x.png")):
    image.resize((size, size), Image.Resampling.LANCZOS).save(BRAND / filename, optimize=True)
