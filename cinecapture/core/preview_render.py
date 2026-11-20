"""Builds the on-screen preview image and its measurements (no Qt here)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from . import analysis


@dataclass
class ViewOptions:
    flip_h: bool = False
    flip_v: bool = False
    rotate: int = 0                 # 0, 90, 180, 270 degrees clockwise
    peaking: bool = False
    zebra: bool = True
    zebra_level: int = 250
    histogram: bool = True
    max_width: int = 1600


@dataclass
class PreviewResult:
    image: np.ndarray               # BGR uint8, oriented, with overlays
    source_width: int               # full-resolution size of the camera frame
    source_height: int
    sharpness: float
    clipped: float                  # fraction of overexposed pixels
    histogram: Optional[np.ndarray]


def orient(img: np.ndarray, opts: ViewOptions) -> np.ndarray:
    if opts.flip_h and opts.flip_v:
        img = cv2.flip(img, -1)
    elif opts.flip_h:
        img = cv2.flip(img, 1)
    elif opts.flip_v:
        img = cv2.flip(img, 0)
    rot = opts.rotate % 360
    if rot == 90:
        img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    elif rot == 180:
        img = cv2.rotate(img, cv2.ROTATE_180)
    elif rot == 270:
        img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return img


def render(img: np.ndarray, opts: ViewOptions, source_size=None) -> PreviewResult:
    """``img`` is a (possibly reduced) BGR camera image."""
    if img.dtype != np.uint8:
        img = (img >> 8).astype(np.uint8)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    h, w = img.shape[:2]
    if w > opts.max_width:
        img = cv2.resize(img, (opts.max_width, int(h * opts.max_width / w)),
                         interpolation=cv2.INTER_AREA)
    img = orient(img, opts)
    sharp = analysis.sharpness(img)
    clipped_mask = (img >= opts.zebra_level).any(axis=2)
    clipped = float(clipped_mask.mean())
    hist = analysis.histogram(img) if opts.histogram else None
    out = img
    if opts.peaking or (opts.zebra and clipped > 0):
        out = img.copy()
    if opts.peaking:
        mask = analysis.focus_peaking_mask(img)
        out[mask] = (0, 0, 255)                     # red edges
    if opts.zebra and clipped > 0:
        yy, xx = np.nonzero(clipped_mask)
        stripe = ((xx + yy) // 6) % 2 == 0
        out[yy[stripe], xx[stripe]] = (255, 0, 255)   # magenta stripes on clipped areas
    sw, sh = source_size if source_size else (w, h)
    return PreviewResult(out, sw, sh, sharp, clipped, hist)
