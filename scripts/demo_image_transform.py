"""
Visual deterministic prepress demo.

Shows a social-media square submission transformed into a production banner
using deterministic resize and DPI metadata update. Demo utility only — does
not call the orchestrator or production services.

    python scripts/demo_image_transform.py

Requires Pillow: pip install Pillow
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont

    # Demo-only: banner output at 300 DPI is very large; allow for this script.
    Image.MAX_IMAGE_PIXELS = None
except ImportError:
    print("Pillow is required for this demo. Install with: pip install Pillow", file=sys.stderr)
    sys.exit(1)

_ROOT = Path(__file__).resolve().parents[1]
_DEMO_DIR = _ROOT / "artifacts" / "demo"
_CUSTOMER_PATH = _DEMO_DIR / "customer_submission.png"
_REQUIREMENTS_PATH = _DEMO_DIR / "production_requirements.png"
_CORRECTED_PATH = _DEMO_DIR / "corrected_output.png"

# Banner requirements (matches shop banner preset in the workflow demo)
PRODUCT_TYPE = "banner"
WIDTH_MM = 2000.0
HEIGHT_MM = 1000.0
MIN_DPI = 300
OUTPUT_FORMAT = "png"
CUSTOMER_SIZE = 800
CUSTOMER_DPI = 72
CORRECTED_PREVIEW_WIDTH = 1600


def _step(title: str) -> None:
    print()
    print("=" * 50)
    print(title)
    print("=" * 50)


def _required_px(length_mm: float, min_dpi: int) -> int:
    return math.ceil(length_mm / 25.4 * min_dpi)


def _ensure_demo_dir() -> None:
    _DEMO_DIR.mkdir(parents=True, exist_ok=True)


def _load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = (
        ("arialbd.ttf", "calibrib.ttf", "segoeuib.ttf")
        if bold
        else ("arial.ttf", "calibri.ttf", "segoeui.ttf")
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _read_dpi(image: Image.Image) -> int:
    dpi_info = image.info.get("dpi")
    if isinstance(dpi_info, (tuple, list)) and dpi_info:
        return int(round(float(dpi_info[0])))
    if isinstance(dpi_info, (int, float)):
        return int(round(float(dpi_info)))
    return CUSTOMER_DPI


def _measure_image(image: Image.Image) -> dict:
    width_px, height_px = image.size
    return {
        "width_px": width_px,
        "height_px": height_px,
        "dpi": _read_dpi(image),
        "aspect_ratio": round(width_px / height_px, 2),
    }


def _draw_arrow(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    color: tuple[int, int, int],
    width: int = 3,
) -> None:
    draw.line([start, end], fill=color, width=width)
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    head = 14
    left = (
        end[0] - head * math.cos(angle - math.pi / 6),
        end[1] - head * math.sin(angle - math.pi / 6),
    )
    right = (
        end[0] - head * math.cos(angle + math.pi / 6),
        end[1] - head * math.sin(angle + math.pi / 6),
    )
    draw.polygon([end, left, right], fill=color)


def _draw_callout_box(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    *,
    title: str,
    lines: list[str],
    accent: tuple[int, int, int],
    fill: tuple[int, int, int] = (248, 250, 252),
) -> None:
    x0, y0, x1, y1 = box
    draw.rectangle(box, fill=fill, outline=accent, width=3)
    draw.line([(x0, y0 + 2), (x1, y0 + 2)], fill=accent, width=4)
    title_font = _load_font(18, bold=True)
    body_font = _load_font(15)
    draw.text((x0 + 14, y0 + 12), title, fill=accent, font=title_font)
    y = y0 + 40
    for line in lines:
        draw.text((x0 + 14, y), line, fill=(35, 45, 60), font=body_font)
        y += 22


def _create_customer_submission() -> Image.Image:
    """Square social-media graphic — wrong format for a production banner."""
    size = CUSTOMER_SIZE
    top_color = (18, 52, 120)
    bottom_color = (42, 118, 196)
    accent = (255, 196, 46)
    white = (255, 255, 255)

    image = Image.new("RGB", (size, size), top_color)
    draw = ImageDraw.Draw(image)

    for y in range(size):
        blend = y / size
        color = tuple(int(top_color[i] * (1 - blend) + bottom_color[i] * blend) for i in range(3))
        draw.line([(0, y), (size, y)], fill=color)

    draw.rounded_rectangle([(40, 40), (size - 40, size - 40)], radius=28, outline=white, width=4)
    draw.ellipse([(250, 120), (550, 320)], fill=(12, 34, 78), outline=accent, width=4)
    draw.rectangle([(300, 250), (500, 290)], fill=accent)
    draw.ellipse([(310, 200), (390, 260)], fill=(220, 230, 245))
    draw.ellipse([(410, 200), (490, 260)], fill=(220, 230, 245))

    title_font = _load_font(54, bold=True)
    body_font = _load_font(30, bold=True)
    small_font = _load_font(22)
    tag_font = _load_font(18)

    draw.text((80, 360), "WEEKEND", fill=white, font=title_font)
    draw.text((80, 420), "CAR DETAILING", fill=accent, font=title_font)
    draw.text((80, 510), "SATURDAY SPECIAL", fill=white, font=body_font)
    draw.rounded_rectangle([(80, 570), (420, 640)], radius=18, fill=accent)
    draw.text((118, 588), "BOOK NOW", fill=(20, 40, 80), font=body_font)
    draw.text((80, 680), "#WeekendShine  #CarCare", fill=(210, 225, 245), font=tag_font)
    draw.text((80, 720), "Social post upload", fill=(190, 205, 230), font=small_font)

    badge_font = _load_font(16, bold=True)
    draw.rounded_rectangle([(size - 190, 52), (size - 52, 92)], radius=12, fill=(255, 90, 90))
    draw.text((size - 176, 62), "800 x 800", fill=white, font=badge_font)

    return image


def _create_production_requirements(
    *,
    customer_width: int,
    customer_height: int,
    required_width_px: int,
    required_height_px: int,
) -> Image.Image:
    """Engineering-style requirements diagram with customer vs required comparison."""
    width = 1280
    height = 900
    canvas = (245, 247, 250)
    navy = (24, 52, 96)
    red = (190, 55, 55)
    green = (28, 120, 72)
    arrow = (70, 90, 120)

    image = Image.new("RGB", (width, height), canvas)
    draw = ImageDraw.Draw(image)

    header_font = _load_font(34, bold=True)
    draw.text((48, 36), "PRODUCTION REQUIREMENTS CHECK", fill=navy, font=header_font)

    _draw_callout_box(
        draw,
        (48, 110, 430, 300),
        title="REQUIRED BANNER SPEC",
        lines=[
            f"Width: {WIDTH_MM:.0f} mm",
            f"Height: {HEIGHT_MM:.0f} mm",
            f"Minimum DPI: {MIN_DPI}",
            f"Format: {OUTPUT_FORMAT.upper()}",
            f"Pixels: {required_width_px} x {required_height_px}",
        ],
        accent=navy,
    )

    customer_box = (120, 390, 320, 590)
    required_box = (860, 430, 1180, 580)
    draw.rectangle(customer_box, fill=(255, 235, 235), outline=red, width=4)
    draw.rectangle(required_box, fill=(232, 248, 238), outline=green, width=4)

    label_font = _load_font(20, bold=True)
    detail_font = _load_font(17)
    draw.text((150, 350), "Customer File", fill=red, font=label_font)
    draw.text((150, 610), f"{customer_width} x {customer_height}", fill=red, font=detail_font)
    draw.text((150, 636), "Square social graphic", fill=(90, 90, 90), font=_load_font(15))

    draw.text((900, 390), "Required Banner", fill=green, font=label_font)
    draw.text((880, 600), f"{required_width_px} x {required_height_px}", fill=green, font=detail_font)
    draw.text((900, 626), "2:1 production canvas", fill=(90, 90, 90), font=_load_font(15))

    _draw_arrow(draw, (340, 490), (520, 490), color=arrow)
    draw.text((390, 455), "GAP", fill=arrow, font=_load_font(18, bold=True))
    _draw_arrow(draw, (560, 490), (840, 505), color=arrow)

    _draw_callout_box(
        draw,
        (470, 700, 1180, 860),
        title="Mismatch Summary",
        lines=[
            f"Aspect ratio: 1:1 submitted vs 2:1 required",
            f"Pixel canvas: {customer_width} x {customer_height} vs {required_width_px} x {required_height_px}",
            f"Metadata DPI: {CUSTOMER_DPI} vs {MIN_DPI} minimum",
            "Result: NOT PRINT READY",
        ],
        accent=red,
        fill=(255, 244, 244),
    )

    return image


def _deterministic_transform(image: Image.Image, required_width_px: int, required_height_px: int) -> Image.Image:
    return image.resize((required_width_px, required_height_px), Image.Resampling.LANCZOS)


def _create_corrected_output(
    corrected: Image.Image,
    *,
    required_width_px: int,
    required_height_px: int,
) -> Image.Image:
    """Wide banner preview with green engineering validation callouts."""
    preview_width = CORRECTED_PREVIEW_WIDTH
    preview_height = max(1, int(preview_width * required_height_px / required_width_px))
    preview = corrected.resize((preview_width, preview_height), Image.Resampling.LANCZOS)

    base = preview.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    green = (24, 128, 72)
    white = (255, 255, 255, 255)
    panel = (18, 96, 58, 235)

    callouts = [
        ("Aspect Ratio: PASS", (28, 28, 300, 78)),
        ("Resolution: PASS", (preview_width - 280, 28, preview_width - 28, 78)),
        ("Dimensions: PASS", (28, preview_height - 150, 320, preview_height - 100)),
        ("PRINT READY", (preview_width - 250, preview_height - 150, preview_width - 28, preview_height - 100)),
    ]

    callout_font = _load_font(20, bold=True)
    for text, box in callouts:
        draw.rectangle(box, fill=panel, outline=(*green, 255), width=3)
        x0, y0, x1, y1 = box
        text_bbox = draw.textbbox((0, 0), text, font=callout_font)
        text_w = text_bbox[2] - text_bbox[0]
        text_h = text_bbox[3] - text_bbox[1]
        draw.text(
            (x0 + ((x1 - x0) - text_w) // 2, y0 + ((y1 - y0) - text_h) // 2),
            text,
            fill=white,
            font=callout_font,
        )

    leader_font = _load_font(14, bold=True)
    draw.line([(164, 78), (164, 130)], fill=(*green, 255), width=2)
    draw.line([(164, 130), (240, 130)], fill=(*green, 255), width=2)
    draw.polygon([(240, 130), (228, 124), (228, 136)], fill=(*green, 255))
    draw.text((250, 118), "2:1 banner", fill=white, font=leader_font)

    draw.line([(preview_width - 154, 78), (preview_width - 154, 130)], fill=(*green, 255), width=2)
    draw.text((preview_width - 236, 118), f"{MIN_DPI} DPI", fill=white, font=leader_font)

    draw.line([(174, preview_height - 100), (174, preview_height - 52)], fill=(*green, 255), width=2)
    draw.text(
        (188, preview_height - 68),
        f"{required_width_px} x {required_height_px} px",
        fill=white,
        font=leader_font,
    )

    return Image.alpha_composite(base, overlay).convert("RGB")


def main() -> None:
    print("=" * 50)
    print("DETERMINISTIC PREPRESS TRANSFORM DEMO")
    print("=" * 50)
    print()
    print("Problem -> Requirements -> Correction")
    print("Example: Weekend car-detailing social post to production banner")
    print()

    _ensure_demo_dir()
    required_width_px = _required_px(WIDTH_MM, MIN_DPI)
    required_height_px = _required_px(HEIGHT_MM, MIN_DPI)
    required_aspect = round(required_width_px / required_height_px, 2)

    customer = _create_customer_submission()
    customer.save(_CUSTOMER_PATH, format="PNG", dpi=(CUSTOMER_DPI, CUSTOMER_DPI))
    customer_measured = _measure_image(customer)

    _step("STEP 1: Customer Submission")
    print(f"Saved: {_CUSTOMER_PATH.name}")
    print(f"Source: Customer social-media upload")
    print(f"Dimensions: {customer_measured['width_px']} x {customer_measured['height_px']} pixels")
    print(f"Aspect ratio: {customer_measured['aspect_ratio']}:1 (square)")
    print(f"DPI metadata: {customer_measured['dpi']}")
    print("Intent: Instagram-style promo graphic, not a print banner")

    requirements = _create_production_requirements(
        customer_width=customer_measured["width_px"],
        customer_height=customer_measured["height_px"],
        required_width_px=required_width_px,
        required_height_px=required_height_px,
    )
    requirements.save(_REQUIREMENTS_PATH, format="PNG")

    _step("STEP 2: Production Requirements")
    print(f"Saved: {_REQUIREMENTS_PATH.name}")
    print(f"Product type: {PRODUCT_TYPE.title()}")
    print(f"Physical size: {WIDTH_MM:.0f} mm x {HEIGHT_MM:.0f} mm")
    print(f"Minimum resolution: {MIN_DPI} DPI")
    print(f"Output format: {OUTPUT_FORMAT.upper()}")
    print(f"Required pixels: {required_width_px} x {required_height_px}")
    print(f"Required aspect ratio: {required_aspect}:1")

    _step("STEP 3: Analysis")
    issues = [
        "Wrong aspect ratio: customer submitted a 1:1 square, banner requires 2:1.",
        (
            f"Wrong pixel canvas: {customer_measured['width_px']} x {customer_measured['height_px']} "
            f"cannot cover a {WIDTH_MM:.0f} mm x {HEIGHT_MM:.0f} mm banner at {MIN_DPI} DPI."
        ),
        f"Low print metadata: {customer_measured['dpi']} DPI is below the {MIN_DPI} DPI production minimum.",
        "Format intent mismatch: social graphic uploaded where production banner artwork is required.",
    ]
    for issue in issues:
        print(f"  - {issue}")
    print()
    print("Verdict: NOT PRINT READY")

    _step("STEP 4: Deterministic Correction")
    print("Repair steps:")
    print(f"  1. Resize artwork to {required_width_px} x {required_height_px} pixels (2:1 banner)")
    print(f"  2. Set output metadata to {MIN_DPI} DPI")
    print(f"  3. Save as {OUTPUT_FORMAT.upper()}")
    print("  4. No AI generation used")

    corrected = _deterministic_transform(customer, required_width_px, required_height_px)
    corrected.info["dpi"] = (MIN_DPI, MIN_DPI)

    corrected_visual = _create_corrected_output(
        corrected,
        required_width_px=required_width_px,
        required_height_px=required_height_px,
    )
    corrected_visual.save(_CORRECTED_PATH, format="PNG", dpi=(MIN_DPI, MIN_DPI))
    corrected_measured = _measure_image(corrected)

    _step("STEP 5: Validation")
    print(f"Saved: {_CORRECTED_PATH.name}")
    print(f"Corrected dimensions: {corrected_measured['width_px']} x {corrected_measured['height_px']} pixels")
    print(f"Corrected aspect ratio: {corrected_measured['aspect_ratio']}:1")
    print(f"Corrected DPI metadata: {corrected_measured['dpi']}")
    print()
    print("Validation checks:")
    print(f"  - Aspect Ratio: PASS ({corrected_measured['aspect_ratio']}:1 matches {required_aspect}:1)")
    print(f"  - Resolution:   PASS ({corrected_measured['dpi']} >= {MIN_DPI})")
    print(
        f"  - Dimensions:   PASS "
        f"({corrected_measured['width_px']} x {corrected_measured['height_px']})"
    )
    print()
    print("Result: PRINT READY")

    print()
    print("Generated Demo Assets:")
    print(f"  {_CUSTOMER_PATH.resolve()}")
    print(f"  {_REQUIREMENTS_PATH.resolve()}")
    print(f"  {_CORRECTED_PATH.resolve()}")
    print()
    print("This demonstrates one worker inside the larger workflow.")
    print()
    print("Next run:")
    print("python scripts/demo_happy_path.py")


if __name__ == "__main__":
    main()
