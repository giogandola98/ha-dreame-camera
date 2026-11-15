"""Robust 'did the scene change?' score between two robot photos (same point/heading).
Tolerant to small robot shifts (alignment search) and global brightness changes (normalisation)."""
import io
import numpy as np
from PIL import Image, ImageFilter

W, H = 96, 54
SHIFT = 5


def signature(jpeg: bytes) -> np.ndarray:
    img = Image.open(io.BytesIO(jpeg)).convert("L").resize((W, H), Image.BILINEAR)
    img = img.filter(ImageFilter.GaussianBlur(1.2))
    a = np.asarray(img, dtype=np.float32)
    return (a - a.mean()) / (a.std() + 1e-6)


def difference(a: np.ndarray, b: np.ndarray) -> float:
    best = 1e9
    for dy in range(-SHIFT // 2, SHIFT // 2 + 1):
        for dx in range(-SHIFT, SHIFT + 1):
            aa = a[max(0, dy):H + min(0, dy), max(0, dx):W + min(0, dx)]
            bb = b[max(0, -dy):H + min(0, -dy), max(0, -dx):W + min(0, -dx)]
            best = min(best, float(np.mean(np.abs(aa - bb))))
    return best
