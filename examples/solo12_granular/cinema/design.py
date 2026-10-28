"""Portable 1080p title assets used by the published edit.

System fonts are read, never copied or redistributed. Pass explicit font paths
when the installed fonts differ. Typography may differ across operating systems.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = (1920, 1080)
WHITE = (239, 244, 249, 255)
MUTED = (145, 163, 181, 255)
ACCENT = (85, 226, 218, 255)


def find_font(explicit=None, bold=False):
    candidates = [explicit] if explicit else []
    candidates += (
        [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "DejaVuSans-Bold.ttf",
        ]
        if bold
        else [
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "DejaVuSans.ttf",
        ]
    )
    if explicit and not Path(explicit).is_file():
        raise FileNotFoundError("The supplied font file does not exist")
    for candidate in candidates:
        try:
            ImageFont.truetype(str(candidate), 24)
            return str(candidate)
        except OSError:
            continue
    raise RuntimeError("No compatible TrueType font found; supply --font and --bold-font")


def build_assets(font=None, bold_font=None, platform_label="Local compute."):
    regular = find_font(font)
    bold = find_font(bold_font, bold=True)

    def face(size, heavy=False):
        return ImageFont.truetype(bold if heavy else regular, size)

    def tracking(draw, text, xy, size, color, spacing=3):
        x, y = xy
        f = face(size, True)
        for char in text:
            draw.text((x, y), char, font=f, fill=color)
            x += draw.textlength(char, font=f) + spacing

    opening = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    d = ImageDraw.Draw(opening)
    for y in range(300):
        d.line((0, y, SIZE[0], y), fill=(4, 8, 13, round(145 * max(0, 1 - y / 300) ** 1.5)))
    d.rounded_rectangle((96, 76, 130, 80), radius=2, fill=ACCENT)
    tracking(d, "SCENWARD", (151, 57), 39, WHITE, 5)
    d.text((151, 118), "Simulation, within reach.", font=face(24), fill=WHITE)

    contact = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    d = ImageDraw.Draw(contact)
    for y in range(260):
        d.line((0, y, SIZE[0], y), fill=(4, 8, 13, round(140 * max(0, 1 - y / 260) ** 1.5)))
    d.rectangle((96, 91, 130, 95), fill=ACCENT)
    tracking(d, "CONTACT, IN DETAIL.", (150, 76), 28, WHITE, 2)

    closing = Image.new("RGB", SIZE, (8, 13, 20))
    d = ImageDraw.Draw(closing)
    tracking(d, "SCENWARD", (140, 180), 32, WHITE, 5)
    d.rectangle((140, 281, 214, 285), fill=ACCENT)
    d.text((136, 340), "Local robot simulation.", font=face(74, True), fill=WHITE)
    d.text((136, 438), platform_label, font=face(74, True), fill=WHITE)
    d.text((140, 620), "Open robot. Local compute. Visible physics.", font=face(29), fill=MUTED)
    d.text(
        (140, 910),
        "Solo12 model: Open Dynamic Robot Initiative · BSD-3-Clause",
        font=face(21),
        fill=MUTED,
    )
    return opening, contact, closing, regular
