"""Render the GitHub social card from the bundled PXRD figure and reviewed CSV.

Requires Pillow. Inputs are read without modification; no extraction is rerun.
"""

import argparse
import csv
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parents[1]
SCALE = 2
INK = "#0b2132"
MUTED = "#567080"
TEAL = "#5ce0c2"


def render(output, font_path=None):
    metadata = json.loads((ROOT / "examples/pxrd-extraction.json").read_text(encoding="utf8"))
    source = ROOT / metadata["source"]
    if hashlib.sha256(source.read_bytes()).hexdigest() != metadata["source_sha256"]:
        raise ValueError("The bundled source figure differs from the reviewed example.")
    with (ROOT / "examples/pxrd-extracted.csv").open(newline="", encoding="utf8") as stream:
        points = [(float(row["two_theta_deg"]), float(row["intensity_counts"]))
                  for row in csv.DictReader(stream)]
    if len(points) != metadata["observed_points"]:
        raise ValueError("The CSV point count differs from the reviewed example.")

    regular = [str(font_path)] if font_path else [
        "C:/Windows/Fonts/segoeui.ttf", "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    bold = [str(font_path)] if font_path else [
        "C:/Windows/Fonts/seguisb.ttf", "DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]

    def font(size, heavy=False):
        for candidate in bold if heavy else regular:
            try:
                return ImageFont.truetype(candidate, size * SCALE)
            except OSError:
                continue
        raise RuntimeError("No suitable font found; supply --font /path/to/font.ttf.")

    image = Image.new("RGB", (1280 * SCALE, 640 * SCALE), "#081c2b")
    draw = ImageDraw.Draw(image)

    def box(bounds, fill, radius=0, outline=None):
        draw.rounded_rectangle(tuple(round(v * SCALE) for v in bounds),
                               radius=radius * SCALE, fill=fill, outline=outline,
                               width=SCALE)

    def text(x, y, value, size, fill, heavy=False, anchor=None):
        draw.text((x * SCALE, y * SCALE), value, font=font(size, heavy),
                  fill=fill, anchor=anchor)

    def line(coords, fill, width=1):
        draw.line([(round(x * SCALE), round(y * SCALE)) for x, y in coords],
                  fill=fill, width=width * SCALE)

    box((64, 37, 70, 98), TEAL, radius=3)
    text(88, 27, "PlotDigitizer", 56, "#f5fbff", heavy=True)
    box((1092, 51, 1216, 91), "#103f43", radius=20)
    text(1154, 59, "OFFLINE", 18, TEAL, heavy=True, anchor="mt")
    text(64, 112, "Scientific figures → reviewed curve data", 32, "#e2edf5")
    text(64, 165, "PXRD  ·  Raman  ·  Infrared spectroscopy", 21, "#a6bdcc")

    box((64, 219, 596, 512), "#ffffff", radius=16)
    box((684, 219, 1216, 512), "#ffffff", radius=16)
    text(86, 235, "01 / ORIGINAL FIGURE", 16, MUTED, heavy=True)
    text(706, 235, "02 / EXTRACTED CURVE", 16, MUTED, heavy=True)
    with Image.open(source) as original:
        fitted = ImageOps.contain(original.convert("RGB"),
                                  (470 * SCALE, 205 * SCALE),
                                  Image.Resampling.LANCZOS)
    image.paste(fitted, (round(330 * SCALE - fitted.width / 2), 272 * SCALE))
    text(330, 485, "US8198452B2 · Fig. 1", 13, MUTED, anchor="mt")

    # Render the recorded numerical samples without smoothing or bridging gaps.
    left, top, right, bottom = 756, 288, 1188, 449
    x_min, x_max, y_max = 3, 65, 20000

    def coordinate(point):
        x, y = point
        return (left + (x - x_min) / (x_max - x_min) * (right - left),
                bottom - y / y_max * (bottom - top))

    text(left, 266, "Intensity (counts)", 12, MUTED)
    for value in (0, 5000, 10000, 15000, 20000):
        y = bottom - value / y_max * (bottom - top)
        line([(left, y), (right, y)], "#e8eef2")
        text(left - 10, y, f"{value:,}", 11, MUTED, anchor="rm")
    for value in (3, 20, 40, 60):
        x = coordinate((value, 0))[0]
        line([(x, bottom), (x, bottom + 4)], MUTED)
        text(x, bottom + 7, str(value), 11, MUTED, anchor="mt")
    line([(left, top), (left, bottom), (right, bottom)], MUTED)
    ticks = metadata["calibration"]["x"]
    step = abs((ticks[1]["value"] - ticks[0]["value"]) /
               (ticks[1]["pixel"][0] - ticks[0]["pixel"][0]))
    for previous, current in zip(points, points[1:]):
        if 0 < current[0] - previous[0] <= 1.6 * step:
            line([coordinate(previous), coordinate(current)], "#117e74", width=1)
    text((left + right) / 2, 470, "2θ (degrees)", 12, MUTED, anchor="mt")
    text(950, 490, f"{len(points)} observed points · reviewed calibration", 12,
         MUTED, anchor="mt")

    line([(616, 366), (661, 366)], TEAL, width=3)
    draw.polygon([(x * SCALE, y * SCALE) for x, y in
                  ((653, 358), (665, 366), (653, 374))], fill=TEAL)
    text(64, 538, "Open → Calibrate → Extract → Review → Export", 25,
         "#f5fbff", heavy=True)
    text(64, 584, "CSV · JSON · XRDML   /   Desktop + local REST API", 19,
         "#a6bdcc")
    text(1216, 587, "github.com/unwatcher/Plot_Digitizer", 15, "#a6bdcc",
         anchor="rt")

    image = image.resize((1280, 640), Image.Resampling.LANCZOS)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, optimize=True)
    if output.stat().st_size >= 1_000_000:
        raise ValueError("Social preview exceeds GitHub's 1 MB upload limit.")
    print(json.dumps({"output": str(output), "size": list(image.size),
                      "bytes": output.stat().st_size, "observed_points": len(points)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/images/social-preview.png")
    parser.add_argument("--font", type=Path)
    arguments = parser.parse_args()
    render(arguments.output, arguments.font)
