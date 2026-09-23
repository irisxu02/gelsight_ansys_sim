"""GelSight Mini capture processing, checked without a camera.

A synthetic raw frame - dark dots on a distorted lattice, JPEG encoded as the
Mini sends it - stands in for the sensor, so framing, decoding and recording
are exercised exactly as the capture thread runs them.
"""

import json
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

from gelsight_ansys.sensor import mini
from gelsight_ansys.sensor.stream import grid_spec

ROWS, COLS, MARGIN = 11, 17, (10.0, 10.0)


def reference_resize_crop(img, imgw, imgh, fraction):
    """The SDK's resize_crop as written: gs_sdk uses 1/25, gs-marker-utils 1/7."""
    border_size_x, border_size_y = (
        int(img.shape[0] * fraction),
        int(np.floor(img.shape[1] * fraction)),
    )
    cropped_imgh = img.shape[0] - 2 * border_size_x
    cropped_imgw = img.shape[1] - 2 * border_size_y
    extra_border_h = 0
    extra_border_w = 0
    if cropped_imgh * imgw / imgh > cropped_imgw + 1e-8:
        extra_border_h = int(cropped_imgh - cropped_imgw * imgh / imgw)
    elif cropped_imgh * imgw / imgh < cropped_imgw - 1e-8:
        extra_border_w = int(cropped_imgw - cropped_imgh * imgw / imgh)
    img = img[
        border_size_x + extra_border_h : img.shape[0] - border_size_x,
        border_size_y + extra_border_w : img.shape[1] - border_size_y,
    ]
    return cv2.resize(img, (imgw, imgh))


def distorted_lattice(raw_size=mini.RAW_SIZE):
    """Marker centers with rotation, keystone and barrel distortion, in raw px."""
    width, height = raw_size
    i, j = np.meshgrid(np.linspace(-1, 1, ROWS), np.linspace(-1, 1, COLS), indexing="ij")
    x, y = 1130 * j, 720 * i
    angle = np.radians(-1.2)
    x, y = x * np.cos(angle) - y * np.sin(angle), x * np.sin(angle) + y * np.cos(angle)
    x = x * (1 + 0.03 * i)  # keystone: wider at the bottom
    r2 = (x / width) ** 2 + (y / height) ** 2
    x, y = x * (1 - 0.15 * r2), y * (1 - 0.15 * r2)  # barrel
    return np.stack([x + width / 2 + 60, y + height / 2 - 20], axis=-1)


def synthetic_jpeg(centers, raw_size=mini.RAW_SIZE):
    width, height = raw_size
    yy, xx = np.mgrid[0:height, 0:width]
    shading = 150 + 40 * np.exp(
        -(((xx - width / 2) / 1400) ** 2 + ((yy - height / 2) / 1100) ** 2)
    )
    image = np.repeat(shading[..., None], 3, axis=2).astype(np.uint8)
    image[..., 0] = np.uint8(0.6 * shading)  # a green-blue cast, as the Mini's
    for x, y in centers.reshape(-1, 2):
        cv2.circle(
            image,
            (int(round(x * 16)), int(round(y * 16))),
            26 * 16,
            (40, 45, 40),
            -1,
            cv2.LINE_AA,
            4,
        )
    ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
    assert ok
    return jpeg.tobytes()


class CropTests(unittest.TestCase):
    def test_full_keeps_the_whole_sensor(self):
        self.assertEqual(mini.crop_box("full"), (0, 0, 3280, 2464))

    def test_gs_sdk_crop_is_the_data_collections(self):
        # 1/25 borders of 98 rows and 131 columns, then 4 more rows for 4:3.
        self.assertEqual(mini.crop_box("gs_sdk"), (131, 102, 3149, 2366))

    def test_gsrobotics_matches_resize_crop_mini(self):
        # 1/7 borders of 352 and 468 px, then 2 more rows for a 4:3 output.
        self.assertEqual(mini.crop_box("gsrobotics"), (468, 354, 2812, 2112))

    def test_explicit_box_is_validated(self):
        self.assertEqual(mini.crop_box("10,20,330,260"), (10, 20, 330, 260))
        with self.assertRaises(ValueError):
            mini.crop_box("0,0,4000,100")
        with self.assertRaises(ValueError):
            mini.crop_box("middle")

    def test_grid_specification(self):
        self.assertEqual(mini.parse_grid("grid:11x17"), (11, 17, (10.0, 10.0)))
        self.assertEqual(mini.parse_grid("grid:7x9:24"), (7, 9, (24.0, 24.0)))
        self.assertEqual(mini.parse_grid("grid:7x9:20,24"), (7, 9, (20.0, 24.0)))
        with self.assertRaises(ValueError):
            mini.parse_grid("grid:11")

    def test_bare_grid_reads_the_sim_runs_marker_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            self.assertEqual(grid_spec("grid", run), "grid:11x17:10")
            optics = {"marker_grid_rows_cols": [7, 9], "marker_margin_px": [24.0, 24.0]}
            (run / "config.json").write_text(json.dumps({"optics": optics}))
            self.assertEqual(grid_spec("grid", run), "grid:7x9:24,24")
            self.assertEqual(grid_spec("full", run), "full")

    def test_sim_marker_pixels_match_the_renderer(self):
        from types import SimpleNamespace

        from gelsight_ansys.camera import reference_marker_pixels

        camera = SimpleNamespace(width_px=320, height_px=240)
        optics = SimpleNamespace(
            marker_grid_rows_cols=(ROWS, COLS), marker_margin_px=MARGIN
        )
        np.testing.assert_allclose(
            mini.sim_marker_pixels(ROWS, COLS, MARGIN),
            reference_marker_pixels(camera, optics),
        )


class GridOrderTests(unittest.TestCase):
    def test_isolated_detections_are_dropped_and_the_rest_ordered(self):
        lattice = distorted_lattice()
        shuffled = np.random.default_rng(0).permutation(lattice.reshape(-1, 2))
        strays = np.array([[15.0, 20.0], [3250.0, 2440.0]])  # dark sensor corners
        ordered = mini.order_grid(np.vstack([shuffled, strays]), ROWS, COLS)
        np.testing.assert_allclose(ordered, lattice.reshape(-1, 2))

    def test_a_missing_marker_is_reported(self):
        with self.assertRaisesRegex(ValueError, "found 186 markers"):
            mini.order_grid(distorted_lattice().reshape(-1, 2)[1:], ROWS, COLS)


@unittest.skipIf(cv2 is None, "opencv is the optional [sensor] extra")
class FramingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lattice = distorted_lattice()
        cls.jpeg = synthetic_jpeg(cls.lattice)

    def test_markers_are_found_in_a_reduced_decode(self):
        points = mini.detect_markers(mini.decode_jpeg(self.jpeg, 4))
        ordered = mini.order_grid(points, ROWS, COLS)
        error = np.linalg.norm(ordered - self.lattice.reshape(-1, 2), axis=1)
        self.assertLess(error.max(), 4.0)  # raw px, from a 1/4-size decode

    def test_lattice_remap_puts_markers_on_the_sim_grid(self):
        points = mini.detect_markers(mini.decode_jpeg(self.jpeg, 4))
        framing = mini.Framing.from_markers(points, ROWS, COLS, MARGIN)
        # The distortion is more than a homography can absorb ...
        self.assertGreater(framing.grid["homography_rms_residual_px"], 0.5)
        self.assertEqual(framing.reduction, 8)
        rgb = framing.apply(mini.decode_jpeg(self.jpeg, framing.reduction))
        self.assertEqual(rgb.shape, (240, 320, 3))  # BGR, like every output
        # ... but the lattice remap lands every marker where the simulator draws it.
        found = mini.order_grid(mini.detect_markers(rgb, (320, 240)), ROWS, COLS)
        error = np.linalg.norm(found - mini.sim_marker_pixels(ROWS, COLS, MARGIN), axis=1)
        self.assertLess(np.sqrt(np.mean(error**2)), 0.35)
        self.assertLess(error.max(), 0.8)

    def test_sdk_crops_reproduce_resize_crop_pixel_for_pixel(self):
        raw = mini.decode_jpeg(self.jpeg, 1)
        for spec, fraction in (("gs_sdk", 1 / 25), ("gsrobotics", 1 / 7)):
            framing = mini.Framing.crop(spec)
            self.assertEqual(framing.reduction, 1)
            np.testing.assert_array_equal(
                framing.apply(mini.decode_jpeg(self.jpeg, framing.reduction)),
                reference_resize_crop(raw, 320, 240, fraction),
                err_msg=spec,
            )

    def test_output_stays_bgr(self):
        bgr = mini.Framing.crop("gs_sdk").apply(mini.decode_jpeg(self.jpeg, 1))
        background = bgr[5, 160]
        self.assertLess(background[0], background[1])  # blue was dimmed in slot 0


class DifferenceTests(unittest.TestCase):
    def test_signed_difference_is_gray_at_zero_and_clips(self):
        reference = np.full((2, 2, 3), 100, np.uint8)
        frame = reference.copy()
        frame[0, 0] = 255
        frame[1, 1] = 0
        out = mini.signed_difference(frame, reference, gain=0.5)
        self.assertEqual(out[0, 1, 0], 128)
        self.assertEqual(out[0, 0, 0], 206)  # 128 + 155 / 2, rounded
        self.assertEqual(out[1, 1, 0], 78)
        self.assertEqual(mini.signed_difference(frame, reference, gain=4)[0, 0, 0], 255)


@unittest.skipIf(cv2 is None, "opencv is the optional [sensor] extra")
class RecordingTests(unittest.TestCase):
    def test_round_trip_in_the_trial_gs_npz_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = mini.Recording(
                tmp, {"kind": "test"}, (4, 3), label="press 1/a", save_raw=True
            )
            self.assertEqual(recording.path.name.split("_", 1)[1], "press_1_a")
            frames = np.random.default_rng(1).integers(
                0, 255, (5, 3, 4, 3), dtype=np.uint8
            )
            devices = [0, 1, 2, 4, 5]  # one frame lost between 2 and 4
            for k, (image, index) in enumerate(zip(frames, devices, strict=True)):
                recording.add(
                    mini.Frame(image, b"jpeg", 1_000_000_000 + k * 50_000_000, index)
                )
            recording.save_reference(frames[0], 1)
            path = recording.close()

            # The keys fast_stream_device.py writes, plus the camera's frame count.
            with np.load(path / "gs.npz", allow_pickle=False) as data:
                self.assertEqual(
                    sorted(data.files),
                    ["device_frame_idx", "frame_idx", "frames", "t_ns", "timestamps"],
                )
                np.testing.assert_array_equal(data["frame_idx"], np.arange(5))
                np.testing.assert_array_equal(data["timestamps"], data["t_ns"])
                np.testing.assert_array_equal(data["device_frame_idx"], devices)
                self.assertEqual(data["t_ns"].dtype, np.int64)

            loaded = mini.load_recording(path)
            np.testing.assert_array_equal(loaded["frames"], frames)
            np.testing.assert_allclose(loaded["t_s"], np.arange(5) * 0.05)
            np.testing.assert_array_equal(loaded["reference"], frames[0])
            meta = loaded["meta"]
            self.assertEqual(meta["frame_count"], 5)
            self.assertEqual(meta["dropped_frames"], 1)
            self.assertAlmostEqual(meta["mean_fps"], 20.0)
            self.assertEqual(len(list((path / "raw").glob("*.jpg"))), 5)
            self.assertFalse((path / "frames.partial").exists())

    def test_an_empty_recording_still_closes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = mini.Recording(tmp, {}, (4, 3)).close()
            self.assertEqual(mini.load_recording(path)["frames"].shape, (0, 3, 4, 3))


class DeviceTests(unittest.TestCase):
    def test_only_gelsight_capture_nodes_are_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            sysfs = Path(tmp)
            for node, name, index in [
                ("video0", "Integrated Webcam", 0),
                ("video2", "GelSight Mini R0B 2D8Y-69GR: Ge", 0),
                ("video3", "GelSight Mini R0B 2D8Y-69GR: Ge", 1),
                ("video10", "GelSight Mini R0B 3XXX: Ge", 0),
            ]:
                (sysfs / node).mkdir()
                (sysfs / node / "name").write_text(name + "\n")
                (sysfs / node / "index").write_text(f"{index}\n")
            found = mini.find_devices(sysfs)
            self.assertEqual([path for path, _ in found], ["/dev/video2", "/dev/video10"])


if __name__ == "__main__":
    unittest.main()
