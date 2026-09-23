"""Press measurement on a synthetic recording, and depth matching against a run."""

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    import cv2
except ImportError:  # pragma: no cover - opencv is the optional [sensor] extra
    cv2 = None

from gelsight_ansys.sensor import mini, press


def unloaded_frame():
    """320 x 240 BGR gel with an 11 x 17 dot grid at a 14.5 px pitch."""
    image = np.zeros((240, 320, 3), np.uint8)
    image[:] = (120, 150, 110)
    for y in 46.0 + 14.6 * np.arange(11):
        for x in 43.0 + 14.5 * np.arange(17):
            cv2.circle(
                image,
                (int(round(x * 16)), int(round(y * 16))),
                4 * 16,
                (40, 45, 40),
                -1,
                cv2.LINE_AA,
                4,
            )
    return image


def pressed(frame, center, radius):
    """A colour shift over a disk, as a press shades the gel."""
    out = frame.astype(np.float32)
    yy, xx = np.mgrid[0:240, 0:320]
    disk = ((xx - center[0]) ** 2 + (yy - center[1]) ** 2 <= radius**2)[..., None]
    return np.clip(out + disk * np.array([60.0, -30.0, 40.0]), 0, 255).astype(np.uint8)


@unittest.skipIf(cv2 is None, "opencv is the optional [sensor] extra")
class RecordingMeasurementTests(unittest.TestCase):
    def test_presses_grid_and_patch_are_recovered(self):
        base = unloaded_frame()
        frames = [base] * 15
        frames += [pressed(base, (180, 130), r) for r in (20, 35, 50, 50, 35)]
        frames += [base] * 10
        frames += [pressed(base, (120, 100), 30)] * 6 + [base] * 5
        frames += [pressed(base, (240, 60), 30)] * 3 + [base] * 5  # a blip, not a press
        with tempfile.TemporaryDirectory() as tmp:
            recording = mini.Recording(tmp, {}, (320, 240))
            for k, image in enumerate(frames):
                recording.add(mini.Frame(image, None, 1_000_000_000 + k * 53_000_000, k))
            report = press.measure_recording(recording.close(), mm_per_px=0.0634)

        grid = report["grid"]
        np.testing.assert_allclose(grid["pitch_px"], [14.5, 14.6], atol=0.05)
        # Rows span 46..192 of 0..239 and columns 43..275 of 0..319: the equal
        # margins that center that extent.
        np.testing.assert_allclose(grid["margin_px_yx"], [46.5, 43.5], atol=0.1)
        first, second = report["presses"]
        self.assertEqual(first["frames"], [15, 19])
        self.assertIn(first["peak_frame"], (17, 18))
        np.testing.assert_allclose(first["center_px"], [180, 130], atol=1.0)
        # The blur spreads the disk edge; the 35%-of-peak patch stays close to it.
        self.assertAlmostEqual(first["patch_radius_px"], 50, delta=3)
        self.assertAlmostEqual(
            first["patch_radius_mm"], first["patch_radius_px"] * 0.0634
        )
        np.testing.assert_allclose(second["center_px"], [120, 100], atol=1.0)
        # Shading tracks the colour change's strength, not its area.
        self.assertGreater(first["shading"], 20)
        self.assertAlmostEqual(first["shading"], second["shading"], delta=8)


class DepthMatchingTests(unittest.TestCase):
    RUN = {
        "frames": [
            {"frame": 0, "depth_m": -1e-4, "normal_force_n": 0.0, "shading": 0.0},
            {
                "frame": 1,
                "depth_m": 3e-4,
                "normal_force_n": 0.1,
                "patch_radius_px": 30.0,
                "shading": 10.0,
            },
            {
                "frame": 2,
                "depth_m": 6e-4,
                "normal_force_n": 0.3,
                "patch_radius_px": 40.0,
                "shading": 20.0,
            },
            {
                "frame": 3,
                "depth_m": 9e-4,
                "normal_force_n": 0.7,
                "patch_radius_px": 50.0,
                "shading": 40.0,
            },
        ]
    }

    def test_interpolates_between_bracketing_frames(self):
        depth, force = press.depth_for_radius(self.RUN, 45.0)
        self.assertAlmostEqual(depth, 7.5e-4)
        self.assertAlmostEqual(force, 0.5)

    def test_does_not_extrapolate_past_the_ramp(self):
        self.assertIsNone(press.depth_for_radius(self.RUN, 60.0))
        self.assertIsNone(press.depth_for_radius(self.RUN, 10.0))

    def test_shading_matches_and_reads_back_along_the_ramp(self):
        depth, force = press.depth_for(self.RUN, "shading", 30.0)
        self.assertAlmostEqual(depth, 7.5e-4)
        self.assertAlmostEqual(force, 0.5)
        # The unloaded frame (depth <= 0) is not part of the ramp.
        self.assertIsNone(press.depth_for(self.RUN, "shading", 5.0))
        self.assertAlmostEqual(press.shading_at(self.RUN, 7.5e-4), 30.0)
        self.assertIsNone(press.shading_at(self.RUN, 1e-3))


if __name__ == "__main__":
    unittest.main()
