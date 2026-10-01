"""Image preprocessing for receipts scanned on a flatbed or photographed."""

from typing import List, Optional, Tuple

import numpy as np
from PIL import Image, ImageFilter

# A receipt narrower than this share of the image is cropped (A4 scan of a receipt).
CROP_MAX_WIDTH_RATIO = 0.7
_DARK_LEVEL = 0.6
_CROP_MARGIN_RATIO = 0.03
# Scanner edges leave long black lines and shadows along the page border.
_EDGE_BAND_RATIO = 0.015
_EDGE_LINE_RATIO = 0.5
_BACKGROUND_BLUR_RATIO = 0.04


def flatten_transparency(image: Image.Image) -> Image.Image:
    """Composite an image with transparency on a white background."""
    if image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    ):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        background.alpha_composite(rgba)
        return background.convert("RGB")
    return image


def normalize_background(gray: Image.Image) -> Image.Image:
    """Divide by the blurred image to flatten uneven or tinted paper."""
    radius = max(5, round(gray.width * _BACKGROUND_BLUR_RATIO))
    pixels = np.asarray(gray, dtype=np.float32)
    background = np.asarray(gray.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32)
    flat = np.clip(pixels / np.maximum(background, 1.0) * 255.0, 0, 255)
    return Image.fromarray(flat.astype(np.uint8))


def _runs(mask: np.ndarray, max_gap: int) -> List[Tuple[int, int]]:
    """Return the ``(start, end)`` runs of True values, merging gaps up to ``max_gap``."""
    indexes = np.flatnonzero(mask)
    if indexes.size == 0:
        return []
    runs = []
    start = previous = int(indexes[0])
    for index in indexes[1:]:
        index = int(index)
        if index - previous > max_gap:
            runs.append((start, previous))
            start = index
        previous = index
    runs.append((start, previous))
    return runs


def _ignore_scanner_edges(dark: np.ndarray) -> None:
    """Clear, in place, the page-long dark lines and the outer band of the page."""
    height, width = dark.shape
    dark[dark.sum(axis=1) > width * _EDGE_LINE_RATIO, :] = False
    dark[:, dark.sum(axis=0) > height * _EDGE_LINE_RATIO] = False
    band_x, band_y = round(width * _EDGE_BAND_RATIO), round(height * _EDGE_BAND_RATIO)
    dark[:band_y, :] = False
    dark[height - band_y:, :] = False
    dark[:, :band_x] = False
    dark[:, width - band_x:] = False


def crop_to_receipt(gray: Image.Image) -> Optional[Image.Image]:
    """Crop a flattened grayscale image around the receipt text.

    Returns None when the text spans most of the width (nothing to crop).
    """
    pixels = np.asarray(gray)
    height, width = pixels.shape
    dark = pixels < int(255 * _DARK_LEVEL)
    _ignore_scanner_edges(dark)

    column_counts = dark.sum(axis=0)
    column_runs = _runs(column_counts >= max(3, height // 1000), max_gap=width // 20)
    if not column_runs:
        return None
    left, right = max(column_runs, key=lambda run: column_counts[run[0]:run[1] + 1].sum())
    if (right - left + 1) > width * CROP_MAX_WIDTH_RATIO:
        return None

    row_counts = dark[:, left:right + 1].sum(axis=1)
    row_runs = _runs(row_counts >= 2, max_gap=height // 40)
    top, bottom = (row_runs[0][0], row_runs[-1][1]) if row_runs else (0, height - 1)

    margin = round(width * _CROP_MARGIN_RATIO)
    return gray.crop(
        (
            max(0, left - margin),
            max(0, top - margin),
            min(width, right + margin),
            min(height, bottom + margin),
        )
    )
