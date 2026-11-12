"""Deterministic, candidate-controlled synthetic CAPTCHA generators.

Families in this module:
- text: five-character visual transcription.
- arithmetic: read a three-glyph arithmetic expression and calculate its result.

These are owned synthetic assessment fixtures, not provider CAPTCHA bypasses.
"""
from __future__ import annotations

import io
import os
import random
import uuid
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ALPHABET = "ACDEFHJKMNPQRTUVWXY"
ARITHMETIC_CHARSET = "123456789+x"
IMAGE_WIDTH = 420
IMAGE_HEIGHT = 110
SLOT_COUNT = 5
FONT_SIZES = (42, 46, 50, 54)
ROTATIONS = (-12, -9, -6, -3, 0, 3, 6, 9, 12)


def _font_candidates() -> list[str | None]:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    candidates: list[Path] = [
        windir / "Fonts" / "arialbd.ttf",
        windir / "Fonts" / "tahomabd.ttf",
        windir / "Fonts" / "verdanab.ttf",
        windir / "Fonts" / "calibrib.ttf",
        windir / "Fonts" / "consolab.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"),
        Path("/usr/share/fonts/truetype/lato/Lato-Bold.ttf"),
        Path("/Library/Fonts/Arial Bold.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ]
    found = [str(path) for path in candidates if path.is_file()]
    return found[:4] or [None]


@lru_cache(maxsize=1)
def available_fonts() -> tuple[str | None, ...]:
    return tuple(_font_candidates())


def make_glyph_mask(char: str, font_path: str | None, size: int, angle: int) -> Image.Image:
    font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
    bbox = font.getbbox(char)
    width = max(8, bbox[2] - bbox[0])
    height = max(8, bbox[3] - bbox[1])
    base = Image.new("L", (width + 20, height + 20), 0)
    draw = ImageDraw.Draw(base)
    draw.text((10 - bbox[0], 10 - bbox[1]), char, font=font, fill=255)
    rotated = base.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
    return rotated.crop(rotated.getbbox()) if rotated.getbbox() else rotated


def _background(rng: random.Random) -> Image.Image:
    """Create a colourful textured background with low-contrast interference."""
    yy, xx = np.mgrid[0:IMAGE_HEIGHT, 0:IMAGE_WIDTH]
    base = np.zeros((IMAGE_HEIGHT, IMAGE_WIDTH, 3), dtype=np.uint8)
    base[:, :, 0] = np.clip(210 + 24 * np.sin(xx / 52) + 8 * np.cos(yy / 15), 170, 250)
    base[:, :, 1] = np.clip(218 + 18 * np.cos(xx / 61) + 10 * np.sin(yy / 17), 170, 250)
    base[:, :, 2] = np.clip(224 + 22 * np.sin((xx + yy) / 47), 175, 252)
    noise = np.random.default_rng(rng.randrange(2**32)).normal(0, 2.2, base.shape)
    base = np.clip(base.astype(np.float32) + noise, 160, 255).astype(np.uint8)
    image = Image.fromarray(base, "RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    palette = [
        (255, 90, 120, 75), (40, 130, 190, 70), (70, 170, 130, 70),
        (230, 150, 40, 75), (130, 90, 210, 70), (25, 160, 175, 70),
        (245, 210, 80, 85),
    ]
    for _ in range(14):
        x, y = rng.randrange(IMAGE_WIDTH), rng.randrange(IMAGE_HEIGHT)
        radius = rng.randrange(3, 15)
        color = rng.choice(palette)
        if rng.random() < 0.5:
            draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=color)
        else:
            draw.polygon([(x, y-radius), (x+radius, y+radius), (x-radius, y+radius)], fill=color)
    for _ in range(10):
        x1, y1 = rng.randrange(IMAGE_WIDTH), rng.randrange(IMAGE_HEIGHT)
        x2 = min(IMAGE_WIDTH, x1 + rng.randrange(25, 120))
        y2 = max(0, min(IMAGE_HEIGHT, y1 + rng.randrange(-35, 36)))
        draw.line((x1, y1, x2, y2), fill=rng.choice(palette), width=rng.choice((1, 2, 3)))
    for x in range(8, IMAGE_WIDTH, 22):
        if rng.random() < 0.55:
            y = rng.randrange(4, IMAGE_HEIGHT - 4)
            draw.ellipse((x-1, y-1, x+1, y+1), fill=(255, 255, 255, 150))
    return image


def _place_glyph(
    canvas: Image.Image,
    char: str,
    font_path: str | None,
    size: int,
    angle: int,
    rng: random.Random,
    slot: int,
    slot_count: int = SLOT_COUNT,
) -> None:
    slot_width = IMAGE_WIDTH // slot_count
    mask = make_glyph_mask(char, font_path, size, angle)
    max_w, max_h = slot_width - 14, IMAGE_HEIGHT - 20
    scale = min(1.0, max_w / mask.width, max_h / mask.height)
    if scale < 1:
        mask = mask.resize(
            (max(1, int(mask.width * scale)), max(1, int(mask.height * scale))),
            Image.Resampling.LANCZOS,
        )
    x = slot * slot_width + (slot_width - mask.width) // 2 + rng.randint(-3, 3)
    y = (IMAGE_HEIGHT - mask.height) // 2 + rng.randint(-3, 3)
    color = rng.choice(((25, 55, 100), (65, 35, 90), (25, 85, 65), (85, 45, 35), (35, 65, 75)))
    canvas.paste(color, (x, y), mask)


def _generate_slot_image(text: str, seed: int | None, slot_count: int) -> bytes:
    rng = random.Random(seed) if seed is not None else random.SystemRandom()
    image = _background(rng)
    fonts = available_fonts()
    for index, char in enumerate(text):
        _place_glyph(
            image, char, rng.choice(fonts), rng.choice(FONT_SIZES),
            rng.choice(ROTATIONS), rng, index, slot_count=slot_count,
        )
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def generate_text_challenge(seed: int | None = None) -> tuple[bytes, str, str]:
    """Generate a five-character coloured synthetic text CAPTCHA."""
    rng = random.Random(seed) if seed is not None else random.SystemRandom()
    answer = "".join(rng.choice(ALPHABET) for _ in range(SLOT_COUNT))
    return _generate_slot_image(answer, seed, SLOT_COUNT), answer, str(uuid.uuid4())


def generate_arithmetic_challenge(seed: int | None = None) -> tuple[bytes, str, str, str]:
    """Generate a visual arithmetic CAPTCHA with a separate calculation step."""
    rng = random.Random(seed) if seed is not None else random.SystemRandom()
    left = rng.randint(2, 9)
    right = rng.randint(2, 9)
    operator = rng.choice(("+", "x"))
    answer = str(left + right if operator == "+" else left * right)
    expression = f"{left}{operator}{right}"
    image = _generate_slot_image(expression, seed, slot_count=3)
    return image, answer, str(uuid.uuid4()), "Calculate the expression shown in the image."
