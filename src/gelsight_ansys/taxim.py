"""Example-based RGB response from surface normals, in a fixed camera frame."""

from pathlib import Path

import numpy as np
from PIL import Image

ASSETS = Path(__file__).parent / "data" / "mini"
ANGULAR_BINS = 120


def calibration_features(camera):
    """Landscape pixels mapped to the clockwise-rotated portrait calibration."""
    row, col = np.indices((camera.height_px, camera.width_px), dtype=float)
    x = row * 240 / camera.height_px
    y = (camera.width_px - 1 - col) * 320 / camera.width_px
    return np.stack((x * x, y * y, x * y, x, y, np.ones_like(x)), axis=-1)


def angular_coordinates(normals):
    nx, ny, nz = np.moveaxis(normals, -1, 0)
    slope = np.hypot(nx, ny)
    mag = np.arctan2(slope, np.maximum(nz, 1e-12))
    # Original calibration: row derivative points against landscape x, column
    # derivative against gel y. Use indentation, not elevation, derivatives.
    direction = np.where(slope > 1e-7, np.arctan2(-nx, -ny), 0.0)
    return mag * (ANGULAR_BINS - 1) / (np.pi / 2), (direction + np.pi) * (
        ANGULAR_BINS - 1
    ) / (2 * np.pi)


def cubic_weights(t):
    return (
        -0.5 * t + t * t - 0.5 * t * t * t,
        1 - 2.5 * t * t + 1.5 * t * t * t,
        0.5 * t + 2 * t * t - 1.5 * t * t * t,
        -0.5 * t * t + 0.5 * t * t * t,
    )


def evaluate(table, features, normals):
    u, v = angular_coordinates(normals)
    u = np.clip(u, 0, table.shape[0] - 1)
    i, j = np.floor(u).astype(int), np.floor(v).astype(int)
    a, b = cubic_weights(u - i), cubic_weights(v - j)
    coeff = 0.0
    for x in range(4):
        ix = np.clip(i + x - 1, 0, table.shape[0] - 1)
        for y in range(4):
            iy = (j + y - 1) % (ANGULAR_BINS - 1)
            coeff = coeff + (a[x] * b[y])[..., None, None] * table[ix, iy]
    return np.sum(coeff * features[..., None, :], axis=-1)


def smooth_response(table, sigma):
    if sigma == 0:
        return table
    radius = int(np.ceil(3 * sigma))
    offsets = np.arange(-radius, radius + 1)
    weights = np.exp(-0.5 * (offsets / sigma) ** 2)
    weights /= weights.sum()
    # Azimuth is periodic; magnitude extends with its endpoint value.
    unique = table[:, : ANGULAR_BINS - 1]
    angular = sum(w * np.roll(unique, int(k), axis=1) for k, w in zip(offsets, weights))
    padded = np.pad(angular, ((radius, radius), (0, 0), (0, 0), (0, 0)), mode="edge")
    result = sum(w * padded[i : i + len(table)] for i, w in enumerate(weights))
    return result[:, np.arange(table.shape[1]) % (ANGULAR_BINS - 1)]


class TaximResponse:
    def __init__(self, camera, gain, smoothing_bins=2.0):
        self.camera, self.gain = camera, gain
        with np.load(ASSETS / "polycalib.npz", allow_pickle=False) as data:
            self.table = np.stack(
                [data[k] for k in ("grad_r", "grad_g", "grad_b")], axis=2
            )
        if self.table.shape != (125, 125, 3, 6) or not np.isfinite(self.table).all():
            raise ValueError("Unexpected optical calibration table")
        # The angular endpoints represent the same direction; measured fits differ.
        seam = (self.table[:, 0] + self.table[:, ANGULAR_BINS - 1]) / 2
        self.table[:, 0] = self.table[:, ANGULAR_BINS - 1] = seam
        # At zero tilt the direction is undefined. Remove each direction's own
        # fitted flat offset so arbitrarily small slopes cannot change the color.
        self.table = self.table - self.table[0:1].copy()
        self.table = smooth_response(self.table, smoothing_bins)
        self.table -= self.table[0:1].copy()
        self.features = calibration_features(camera)
        with Image.open(ASSETS / "background.png") as image:
            image = image.transpose(Image.Transpose.ROTATE_270)
            image = image.resize(
                (camera.width_px, camera.height_px), Image.Resampling.BILINEAR
            )
            self.background = np.asarray(image, dtype=float) / 255
        self.flat = evaluate(self.table, self.features, np.array([0.0, 0.0, 1.0]))

    def shade(self, normals, valid, background):
        delta = (
            (evaluate(self.table, self.features, normals) - self.flat) * self.gain / 255
        )
        return np.clip(background + np.where(valid[..., None], delta, 0), 0, 1)
