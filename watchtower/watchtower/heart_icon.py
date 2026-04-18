"""Heart icon — system health status indicator for Watchtower player page.

Pre-computes all icon frames at startup as PhotoImage objects:
- Green (nominal): single static frame
- Amber (degraded): single static frame
- Red pulse (critical): ~30 frames with sharp attack / exponential decay

The heart shape is defined as a binary mask. Color variants are generated
by applying colors to the mask. The pulse sequence uses a brightness curve
that emulates a natural heartbeat rhythm.

Copyright (c) 2026 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import math
import numpy as np
import PIL.Image
import PIL.ImageTk


# Icon dimensions — matches LIST button proportions (60×60)
ICON_SIZE = 50

# Colors (BGR ordering not needed — we work in RGB for PIL)
COLOR_GREEN = (46, 204, 113)     # #2ECC71
COLOR_AMBER = (243, 156, 18)     # #F39C12
COLOR_RED = (231, 76, 60)        # #E74C3C
COLOR_RED_DIM = (40, 10, 8)      # near-black red for pulse trough

# Pulse sequence length (~1 second at 30 FPS update rate)
PULSE_FRAMES = 30


def _build_heart_mask(size):
    """Generate a binary heart shape mask.

    Uses the parametric heart curve scaled to fit within size×size.
    Returns a float32 array (0.0 or 1.0) of shape (size, size).
    """
    mask = np.zeros((size, size), dtype=np.float32)
    cx, cy = size / 2, size * 0.54  # offset down — top lobes extend further than bottom tip
    scale = size / 2.35

    for y in range(size):
        for x in range(size):
            # Normalize coordinates to [-1, 1] range centered on (cx, cy)
            nx = (x - cx) / scale
            ny = (cy - y) / scale  # flip Y for math coordinates
            # Heart implicit equation: (x^2 + y^2 - 1)^3 - x^2 * y^3 <= 0
            val = (nx * nx + ny * ny - 1) ** 3 - nx * nx * ny * ny * ny
            if val <= 0:
                mask[y, x] = 1.0

    return mask


def _make_frame(mask, color, alpha_bg=0):
    """Create an RGBA PIL Image from a mask and RGB color tuple."""
    size = mask.shape[0]
    rgba = np.zeros((size, size, 4), dtype=np.uint8)
    for c in range(3):
        rgba[:, :, c] = (mask * color[c]).astype(np.uint8)
    # Alpha channel: full opacity where mask is set, transparent elsewhere
    rgba[:, :, 3] = (mask * 255).astype(np.uint8)
    return PIL.Image.fromarray(rgba, 'RGBA')


def _pulse_brightness(frame_idx, total_frames):
    """Compute brightness factor (0.0–1.0) for a heartbeat pulse.

    Sharp attack at frame 0, exponential decay through the rest of the cycle.
    """
    t = frame_idx / total_frames
    # Sharp attack: peak at t=0, fast decay
    # Using shifted exponential: e^(-6t) gives fast initial decay
    brightness = math.exp(-6.0 * t)
    # Clamp minimum so the heart doesn't fully disappear
    return max(brightness, 0.15)


class HeartIcon:
    """Pre-computed heart icon frames for three health states.

    Usage:
        heart = HeartIcon()
        photo = heart.green          # PhotoImage for green state
        photo = heart.amber          # PhotoImage for amber state
        photo = heart.pulse(idx)     # PhotoImage for red pulse frame
        idx = heart.next_pulse(idx)  # Advance pulse index with wrap
    """

    def __init__(self):
        self._mask = _build_heart_mask(ICON_SIZE)

        # Static frames
        green_img = _make_frame(self._mask, COLOR_GREEN)
        amber_img = _make_frame(self._mask, COLOR_AMBER)
        self.green = PIL.ImageTk.PhotoImage(green_img)
        self.amber = PIL.ImageTk.PhotoImage(amber_img)

        # Red pulse sequence
        self._pulse_frames = []
        for i in range(PULSE_FRAMES):
            b = _pulse_brightness(i, PULSE_FRAMES)
            # Interpolate between dim and full red
            color = tuple(
                int(COLOR_RED_DIM[c] + b * (COLOR_RED[c] - COLOR_RED_DIM[c]))
                for c in range(3)
            )
            frame_img = _make_frame(self._mask, color)
            self._pulse_frames.append(PIL.ImageTk.PhotoImage(frame_img))

        self.pulse_length = PULSE_FRAMES

    def pulse(self, idx):
        """Get the PhotoImage for a given pulse frame index."""
        return self._pulse_frames[idx % self.pulse_length]

    def next_pulse(self, idx):
        """Advance pulse index with wrap-around."""
        return (idx + 1) % self.pulse_length
