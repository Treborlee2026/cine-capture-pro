"""Image measurements used during capture: sharpness, blank-frame detection, histograms."""

from __future__ import annotations

from typing import Optional, Tuple

import cv2
import numpy as np


def _gray_small(img: np.ndarray, max_width: int = 640) -> np.ndarray:
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if img.dtype != np.uint8:
        img = (img >> 8).astype(np.uint8)
    h, w = img.shape[:2]
    if w > max_width:
        img = cv2.resize(img, (max_width, int(h * max_width / w)), interpolation=cv2.INTER_AREA)
    return img


def centre_crop(img: np.ndarray, fraction: float = 0.5) -> np.ndarray:
    h, w = img.shape[:2]
    ch, cw = int(h * fraction), int(w * fraction)
    y0, x0 = (h - ch) // 2, (w - cw) // 2
    return img[y0:y0 + ch, x0:x0 + cw]


def sharpness(img: np.ndarray, roi: Optional[Tuple[int, int, int, int]] = None) -> float:
    """Focus/blur score: variance of the Laplacian. Higher is sharper.

    ``roi`` is (x, y, w, h) in the image's own pixels; default is the centre half.
    """
    if roi is not None:
        x, y, w, h = roi
        img = img[max(0, y):y + h, max(0, x):x + w]
    else:
        img = centre_crop(img, 0.5)
    g = _gray_small(img)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def is_blank(img: np.ndarray, std_threshold: float = 4.0) -> Tuple[bool, str]:
    """Detect a frame with no picture: clear leader, black leader or no film.

    Returns (blank, kind) where kind is "clear", "dark" or "".
    """
    g = centre_crop(_gray_small(img, 320), 0.6)
    mean, std = float(g.mean()), float(g.std())
    if std < std_threshold:
        if mean > 200:
            return True, "clear"
        if mean < 25:
            return True, "dark"
        return True, "flat"
    return False, ""


def histogram(img: np.ndarray, bins: int = 256) -> np.ndarray:
    """Per-channel histogram, shape (3, bins) for colour or (1, bins) for mono, values 0..1."""
    small = img if img.shape[1] <= 960 else cv2.resize(
        img, (960, int(img.shape[0] * 960 / img.shape[1])), interpolation=cv2.INTER_AREA)
    if small.dtype != np.uint8:
        small = (small >> 8).astype(np.uint8)
    chans = [small] if small.ndim == 2 else cv2.split(small)
    out = np.stack([cv2.calcHist([c], [0], None, [bins], [0, 256]).ravel() for c in chans])
    peak = out.max()
    return out / peak if peak > 0 else out


def clipped_fraction(img: np.ndarray, level: int = 250) -> float:
    """Fraction of pixels at or above ``level`` in any channel (overexposure)."""
    small = _gray_small(img, 640) if img.ndim == 2 else cv2.resize(
        img, (640, int(img.shape[0] * 640 / img.shape[1])), interpolation=cv2.INTER_AREA)
    if small.dtype != np.uint8:
        small = (small >> 8).astype(np.uint8)
    mask = small >= level if small.ndim == 2 else (small >= level).any(axis=2)
    return float(mask.mean())


def focus_peaking_mask(img: np.ndarray, strength: float = 0.5) -> np.ndarray:
    """Boolean mask of in-focus edges for an overlay, same size as ``img``."""
    g = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if g.dtype != np.uint8:
        g = (g >> 8).astype(np.uint8)
    lap = np.abs(cv2.Laplacian(g, cv2.CV_16S, ksize=3))
    thresh = np.percentile(lap, 100 - max(1.0, 10.0 * strength))
    return lap > max(thresh, 20)
